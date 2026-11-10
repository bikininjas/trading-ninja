"""Droits réels des services PAPER, cible locale jetable uniquement."""

from datetime import datetime, timezone
from decimal import Decimal as D

import asyncpg
import pytest
import pytest_asyncio

from app.db.repository import PostgresRepository
from app.execution.paper import PaperBroker
from app.insights.experiments import HistoricalExchange, ExperimentSignal
from app.runtime.orchestrator import Orchestrator
from tests.test_insights import config, historical_data
from scripts.paper_component import verify_sql_role, SQL_ROLES

pytestmark = [pytest.mark.postgres_integration, pytest.mark.asyncio]
ROLES = ('tn_paper_engine', 'tn_paper_api', 'tn_paper_observer', 'tn_paper_probe')


@pytest_asyncio.fixture
async def connection(postgres_test_url):
    conn = await asyncpg.connect(postgres_test_url)
    tx = conn.transaction()
    await tx.start()
    try:
        yield conn
    finally:
        await tx.rollback()
        await conn.close()


@pytest.mark.parametrize('role', ROLES)
async def test_roles_cannot_login_bypass_rls_see_live_or_change_schema(connection, role):
    conn = connection
    definition = await conn.fetchrow('SELECT rolcanlogin,rolsuper,rolcreaterole,rolcreatedb,rolbypassrls FROM pg_roles WHERE rolname=$1', role)
    assert not any(definition.values())
    await conn.execute(f'SET LOCAL ROLE {role}')
    component = next(c for c, name in SQL_ROLES.items() if name == role)
    await verify_sql_role(conn, component)
    with pytest.raises(ValueError, match='isolée'):
        await verify_sql_role(conn, 'api' if component != 'api' else 'engine')
    assert await conn.fetchval("SELECT count(*) FROM bot_state WHERE mode<>'PAPER'") == 0
    assert await conn.fetchval('SELECT count(*) FROM schema_migrations') == 14
    assert not await conn.fetchval("SELECT has_schema_privilege(current_user,'public','CREATE')")
    assert not await conn.fetchval("SELECT has_function_privilege(current_user,'ensure_partitions(integer)','EXECUTE')")
    assert not await conn.fetchval("SELECT has_table_privilege(current_user,'llm_signals_default','INSERT')")


async def test_probe_cannot_write_or_read_financial_details(connection):
    conn = connection
    await conn.execute('SET LOCAL ROLE tn_paper_probe')
    for sql in ("UPDATE bot_state SET state='IDLE' WHERE mode='PAPER'",
                "INSERT INTO protection_observations VALUES('PAPER',now(),'{}')",
                'SELECT * FROM positions', 'SELECT * FROM runtime_settings'):
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with conn.transaction():
                await conn.execute(sql)


async def test_observer_can_persist_incident_and_halt_but_cannot_resume_or_fill(connection):
    conn = connection
    await conn.execute('SET LOCAL ROLE tn_paper_observer')
    repo = PostgresRepository(conn)
    at = datetime.now(timezone.utc)
    await repo.record_protection('PAPER', {'observed_at': at, 'complete': True},
                                [{'key': 'qualification', 'severity': 'CRITICAL'}])
    await repo.protection_notification_state('PAPER', at, True)
    await repo.force_halt('PAPER', 'qualification locale')
    assert (await repo.state('PAPER'))[0] == 'EMERGENCY_HALT'
    assert await conn.fetchval("SELECT count(*) FROM observation_commands WHERE mode='PAPER'") == 1
    for sql in ("UPDATE bot_state SET state='IDLE' WHERE mode='PAPER'",
                "UPDATE bot_state SET last_heartbeat_at=now() WHERE mode='PAPER'",
                "UPDATE bot_state SET cooldown_until=now() WHERE mode='PAPER'",
                "INSERT INTO bot_state(mode) VALUES('LIVE')",
                "INSERT INTO orders(mode) VALUES('PAPER')"):
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with conn.transaction():
                await conn.execute(sql)


@pytest.mark.parametrize('role', ['tn_paper_engine', 'tn_paper_api'])
async def test_runtime_works_under_real_rls_and_cannot_write_live(connection, postgres_test_url, role):
    conn = connection
    await conn.execute(f'SET LOCAL ROLE {role}')
    async def lock_connection():
        other = await asyncpg.connect(postgres_test_url)
        await other.execute(f'SET ROLE {role}')
        return other
    repo = PostgresRepository(conn, lock_connection_factory=lock_connection)
    data = historical_data(264)
    now = datetime.fromtimestamp((data['BTC/EUR']['candles']['4h'][259][0] + 14_400_000) / 1000, timezone.utc)
    runtime = Orchestrator(config(), repo, HistoricalExchange(data, now),
                           ExperimentSignal({'kind': 'mechanical'}), PaperBroker(D('.008'), 5), now=lambda: now)
    report = await runtime.run_once()
    assert not report.errors and report.position_opened
    assert await conn.fetchval("SELECT count(*) FROM positions WHERE mode='PAPER'") == 1
    assert await conn.fetchval("SELECT count(*) FROM decision_cycles WHERE mode='PAPER' AND status='COMPLETE'") == 1
    assert (await repo.recent_cycles('PAPER'))
    packet = await repo.cycle_packet('PAPER', await conn.fetchval("SELECT id FROM decision_cycles WHERE mode='PAPER'"))
    assert packet['artifacts']
    for sql in ("INSERT INTO bot_state(mode) VALUES('LIVE') ON CONFLICT(mode) DO UPDATE SET state='IDLE'",
                "UPDATE bot_state SET mode='LIVE' WHERE mode='PAPER'",
                "UPDATE protection_observations SET body='{}' WHERE mode='PAPER'",
                'DELETE FROM orders'):
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with conn.transaction():
                await conn.execute(sql)
    assert await conn.execute("UPDATE runtime_settings SET payload='{}' WHERE mode='LIVE'") == 'UPDATE 0'


async def test_public_api_roles_still_have_no_table_or_maintenance_access(connection):
    for role in ('anon', 'authenticated'):
        assert not await connection.fetchval("SELECT has_table_privilege($1,'positions','SELECT')", role)
        assert not await connection.fetchval("SELECT has_function_privilege($1,'ensure_partitions(integer)','EXECUTE')", role)
    for role in ROLES:
        assert not await connection.fetchval("SELECT pg_has_role($1,'postgres','MEMBER')", role)


async def test_preflight_refuses_an_administrator_connection(connection):
    with pytest.raises(ValueError, match='isolée'):
        await verify_sql_role(connection, 'engine')
