"""Invariants de l'enveloppe et scénarios de pertes, reprise et promotion."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from random import Random
from unittest.mock import AsyncMock
import uuid

import pytest

from app.engine.capital import CapitalPolicy, start_capital, allocate_close
from app.core.settings import load_settings
from app.db.repository import PositionRow
from app.engine.machine import StateMachine, IN_POSITION, HALT
from app.execution.paper import PaperBroker, Fill
from app.execution.factory import make_broker
from app.runtime.orchestrator import Orchestrator, CycleReport
from app.runtime import orchestrator as module
from tests.test_orchestrator import FakeRepo, FakeLLM, llm_signal, symbol_state, NOW

POLICY = CapitalPolicy(enabled=True)


def config(**overrides):
    return load_settings(use_environment=False, overrides={
        'RISK_PROFILE':'progressif','EXCHANGE_ID':'kraken','TRADING_SYMBOL':'BTC/EUR',
        'PAPER_START_EQUITY':'9.37',**overrides})


def test_new_surplus_is_protected_immediately_and_step_is_small():
    initial = start_capital(D('9.37'), NOW)
    state = allocate_close(initial,D(2),NOW+timedelta(days=7),POLICY)
    assert state.active_quote == D('9.62')
    assert state.reserve_quote == D('.60')
    assert state.growth_quote == D('1.15')
    assert state.total == D('11.37')
    assert state.promotions == 1


def test_losses_and_recovery_never_distribute_the_same_profit_twice():
    state = allocate_close(start_capital(D(10),NOW),D(1),NOW,POLICY)
    state = allocate_close(state,D(-2),NOW,POLICY)
    assert state.reserve_quote == D('.3') and state.growth_quote == D('.7')
    state = allocate_close(state,D(2),NOW,POLICY)
    assert state.reserve_quote == D('.3') and state.growth_quote == D('.7')
    assert state.active_quote == 10 and state.total == 11
    assert state.promotions == 0


def test_a_winning_trade_below_the_global_high_does_not_create_a_reserve():
    state=start_capital(D(10),NOW)
    state=allocate_close(state,D(-2),NOW,POLICY)
    state=allocate_close(state,D(1),NOW,POLICY)
    assert state.active_quote == 9 and state.reserve_quote == 0 and state.growth_quote == 0
    assert state.active_drawdown == D('.1')


def test_promotion_cannot_repeat_during_the_week_or_after_a_loss():
    state=allocate_close(start_capital(D(10),NOW),D(10),NOW+timedelta(days=7),POLICY)
    assert state.promotions == 1
    state=allocate_close(state,D('.1'),NOW+timedelta(days=8),POLICY)
    assert state.promotions == 1
    state=allocate_close(state,D(-1),NOW+timedelta(days=14),POLICY)
    assert state.promotions == 1
    state=allocate_close(state,D('.5'),NOW+timedelta(days=14),POLICY)
    assert state.promotions == 1


def test_promotion_is_capped_and_no_loss_is_funded_by_the_pots():
    state=allocate_close(start_capital(D('19.9'),NOW),D(10),NOW+timedelta(days=7),POLICY)
    assert state.active_quote == 20
    state=allocate_close(state,D(-1),NOW+timedelta(days=14),POLICY)
    assert state.active_quote == 19
    with pytest.raises(ValueError,match='épuisé'):
        allocate_close(state,D(-20),NOW+timedelta(days=14),POLICY)


def test_conservation_and_monotone_reserve_over_many_mixed_closes():
    state=start_capital(D(100),NOW)
    random=Random(70217)
    pnl_sum=D(0)
    for i in range(1000):
        pnl=D(random.randint(-50,60))/100
        pnl_sum+=pnl
        previous=state
        state=allocate_close(state,pnl,NOW+timedelta(hours=i),replace(POLICY,max_active_quote=D(200)))
        assert state.total == D(100)+pnl_sum
        assert state.reserve_quote >= previous.reserve_quote
        if pnl < 0:
            assert state.reserve_quote == previous.reserve_quote
            assert state.growth_quote == previous.growth_quote
        assert state.promotions-previous.promotions in (0,1)


@pytest.mark.parametrize('values',[
    {'interval_days':1},{'step_max_pct':D('.1')},{'reserve_ratio':D('NaN')},
    {'growth_trigger':D('Infinity')},{'step_quote':D(2)},
])
def test_dangerous_policy_is_rejected(values):
    with pytest.raises(ValueError): CapitalPolicy(**values)


def test_capital_steps_cannot_be_armed_in_live():
    with pytest.raises(RuntimeError,match='paper'):
        config(RISK_PROFILE='micro',CAPITAL_STEPS_ENABLED='true',TRADING_MODE='live')


def position():
    return PositionRow(uuid.uuid4(),'BTC/EUR',D(100),D('.05'),D(95),None,
                       NOW-timedelta(hours=9),D('.25'),NOW-timedelta(hours=8))


def bot(repo, *, settings=None, broker=None):
    return Orchestrator(settings or config(),repo,object(),FakeLLM(llm_signal('HOLD')),
                        broker or PaperBroker(D('.008'),5),now=lambda:NOW)


def candle(close_at, *, low=96, close=115):
    return [int((close_at-timedelta(hours=4)).timestamp()*1000),100,116,low,close,1]


@pytest.mark.asyncio
async def test_all_missed_candles_are_checked_before_a_recovery(monkeypatch):
    pos=position(); repo=FakeRepo(state=IN_POSITION,position=pos)
    repo.capital=start_capital(D('9.37'),NOW-timedelta(days=1))
    monkeypatch.setattr(module,'build_snapshot',lambda *a,**k:symbol_state(price='115'))
    monkeypatch.setattr(module,'closed_candles',lambda *a,**k:[candle(NOW-timedelta(hours=4),low=90),candle(NOW)])
    report=CycleReport('PAPER',IN_POSITION,IN_POSITION)
    await bot(repo)._monitor(StateMachine(repo,'PAPER',IN_POSITION),pos,repo.account_snapshot,report)
    assert repo.closed['exit_reason'] == 'STOP_LOSS'
    assert repo.closed['exit_price'] < 95


@pytest.mark.asyncio
async def test_stop_cursor_survives_an_interruption_before_the_llm_signal(monkeypatch):
    pos=replace(position(),monitor_cursor_at=NOW-timedelta(hours=4))
    repo=FakeRepo(state=IN_POSITION,position=pos)
    monkeypatch.setattr(module,'build_snapshot',lambda *a,**k:symbol_state(price='115'))
    monkeypatch.setattr(module,'closed_candles',lambda *a,**k:[candle(NOW)])
    monkeypatch.setattr(module,'last_price',lambda *a,**k:D(115))
    instance=bot(repo)
    instance._llm.propose=AsyncMock(side_effect=RuntimeError('interruption'))
    with pytest.raises(RuntimeError):
        await instance._monitor(StateMachine(repo,'PAPER',IN_POSITION),pos,repo.account_snapshot,CycleReport('PAPER',IN_POSITION,IN_POSITION))
    assert repo.position.stop_loss_price == 110 and repo.position.monitor_cursor_at == NOW
    instance=bot(repo)
    await instance._monitor(StateMachine(repo,'PAPER',IN_POSITION),repo.position,repo.account_snapshot,CycleReport('PAPER',IN_POSITION,IN_POSITION))
    assert repo.closed is None


@pytest.mark.asyncio
async def test_history_gap_is_not_interpreted_as_no_stop(monkeypatch):
    pos=position(); repo=FakeRepo(state=IN_POSITION,position=pos)
    monkeypatch.setattr(module,'build_snapshot',lambda *a,**k:symbol_state(price='115'))
    monkeypatch.setattr(module,'closed_candles',lambda *a,**k:[candle(NOW)])
    with pytest.raises(RuntimeError,match='trou de surveillance'):
        await bot(repo)._monitor(StateMachine(repo,'PAPER',IN_POSITION),pos,repo.account_snapshot,CycleReport('PAPER',IN_POSITION,IN_POSITION))
    assert repo.closed is None


@pytest.mark.asyncio
async def test_account_risk_excludes_reserve_and_growth():
    repo=FakeRepo(free_quote='10')
    account=replace(repo.account_snapshot,equity=D(100),reserve_quote=D(60),growth_quote=D(30))
    assert bot(repo)._account_state(account).equity_quote == 10


@pytest.mark.asyncio
async def test_live_balance_keeps_virtual_reserve_out_of_the_next_purchase():
    class Broker:
        async def free_quote(self): return D(12)
    repo=FakeRepo(free_quote='10')
    repo.account_snapshot=replace(repo.account_snapshot,equity=D(12),reserve_quote=D(2))
    instance=bot(repo,settings=config(RISK_PROFILE='micro'),broker=Broker())
    instance._evaluate_cycle=AsyncMock()
    await instance.run_once()
    account=instance._evaluate_cycle.await_args.args[1]
    assert account.free_quote == 10 and account.reserve_quote == 2


@pytest.mark.asyncio
async def test_uncertain_live_exit_never_sends_a_second_sell():
    pos=position();repo=FakeRepo(state=IN_POSITION,position=pos)
    repo.pending[pos.id]={'fill':None}
    broker=PaperBroker(D('.008'),5)
    broker.exit=AsyncMock()
    instance=bot(repo,settings=config(RISK_PROFILE='micro'),broker=broker)
    instance._mode='LIVE'
    machine=StateMachine(repo,'LIVE',IN_POSITION)
    await instance._monitor(machine,pos,repo.account_snapshot,CycleReport('LIVE',IN_POSITION,IN_POSITION))
    broker.exit.assert_not_called()
    assert machine.current == HALT


def test_the_fill_and_risk_use_the_same_effective_fee():
    class Exchange:
        def fetch_trading_fee(self,symbol):return {'taker':'.01'}
    settings=config();broker=make_broker(settings,Exchange())
    instance=bot(FakeRepo(),settings=settings,broker=broker)
    assert instance._settings.risk.execution_fee_pct == D('.01')


@pytest.mark.asyncio
async def test_replay_protects_wins_and_uses_only_the_active_envelope_for_sizing():
    from tests.test_backtest import candles,h1_series,market_info,settings,START,H4_MS
    from app.backtest.engine import run_backtest
    settings=settings()
    settings.capital_policy=replace(POLICY,max_active_quote=D(2000))
    h4=candles(START,260,H4_MS)
    # Un TP synthétique, sans prétention de rentabilité : les gains doivent
    # quitter immédiatement l'enveloppe d'achat avant le premier palier.
    h4[251][2]=h4[250][4]*1.10
    h4[251][3]=h4[250][4]*.999
    h4[251][4]=h4[250][4]*1.09
    result=await run_backtest(symbol='BTC/EUR',info=market_info(),h4=h4,h1=h1_series(),settings=settings,
                              propose=None,mechanical=True,start_index=250,end_index=251,counterfactuals=False)
    capital=result.baseline_capital
    assert result.baseline_trades
    assert capital.reserve_quote > 0 and capital.growth_quote > 0
    assert capital.total == result.baseline_equity_end
    assert capital.active_quote == 1000  # Gains exclus des achats avant le premier palier.
