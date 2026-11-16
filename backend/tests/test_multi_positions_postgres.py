"""Deux clôtures sur PostgreSQL jetable : les autres actifs restent dans l'équité."""
from decimal import Decimal as D

import pytest

from app.engine.machine import COOLDOWN, IN_POSITION, StateMachine
from app.engine.risk_gate import RejectionCode, evaluate
from app.execution.paper import Fill, PaperBroker
from app.runtime.orchestrator import CycleReport, Orchestrator
from tests.test_insights_postgres import pg
from tests.test_multi_positions import config
from tests.test_orchestrator import FakeLLM, NOW, llm_signal
from tests.test_risk_gate import CONSTRAINTS, intent, market

pytestmark = [pytest.mark.postgres_integration, pytest.mark.asyncio]


async def test_two_real_ledger_closes_preserve_cash_equity_and_stops(pg, tmp_path, monkeypatch):
    repo, conn = pg
    await conn.execute("UPDATE bot_state SET state='IN_POSITION' WHERE mode='PAPER'")
    for symbol in ('BTC/EUR', 'ETH/EUR'):
        await repo.create_position('PAPER', symbol=symbol, entry_price=D(100), amount=D(1),
            notional_quote=D(100), stop_loss_price=D(95), take_profit_price=D(120),
            risk_amount_quote=D(5), r_multiple_at_tp=D(4), signal_id=None,
            evaluation_id=None, protective_order_id='paper-stop-' + symbol)
    await repo.snapshot_equity('PAPER', equity=D(1000), free_quote=D(800), position_value_quote=D(200))
    positions = await repo.open_positions('PAPER')
    monkeypatch.setattr('app.runtime.orchestrator.last_price', lambda *a: D(100))
    engine = Orchestrator(config(tmp_path), repo, object(), FakeLLM(llm_signal('HOLD')),
                          PaperBroker(D('.0026'), 5), now=lambda: NOW)
    machine = StateMachine(repo, 'PAPER', IN_POSITION)
    report = CycleReport('PAPER', IN_POSITION, IN_POSITION)
    for index, position in enumerate(positions):
        account = await repo.account('PAPER', D(1000))
        fill = Fill(D(98), D(1), D(98), D('.2548'), D(0))
        await engine._close_trade(machine, position, account, D(98), 'STOP_LOSS', report,
                                  purpose='STOP_LOSS', fill=fill)
        saved = await repo.account('PAPER', D(1000))
        expected_cash = D(800) + D('97.7452') * (index + 1)
        assert saved.free_quote == expected_cash
        assert saved.equity == expected_cash + D(100) * (1 - index)
        assert saved.consecutive_losses == index + 1
        assert len(await repo.open_positions('PAPER')) == 1 - index
    assert machine.current == COOLDOWN
    assert await conn.fetchval("SELECT count(*) FROM orders WHERE mode='PAPER' AND status='FILLED'") == 2


async def test_entry_refreshes_sql_drawdowns_before_admitting_more_risk(pg, tmp_path, monkeypatch):
    repo, _ = pg
    await repo.create_position('PAPER', symbol='BTC/EUR', entry_price=D(100), amount=D(2),
        notional_quote=D(200), stop_loss_price=D(95), take_profit_price=D(120),
        risk_amount_quote=D(10), r_multiple_at_tp=D(4), signal_id=None,
        evaluation_id=None, protective_order_id='paper-stop')
    await repo.snapshot_equity('PAPER', equity=D(1000), free_quote=D(800), position_value_quote=D(200))
    monkeypatch.setattr('app.runtime.orchestrator.last_price', lambda *a: D(50))
    engine = Orchestrator(config(tmp_path), repo, object(), FakeLLM(llm_signal('HOLD')),
                          PaperBroker(D('.0026'), 5), now=lambda: NOW)
    stale = await repo.account('PAPER', D(1000))
    assert stale.daily_drawdown_pct == 0
    current, state = await engine._entry_context(stale)
    assert current.equity == D(900)
    assert state.daily_drawdown_pct == D('.1')
    assert state.drawdown_from_hwm_pct == D('.1')
    decision = evaluate(intent(), market(), state, CONSTRAINTS, engine._settings.risk)
    assert decision.rejection_code == RejectionCode.CIRCUIT_BREAKER_DAILY_DRAWDOWN
