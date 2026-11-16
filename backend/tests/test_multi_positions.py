"""Portefeuille PAPER : positions simultanées, risque et cash réellement partagés."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D

import pytest

from app.api.service import build_status
from app.engine.costs import planned_loss
from app.engine.capital import start_capital
from app.engine.machine import COOLDOWN, EXECUTING, HALT, IDLE, IN_POSITION, StateMachine
from app.execution.paper import Fill
from app.engine.risk_gate import RejectionCode, RiskConfig, evaluate
from app.market.feed import FeedError
from app.runtime import orchestrator as runtime
from app.runtime import recovery
from app.runtime.orchestrator import CycleReport
from app.runtime.recovery import PanicLiquidationError, liquidate_open_positions
from tests.test_orchestrator import (
    FakeRepo, FakeLLM, NOW, flat_candle, llm_signal, open_position,
    orchestrator, settings, symbol_state,
)
from tests.test_risk_gate import account, intent, market, CONSTRAINTS


class PortfolioRepo(FakeRepo):
    def __init__(self, positions=(), cash='1000', state=None):
        super().__init__(state=state or (IN_POSITION if positions else IDLE), free_quote=cash)
        self.positions = list(positions)
        self.closed_ids = []
        self.account_snapshot = replace(self.account_snapshot,
            equity=D(cash) + sum((p.amount * p.entry_price for p in positions), D(0)),
            has_open_position=bool(positions))

    async def open_positions(self, mode):
        return list(self.positions)

    async def open_position(self, mode):
        return self.positions[0] if self.positions else None

    async def snapshot_equity(self, mode, **values):
        await super().snapshot_equity(mode, **values)
        self.account_snapshot = replace(self.account_snapshot, equity=values['equity'],
            free_quote=values['free_quote'], has_open_position=bool(self.positions),
            reserve_quote=self.capital.reserve_quote if self.capital else D(0),
            growth_quote=self.capital.growth_quote if self.capital else D(0))

    async def create_position(self, mode, **values):
        ident = await super().create_position(mode, **values)
        self.positions.append(self.position)
        return ident

    async def close_position(self, ident, **values):
        self.closed_ids.append(ident)
        self.closed = values
        self.positions = [p for p in self.positions if p.id != ident]

    async def advance_paper_position(self, position, stop, close_at):
        self.positions = [replace(p, stop_loss_price=stop, monitor_cursor_at=close_at)
                          if p.id == position.id else p for p in self.positions]

    async def update_after_close(self, mode, **values):
        await super().update_after_close(mode, **values)
        self.account_snapshot = replace(self.account_snapshot,
            consecutive_losses=values['consecutive_losses'])

    async def recent(self, *args):
        return []


def config(tmp_path, **overrides):
    values = dict(MAX_OPEN_POSITIONS='3', RISK_PROFILE='micro_budget',
        TRADING_SYMBOLS='BTC/EUR,ETH/EUR,SOL/EUR', PAPER_START_EQUITY='1000',
        MAX_POSITION_NOTIONAL_QUOTE='100', MAX_ORDERS_PER_HOUR='20')
    values.update(overrides)
    return settings(tmp_path, **values)


@pytest.fixture(autouse=True)
def market_data(monkeypatch):
    def snapshot(exchange, symbol, **kwargs):
        st = symbol_state()
        return replace(st, symbol=symbol, info=replace(st.info, symbol=symbol),
                       snapshot=replace(st.snapshot, symbol=symbol))
    monkeypatch.setattr(runtime, 'build_snapshot', snapshot)
    monkeypatch.setattr(runtime, 'last_price', lambda exchange, symbol: D('100'))
    monkeypatch.setattr(runtime, 'closed_candles', lambda *args, **kwargs: [flat_candle()])


@pytest.mark.asyncio
async def test_three_entries_keep_every_position_and_shared_cash(tmp_path, monkeypatch):
    repo, llm = PortfolioRepo(), FakeLLM(llm_signal())
    engine = orchestrator(tmp_path, repo, llm, config(tmp_path))
    clock = [NOW]
    original = runtime.build_snapshot
    def next_candle(*args, **kwargs):
        return replace(original(*args, **kwargs), last_close_at=clock[0])
    monkeypatch.setattr(runtime, 'build_snapshot', next_candle)
    engine._now = lambda: clock[0]
    reports = []
    for _ in range(3):
        reports.append(await engine.run_once())
        clock[0] += timedelta(hours=4)
    assert [p.symbol for p in repo.positions] == ['BTC/EUR', 'ETH/EUR', 'SOL/EUR'], reports
    assert repo.current_state == IN_POSITION
    assert len(repo.orders) == 6
    spent = sum((o['filled_amount'] * o['average_fill_price'] + o['fee_amount']
                 for o in repo.orders if o['side'] == 'BUY'), D(0))
    assert repo.account_snapshot.free_quote == D('1000') - spent
    assert repo.snapshots[-1]['position_value_quote'] == sum((p.amount * D(100) for p in repo.positions), D(0))
    assert repo.account_snapshot.equity == repo.account_snapshot.free_quote + repo.snapshots[-1]['position_value_quote']
    await engine.run_once()
    assert len(repo.positions) == 3
    assert len([o for o in repo.orders if o['side'] == 'BUY']) == 3


@pytest.mark.asyncio
async def test_exits_are_all_booked_without_erasing_remaining_value(tmp_path, monkeypatch):
    btc = open_position()
    eth = replace(open_position(), symbol='ETH/EUR')
    repo = PortfolioRepo([btc, eth], cash='100')
    monkeypatch.setattr(runtime, 'closed_candles', lambda *a, **k:
                        [[(NOW-timedelta(hours=4)).timestamp()*1000, '100','101','90','95','1']])
    report = await orchestrator(tmp_path, repo, FakeLLM(llm_signal('HOLD')), config(tmp_path)).run_once()
    assert repo.closed_ids == [btc.id, eth.id]
    assert repo.snapshots[0]['position_value_quote'] == eth.amount * D(100)
    assert repo.snapshots[-1]['position_value_quote'] == 0
    assert repo.current_state == COOLDOWN
    assert repo.account_snapshot.consecutive_losses == 2
    assert len([o for o in repo.orders if o['side'] == 'SELL']) == 2
    assert report.signals == 0


@pytest.mark.asyncio
async def test_closed_trade_keeps_monitoring_sibling_during_cooldown(tmp_path, monkeypatch):
    btc = open_position()
    eth = replace(open_position(), symbol='ETH/EUR', stop_loss_price=D(80))
    repo = PortfolioRepo([btc, eth], cash='100')
    monkeypatch.setattr(runtime, 'closed_candles', lambda *a, **k:
                        [[(NOW-timedelta(hours=4)).timestamp()*1000, '100','101','90','95','1']])
    llm = FakeLLM(llm_signal('HOLD'))
    await orchestrator(tmp_path, repo, llm, config(tmp_path)).run_once()
    assert [p.symbol for p in repo.positions] == ['ETH/EUR']
    assert repo.positions[0].monitor_cursor_at == NOW
    assert repo.current_state == IN_POSITION
    assert repo.cooldown > NOW
    assert len(repo.orders) == 1


@pytest.mark.asyncio
async def test_missing_quote_does_not_prevent_other_stop_exit(tmp_path, monkeypatch):
    btc, eth = open_position(), replace(open_position(), symbol='ETH/EUR')
    repo = PortfolioRepo([btc, eth], cash='100')
    original = runtime.build_snapshot
    def data(exchange, symbol, **kw):
        if symbol == 'BTC/EUR':
            raise FeedError('indisponible')
        return original(exchange, symbol, **kw)
    def price(exchange, symbol):
        if symbol == 'BTC/EUR':
            raise FeedError('cours indisponible')
        return D(100)
    monkeypatch.setattr(runtime, 'build_snapshot', data)
    monkeypatch.setattr(runtime, 'last_price', price)
    monkeypatch.setattr(runtime, 'closed_candles', lambda *a, **k:
                        [[(NOW-timedelta(hours=4)).timestamp()*1000, '100','101','90','95','1']])
    report = await orchestrator(tmp_path, repo, FakeLLM(llm_signal()), config(tmp_path)).run_once()
    assert repo.closed_ids == [eth.id]
    assert len(repo.positions) == 1
    assert report.errors
    assert all(o['side'] == 'SELL' for o in repo.orders)


@pytest.mark.asyncio
async def test_halt_preserves_stop_management_for_all_other_positions(tmp_path, monkeypatch):
    btc, eth = open_position(), replace(open_position(), symbol='ETH/EUR')
    repo = PortfolioRepo([btc, eth], cash='100', state=HALT)
    monkeypatch.setattr(runtime, 'closed_candles', lambda *a, **k:
                        [[(NOW-timedelta(hours=4)).timestamp()*1000, '100','101','90','95','1']])
    llm = FakeLLM(llm_signal())
    await orchestrator(tmp_path, repo, llm, config(tmp_path)).run_once()
    assert not repo.positions
    assert repo.current_state == HALT
    assert llm.calls == 0


@pytest.mark.asyncio
async def test_model_halt_does_not_skip_a_sibling_stop(tmp_path, monkeypatch):
    btc, eth = open_position(), replace(open_position(), symbol='ETH/EUR')
    repo = PortfolioRepo([btc, eth], cash='100')
    def candles(exchange, symbol, *a):
        return [flat_candle()] if symbol == 'BTC/EUR' else [
            [(NOW-timedelta(hours=4)).timestamp()*1000, '100','101','90','95','1']]
    monkeypatch.setattr(runtime, 'closed_candles', candles)
    await orchestrator(tmp_path, repo, FakeLLM(llm_signal('HALT')), config(tmp_path)).run_once()
    assert repo.closed_ids == [btc.id, eth.id]
    assert repo.current_state == HALT


@pytest.mark.asyncio
async def test_progressive_capital_does_not_make_invested_cash_available_again(tmp_path):
    btc = replace(open_position(), amount=D(1))
    eth = replace(open_position(), symbol='ETH/EUR', amount=D(1))
    repo = PortfolioRepo([btc, eth], cash='799.48')
    repo.capital = start_capital(D(1000), NOW)
    cfg = config(tmp_path, RISK_PROFILE='progressif')
    engine = orchestrator(tmp_path, repo, FakeLLM(llm_signal('HOLD')), cfg)
    machine, report = StateMachine(repo, 'PAPER', IN_POSITION), CycleReport('PAPER', IN_POSITION, IN_POSITION)
    for position, price in [(btc, D(110)), (eth, D(95))]:
        fee = price * D('.0026')
        await engine._close_trade(machine, position, repo.account_snapshot, price,
            'TEST', report, purpose='LLM_EXIT', fill=Fill(price, D(1), price, fee, D(0)))
        if position.id == btc.id:
            assert repo.account_snapshot.free_quote == D('899.7400000000')
            assert repo.snapshots[-1]['position_value_quote'] == D(100)
            assert repo.account_snapshot.equity == repo.capital.total - D('.26')
    assert repo.account_snapshot.free_quote == repo.capital.active_quote
    assert repo.account_snapshot.equity == repo.capital.total


@pytest.mark.asyncio
async def test_status_exposes_every_position(tmp_path):
    positions = [open_position(), replace(open_position(), symbol='ETH/EUR')]
    body = await build_status(PortfolioRepo(positions), config(tmp_path))
    assert [p['symbol'] for p in body['positions']] == ['BTC/EUR', 'ETH/EUR']
    assert body['position'] == body['positions'][0]


@pytest.mark.asyncio
async def test_lowered_slot_limit_keeps_all_existing_positions_and_global_marks(tmp_path):
    btc, eth = open_position(), replace(open_position(), symbol='ETH/EUR')
    repo = PortfolioRepo([btc, eth], cash='100')
    engine = orchestrator(tmp_path, repo, FakeLLM(llm_signal('HOLD')),
                          config(tmp_path, MAX_OPEN_POSITIONS='1'))
    await engine._close_trade(StateMachine(repo, 'PAPER', IN_POSITION), btc,
        repo.account_snapshot, D(95), 'STOP_LOSS', CycleReport('PAPER', IN_POSITION, IN_POSITION),
        purpose='STOP_LOSS', fill=Fill(D(95), btc.amount, D(95)*btc.amount, D(0), D(0)))
    assert [p.symbol for p in repo.positions] == ['ETH/EUR']
    assert repo.snapshots[-1]['position_value_quote'] == eth.amount * D(100)
    assert repo.snapshots[-1]['symbol'] is None


@pytest.mark.asyncio
async def test_panic_closes_all_positions_and_preserves_intermediate_equity(tmp_path, monkeypatch):
    positions = [open_position(), replace(open_position(), symbol='ETH/EUR')]
    repo = PortfolioRepo(positions, cash='100')
    monkeypatch.setattr(recovery, 'last_price', lambda *a: D(90))
    repo.open_orders = lambda mode: _orders()
    engine = orchestrator(tmp_path, repo, FakeLLM(llm_signal()), config(tmp_path))
    results = await liquidate_open_positions(repo, object(), engine._broker, 'PAPER',
        quote_currency='EUR', starting_equity=D(1000))
    assert len(results) == 2 and not repo.positions
    assert repo.snapshots[0]['position_value_quote'] == positions[1].amount * D(90)
    assert repo.snapshots[-1]['position_value_quote'] == 0
    assert repo.current_state == HALT
    assert repo.account_snapshot.consecutive_losses == 2


async def _orders():
    return []


@pytest.mark.asyncio
async def test_panic_continues_after_one_market_fails(tmp_path, monkeypatch):
    positions = [open_position(), replace(open_position(), symbol='ETH/EUR')]
    repo = PortfolioRepo(positions, cash='100')
    def quote(exchange, symbol):
        if symbol == 'BTC/EUR':
            raise FeedError('cours indisponible')
        return D(90)
    monkeypatch.setattr(recovery, 'last_price', quote)
    repo.open_orders = lambda mode: _orders()
    engine = orchestrator(tmp_path, repo, FakeLLM(llm_signal()), config(tmp_path))
    with pytest.raises(PanicLiquidationError) as failure:
        await liquidate_open_positions(repo, object(), engine._broker, 'PAPER',
            quote_currency='EUR', starting_equity=D(1000))
    assert [p.symbol for p in repo.positions] == ['BTC/EUR']
    assert len(failure.value.liquidated) == 1
    assert repo.current_state == HALT


def test_portfolio_risk_remaining_is_sized_net_of_fees():
    cfg = RiskConfig(max_open_positions=3, execution_fee_pct=D('.008'), execution_slippage_pct=D('.0005'), take_profit_r_multiple=D('3.5'))
    existing = D(40)
    a = account(open_position_symbols=('ETH/USDT',), open_risk_quote=existing)
    decision = evaluate(intent(), market(), a, CONSTRAINTS, cfg)
    assert decision.approved
    loss = decision.amount * planned_loss(market().price, decision.stop_loss_price,
                                         cfg.execution_fee_pct, cfg.execution_slippage_pct)
    assert 0 < loss <= D(10)
    assert existing + loss <= a.equity_quote * cfg.risk_per_trade_pct


def test_full_portfolio_risk_rejects_another_buy():
    a = account(open_position_symbols=('ETH/USDT',), open_risk_quote=D(50))
    decision = evaluate(intent(), market(), a, CONSTRAINTS, RiskConfig(max_open_positions=3))
    assert decision.rejection_code == RejectionCode.RISK_LIMIT_EXCEEDED


def test_loss_throttle_also_reduces_the_total_portfolio_budget():
    a = account(open_position_symbols=('ETH/USDT',), open_risk_quote=D(30), consecutive_losses=2)
    decision = evaluate(intent(), market(), a, CONSTRAINTS, RiskConfig(max_open_positions=3))
    assert decision.rejection_code == RejectionCode.RISK_LIMIT_EXCEEDED


@pytest.mark.asyncio
async def test_entry_refreshes_equity_before_sizing_another_position(tmp_path, monkeypatch):
    position = replace(open_position(), amount=D(2))
    repo = PortfolioRepo([position], cash='800')
    monkeypatch.setattr(runtime, 'last_price', lambda *a: D(50))
    engine = orchestrator(tmp_path, repo, FakeLLM(llm_signal()), config(tmp_path))
    current, state = await engine._entry_context(repo.account_snapshot)
    assert current.equity == state.equity_quote == D(900)
    assert state.open_notional_quote == D(100)
    assert repo.snapshots[-1]['source'] == 'PAPER_REFRESH'


@pytest.mark.asyncio
async def test_final_candidate_price_is_read_after_portfolio_refresh(tmp_path, monkeypatch):
    repo = PortfolioRepo([open_position()], cash='800', state=EXECUTING)
    engine = orchestrator(tmp_path, repo, FakeLLM(llm_signal()), config(tmp_path))
    clock, observed = [NOW], []
    engine._now = lambda: clock[0]
    engine._selected_confidence = D('.9')
    def quote(exchange, symbol):
        observed.append(symbol)
        if symbol == 'BTC/EUR':
            clock[0] += timedelta(seconds=8)
        return D(200) if clock[0] > NOW else D(100)
    monkeypatch.setattr(runtime, 'last_price', quote)
    st = runtime.build_snapshot(None, 'ETH/EUR')
    await engine._enter(StateMachine(repo, 'PAPER', EXECUTING), st, None,
        None, None, repo.account_snapshot, CycleReport('PAPER', EXECUTING, EXECUTING),
        signal_created_at=NOW)
    snapshot = repo.evaluations[0]['snapshot']
    assert observed.index('BTC/EUR') < observed.index('ETH/EUR')
    assert snapshot.price == D(200)
    assert snapshot.now == snapshot.tick_at == NOW + timedelta(seconds=8)


@pytest.mark.asyncio
async def test_final_valuation_failure_keeps_existing_positions_and_resting_state(tmp_path, monkeypatch):
    repo = PortfolioRepo([open_position()], cash='800', state=EXECUTING)
    engine = orchestrator(tmp_path, repo, FakeLLM(llm_signal()), config(tmp_path))
    engine._selected_confidence = D('.9')
    monkeypatch.setattr(runtime, 'last_price', lambda *a: (_ for _ in ()).throw(FeedError('panne')))
    report = CycleReport('PAPER', EXECUTING, EXECUTING)
    await engine._enter(StateMachine(repo, 'PAPER', EXECUTING), symbol_state(), None,
        None, None, repo.account_snapshot, report, signal_created_at=NOW)
    assert repo.current_state == IN_POSITION
    assert len(repo.positions) == 1 and not repo.orders
    assert report.errors


@pytest.mark.asyncio
@pytest.mark.parametrize('cursor_missing', [False, True])
async def test_broken_history_does_not_skip_another_position_stop(tmp_path, monkeypatch, cursor_missing):
    btc = replace(open_position(), monitor_cursor_at=None if cursor_missing else NOW-timedelta(hours=8))
    eth = replace(open_position(), symbol='ETH/EUR')
    repo = PortfolioRepo([btc, eth], cash='100')
    monkeypatch.setattr(runtime, 'closed_candles', lambda *a, **k:
                        [[(NOW-timedelta(hours=4)).timestamp()*1000, '100','101','90','95','1']])
    report = await orchestrator(tmp_path, repo, FakeLLM(llm_signal()), config(tmp_path)).run_once()
    assert repo.closed_ids == [eth.id]
    assert repo.positions[0].id == btc.id
    assert report.errors and report.signals == 0


def test_multi_position_configuration_reports_cost_aware_sizing(tmp_path):
    assert config(tmp_path, RISK_PROFILE='micro', COST_AWARE='false').risk.cost_aware


def test_portfolio_notional_limit_is_shared():
    a = account(open_position_symbols=('ETH/USDT',), open_notional_quote=D(1990))
    cfg = RiskConfig(max_open_positions=3)
    decision = evaluate(intent(), market(), a, CONSTRAINTS, cfg)
    assert decision.approved
    assert decision.notional_quote <= D(10)


@pytest.mark.parametrize('limit', [0, 6])
def test_position_count_is_bounded(tmp_path, limit):
    with pytest.raises(ValueError, match='max_open_positions'):
        config(tmp_path, **{'MAX_OPEN_POSITIONS': str(limit)})


def test_live_cannot_enable_multiple_positions(tmp_path):
    with pytest.raises(RuntimeError, match='PAPER'):
        settings(tmp_path, TRADING_MODE='live', MAX_OPEN_POSITIONS='3')
