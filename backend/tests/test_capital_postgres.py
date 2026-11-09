"""Contrat PostgreSQL réel : opt-in, exclusivement base locale jetable migrée.

TN_TEST_DATABASE_URL=postgresql://...@127.0.0.1:55432/postgres pytest tests/test_capital_postgres.py
Chaque test annule sa transaction ; ne charge jamais les fichiers .env.
"""
import os
import uuid
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from urllib.parse import urlparse
from unittest.mock import AsyncMock

import asyncpg
import pytest
import pytest_asyncio

from app.db.repository import PostgresRepository
from app.engine.capital import CapitalPolicy
from app.engine.machine import StateMachine, IN_POSITION
from app.execution.paper import PaperBroker, Fill
from app.runtime.orchestrator import Orchestrator, CycleReport
from tests.test_capital import config, NOW
from tests.test_orchestrator import FakeLLM, llm_signal

DSN=os.getenv('TN_TEST_DATABASE_URL','')
pytestmark=pytest.mark.skipif(not DSN, reason='base PostgreSQL locale de test non configurée')


@pytest_asyncio.fixture
async def repo():
    parsed=urlparse(DSN)
    assert parsed.hostname in ('127.0.0.1','localhost') and parsed.port == 55432, 'base jetable locale sur 55432 uniquement'
    conn=await asyncpg.connect(DSN)
    tx=conn.transaction()
    await tx.start()
    try:
        repository=PostgresRepository(conn,lock_connection_factory=lambda:asyncpg.connect(DSN))
        await repository.ensure_mode('PAPER')
        await conn.execute("UPDATE bot_state SET state='IN_POSITION' WHERE mode='PAPER'")
        yield repository
    finally:
        await tx.rollback()
        await conn.close()


async def add_position(repo):
    await repo.ensure_capital('PAPER','EUR',D('9.37'),D(0),NOW-timedelta(days=8))
    position_id=await repo._conn.fetchval(
        "INSERT INTO positions(symbol,mode,entry_price,initial_amount,stop_loss_price,risk_amount_quote,opened_at,monitor_cursor_at) "
        "VALUES('BTC/EUR','PAPER',100,.05,95,.25,$1,$1) RETURNING id",NOW-timedelta(hours=4))
    return await repo.open_position('PAPER')


@pytest.mark.asyncio
async def test_fault_after_position_close_rolls_back_all_money_and_retries_once(repo):
    pos=await add_position(repo)
    await repo.snapshot_equity('PAPER',equity=D('9.37'),free_quote=D('4.33'),position_value_quote=D(5))
    account=await repo.account('PAPER',D('9.37'))
    instance=Orchestrator(config(),repo,object(),FakeLLM(llm_signal('HOLD')),PaperBroker(D('.008'),5),now=lambda:NOW)
    snapshot=repo.snapshot_equity
    repo.snapshot_equity=AsyncMock(side_effect=RuntimeError('panne après écriture du capital'))
    fill=Fill(D(150),D('.05'),D('7.5'),D('.06'),D(0))
    with pytest.raises(RuntimeError,match='panne'):
        await instance._close_trade(StateMachine(repo,'PAPER',IN_POSITION),pos,account,D(150),'TEST',
                                    CycleReport('PAPER',IN_POSITION,IN_POSITION),purpose='LLM_EXIT',fill=fill)
    assert (await repo.open_position('PAPER')).id == pos.id
    assert (await repo.state('PAPER'))[0] == IN_POSITION
    assert await repo._conn.fetchval('SELECT count(*) FROM capital_events') == 0
    assert await repo._conn.fetchval("SELECT count(*) FROM orders WHERE purpose='LLM_EXIT'") == 0
    assert (await repo.capital_state('PAPER')).total == D('9.37')
    repo.snapshot_equity=snapshot
    await instance._close_trade(StateMachine(repo,'PAPER',IN_POSITION),pos,account,D(150),'TEST',
                                CycleReport('PAPER',IN_POSITION,IN_POSITION),purpose='LLM_EXIT',fill=fill)
    assert await repo.open_position('PAPER') is None
    capital=await repo.capital_state('PAPER')
    assert capital.realized_pnl == D('2.4')
    assert capital.reserve_quote == D('.72')
    assert capital.growth_quote == D('1.43')
    assert capital.active_quote == D('9.62')
    assert capital.total == D('11.77')
    assert await repo._conn.fetchval('SELECT count(*) FROM capital_events') == 1
    # Rejouer le même identifiant ne crédite pas une seconde fois le gain.
    async with repo.transaction():
        with pytest.raises(ValueError,match='déjà affectée'):
            await repo.allocate_capital('PAPER',pos.id,D('2.4'),NOW,config().capital_policy,allow_promotion=True)
    assert (await repo.capital_state('PAPER')).total == D('11.77')


@pytest.mark.asyncio
async def test_cursor_and_stop_cannot_be_half_applied(repo):
    pos=await add_position(repo)
    with pytest.raises(RuntimeError):
        async with repo.transaction():
            await repo.advance_paper_position(pos,D(110),NOW)
            raise RuntimeError('rollback')
    saved=await repo.open_position('PAPER')
    assert saved.stop_loss_price == 95 and saved.monitor_cursor_at == pos.monitor_cursor_at
    await repo.advance_paper_position(saved,D(110),NOW)
    saved=await repo.open_position('PAPER')
    assert saved.stop_loss_price == 110 and saved.monitor_cursor_at == NOW
    with pytest.raises(Exception,match='curseur'):
        await repo.advance_paper_position(pos,D(105),NOW+timedelta(hours=4))


@pytest.mark.asyncio
async def test_new_tables_are_private_and_balances_have_database_constraints(repo):
    for table in ('capital_accounts','capital_events','pending_exits'):
        assert await repo._conn.fetchval('SELECT relrowsecurity FROM pg_class WHERE oid=$1::regclass',table)
        for role in ('anon','authenticated'):
            assert not await repo._conn.fetchval("SELECT has_table_privilege($1,$2,'SELECT,INSERT,UPDATE,DELETE')",role,table)
    await repo.ensure_capital('PAPER','EUR',D('9.37'),D(0),NOW)
    with pytest.raises(asyncpg.CheckViolationError):
        async with repo.transaction():
            await repo._conn.execute("UPDATE capital_accounts SET growth_quote=1 WHERE mode='PAPER'")


@pytest.mark.asyncio
async def test_live_fill_intent_survives_local_bookkeeping_rollback(repo):
    pos=await add_position(repo)
    await repo.prepare_exit('PAPER',pos.id,'TEST','LLM_EXIT',False)
    fill=Fill(D(150),D('.05'),D('7.5'),D('.06'),D(0))
    await repo.confirm_exit(pos.id,fill)
    with pytest.raises(RuntimeError):
        async with repo.transaction():
            await repo.clear_exit(pos.id)
            raise RuntimeError('panne comptable')
    pending=await repo.pending_exit(pos.id)
    assert pending['fill'] is not None
    assert pending['reason'] == 'TEST'


@pytest.mark.asyncio
async def test_parallel_cycle_is_refused_before_trading(repo):
    other=PostgresRepository(await asyncpg.connect(DSN),lock_connection_factory=lambda:asyncpg.connect(DSN))
    try:
        async with repo.cycle_lock('PAPER'):
            with pytest.raises(Exception,match='verrou'):
                async with other.cycle_lock('PAPER'): pass
        async with other.cycle_lock('PAPER'): pass
        # Lecture cohérente hors transaction de test (chemin du dashboard réel).
        account=await other.account('TESTNET',D(10))
        assert account.equity == 10 and not other._conn.is_in_transaction()
    finally: await other.close()


@pytest.mark.asyncio
async def test_manual_panic_updates_the_envelopes_atomically_without_promotion(repo,monkeypatch):
    from app.runtime.recovery import liquidate_open_positions
    from app.runtime import recovery
    pos=await add_position(repo)
    await repo.snapshot_equity('PAPER',equity=D('9.37'),free_quote=D('4.33'),position_value_quote=D(5))
    monkeypatch.setattr(recovery,'last_price',lambda *a:D(150))
    await liquidate_open_positions(repo,object(),PaperBroker(D('.008'),0),'PAPER',
                                   quote_currency='EUR',starting_equity=D('9.37'),now=NOW,
                                   capital_policy=config().capital_policy)
    state=await repo.capital_state('PAPER')
    assert state.total == D('11.77')
    assert state.active_quote == D('9.37') and state.reserve_quote == D('.72') and state.growth_quote == D('1.68')
    assert state.promotions == 0
    assert (await repo.state('PAPER'))[0] == 'EMERGENCY_HALT'
    assert await repo.open_position('PAPER') is None
