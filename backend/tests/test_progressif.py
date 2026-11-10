"""Le petit budget ne doit ni forcer un achat ni masquer ses coûts."""
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path
from datetime import timedelta
from unittest.mock import AsyncMock
import uuid

import pytest

from app.core.settings import load_settings
from app.engine.costs import entry_cost, exit_proceeds
from app.engine.exit_rule import advance_profit_stop
from app.engine.risk_gate import RiskConfig
from app.backtest.engine import _simulate_exit, _record_trade_curve, _max_drawdown
from app.execution.paper import PaperBroker
from tests.test_risk_gate import run, market, account, CONSTRAINTS


def progressive(**changes):
    base = load_settings(use_environment=False, overrides={
        "RISK_PROFILE": "progressif", "EXCHANGE_ID": "kraken",
    }).risk
    return replace(base, **changes)


def test_profile_is_paper_only_and_keeps_a_small_risk():
    cfg = progressive()
    assert cfg.risk_per_trade_pct == D('.005')
    assert not cfg.small_account_full_allocation
    assert cfg.take_profit_r_multiple == 0
    with pytest.raises(RuntimeError, match="réservé"):
        load_settings(use_environment=False, overrides={
            "RISK_PROFILE": "progressif", "TRADING_MODE": "live"})


def test_budget_includes_both_fees_and_slippage():
    cfg = progressive()
    decision = run(cfg=cfg)
    assert decision.approved, decision.rejection_details
    entry = PaperBroker(cfg.execution_fee_pct, 5).entry(decision, D(100))
    exit_fill = PaperBroker(cfg.execution_fee_pct, 5).exit(entry.amount, decision.stop_loss_price)
    loss = entry.notional_quote + entry.fee_quote - exit_fill.notional_quote + exit_fill.fee_quote
    assert loss == decision.estimated_total_loss_quote
    assert loss <= D(50)  # 0,5 % de 10 000
    assert decision.effective_risk_quote < loss


def test_costs_can_reject_a_gross_reward_that_looks_profitable():
    d = run(m=market(atr='0.2'), cfg=progressive())
    assert not d.approved
    assert "coûts disproportionnés" in d.rejection_details


def test_small_budget_does_not_raise_risk_to_meet_exchange_minimum():
    d = run(a=account(equity_quote=D('9.37'), free_quote=D('9.37')), cfg=progressive())
    assert not d.approved
    assert d.amount is None


def test_live_swing_does_not_raise_risk_to_fund_an_exchange_minimum():
    from tests.test_risk_gate import swing_market
    from app.engine.risk_gate import ExchangeConstraints

    live = load_settings(use_environment=False, overrides={
        "TRADING_MODE": "live", "EXCHANGE_SANDBOX_MODE": "false",
        "I_UNDERSTAND_REAL_MONEY": "true", "EXCHANGE_API_KEY": "key",
        "EXCHANGE_SECRET": "secret", "RISK_PROFILE": "swing",
        "EXCHANGE_ID": "kraken", "MIN_NET_REWARD_TO_RISK": "0.01",
    }).risk
    limits = ExchangeConstraints(amount_step=D("0.001"), min_amount=D("0.001"),
                                 min_notional=D("15"), price_tick=D("0.01"))
    decision = run(m=swing_market(), a=account(equity_quote=D(20), free_quote=D(20)),
                   c=limits, cfg=live)
    assert not decision.approved
    assert "plafond petit compte" in decision.rejection_details


def stop_at(close, current=D(95), **changes):
    return advance_profit_stop(entry_price=D(100), initial_distance=D(5),
                               current_stop=current, close=D(close), price_tick=D('.1'),
                               config=progressive(**changes))


def test_trailing_waits_for_a_net_gain_and_never_moves_down():
    assert stop_at('110') == D(95)  # +2R brut, pas encore net
    raised = stop_at('115')
    assert raised == D(110)
    assert stop_at('112', current=raised) == raised


def test_large_winner_is_not_capped_by_a_take_profit():
    assert stop_at('150') == D(145)
    assert progressive().take_profit_r_multiple == 0


def test_trailing_floor_really_covers_fees_after_rounding():
    cfg = progressive(profit_trailing_distance_r=D('1.9'))
    level = advance_profit_stop(entry_price=D(100), initial_distance=D(5),
        current_stop=D(95), close=D(112), price_tick=D(1), config=cfg)
    assert exit_proceeds(level, cfg.execution_fee_pct, cfg.execution_slippage_pct) >= D(100) * (1 + cfg.execution_fee_pct)


def test_new_stop_is_not_applied_to_the_candle_used_to_compute_it():
    decision = run(cfg=progressive())
    candles = [[0,100,101,99,100,1], [14400000,100,116,96,115,1],
               [28800000,115,116,109,112,1]]
    j, fill, reason = _simulate_exit(candles,0,decision,PaperBroker(D('.008'),0),
                                      config=progressive(),price_tick=D('.01'))
    assert j == 2
    assert reason == 'STOP_LOSS'
    assert fill.price == D(110)


def test_drawdown_includes_a_temporary_loss_before_recovery():
    broker = PaperBroker(D(0),0)
    d = run()
    entry = broker.entry(d,D(100))
    candles = [[0,100,100,100,100,1],[14400000,100,100,80,80,1],
               [28800000,80,110,80,110,1]]
    curve = []
    _record_trade_curve(curve,candles,0,2,D(1000),entry,broker.exit(entry.amount,D(110)),broker)
    assert _max_drawdown(curve) == D('.2')


def test_frozen_settings_ignore_local_environment(monkeypatch):
    monkeypatch.setenv('RISK_PROFILE','aggressive')
    monkeypatch.setenv('PAPER_START_EQUITY','999999')
    settings = load_settings(use_environment=False, overrides={'PAPER_START_EQUITY':'9.37'})
    assert settings.risk_profile == 'prudent'
    assert settings.paper_start_equity == D('9.37')


@pytest.mark.asyncio
async def test_budget_comparison_reports_the_mechanical_ledger():
    from scripts.compare_budgets import compare
    fixture = Path(__file__).parent / 'fixtures/backtest_golden.json'
    rows = await compare(fixture,[D('1000')],['micro'])
    assert rows[0]['trades'] > 0
    assert D(rows[0]['net_pnl']) < 0
    assert D(rows[0]['equity_end']) == D(1000) + D(rows[0]['net_pnl'])


@pytest.mark.asyncio
async def test_runtime_raises_the_same_stop_as_the_replay(monkeypatch):
    from tests.test_orchestrator import FakeRepo, FakeLLM, llm_signal, symbol_state, NOW
    from app.db.repository import PositionRow
    from app.runtime import orchestrator as module
    from app.engine.machine import StateMachine, IN_POSITION
    pos = PositionRow(id=uuid.uuid4(),symbol='BTC/EUR',entry_price=D(100),
        amount=D('.05'),stop_loss_price=D(95),take_profit_price=None,
        opened_at=NOW-timedelta(hours=1),risk_amount_quote=D('.25'),
        monitor_cursor_at=NOW-timedelta(hours=4))
    repo = FakeRepo(state=IN_POSITION,position=pos)
    repo.advance_paper_position = AsyncMock(wraps=repo.advance_paper_position)
    settings = load_settings(use_environment=False, overrides={
        'RISK_PROFILE':'progressif','EXCHANGE_ID':'kraken','PAPER_START_EQUITY':'9.37'})
    monkeypatch.setattr(module,'build_snapshot',lambda *a,**kw:symbol_state(price='115'))
    monkeypatch.setattr(module,'closed_candles',lambda *a,**kw:[[int((NOW-timedelta(hours=4)).timestamp()*1000),100,116,96,115,1]])
    monkeypatch.setattr(module,'last_price',lambda *a,**kw:D(115))
    bot = module.Orchestrator(settings,repo,object(),FakeLLM(llm_signal('HOLD')),
                               PaperBroker(D('.008'),5),now=lambda:NOW)
    report = module.CycleReport('PAPER',IN_POSITION,IN_POSITION)
    await bot._monitor(StateMachine(repo,'PAPER',IN_POSITION),pos,repo.account_snapshot,report)
    assert repo.position.stop_loss_price == D(110)
    repo.advance_paper_position.assert_awaited_once()
    assert repo.closed is None
    await bot._monitor(StateMachine(repo,'PAPER',IN_POSITION),
                       repo.position,repo.account_snapshot,report)
    assert repo.closed is None  # Ne pas rejouer le low=96 contre le nouveau stop.


@pytest.mark.asyncio
async def test_runtime_revalidates_sizing_at_the_price_used_for_paper_fill(monkeypatch):
    from tests.test_orchestrator import FakeRepo, FakeLLM, llm_signal, symbol_state, NOW
    from app.runtime import orchestrator as module
    repo = FakeRepo(free_quote='1000')
    settings = load_settings(use_environment=False, overrides={
        'RISK_PROFILE':'progressif','EXCHANGE_ID':'kraken','TRADING_SYMBOL':'BTC/EUR',
        'PAPER_START_EQUITY':'1000'})
    prices = iter([D(100),D(120)])
    monkeypatch.setattr(module,'build_snapshot',lambda *a,**kw:symbol_state())
    monkeypatch.setattr(module,'last_price',lambda *a,**kw:next(prices))
    bot = module.Orchestrator(settings,repo,object(),FakeLLM(llm_signal('BUY')),
                               PaperBroker(D('.008'),5),now=lambda:NOW)
    report = await bot.run_once()
    assert report.position_opened is not None
    assert len(repo.evaluations) == 2
    assert repo.position.entry_price == D('120.0600')
    assert repo.evaluations[-1]['decision'].estimated_total_loss_quote <= D(5)
