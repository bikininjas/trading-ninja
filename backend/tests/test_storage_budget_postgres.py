"""Contrats de rétention sur PostgreSQL jetable uniquement."""
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest
import pytest_asyncio

pytestmark = [pytest.mark.postgres_integration, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def db(postgres_test_url):
    conn = await asyncpg.connect(postgres_test_url)
    tx = conn.transaction()
    await tx.start()
    try:
        yield conn
    finally:
        await tx.rollback()
        await conn.close()


async def test_retention_preserves_active_and_recent_intel_and_financial_state(db):
    now = datetime.now(timezone.utc)
    await db.execute("DELETE FROM public.market_intel_digests")
    for age, valid in [(31, -30), (1, 1), (31, 1)]:
        observed = now - timedelta(days=age)
        expires = now + timedelta(days=valid)
        await db.execute("INSERT INTO public.market_intel_digests(observed_at,valid_until,status,overall_sentiment,sentiment_score,systemic_risk_detected,affected_assets,catalyst_summary,model,source_hashes) VALUES($1,$2,'OK','NEUTRAL',0,false,'{}','test','test','{}')", observed, expires)
    before = await db.fetchval("SELECT count(*) FROM public.positions")
    await db.execute("SELECT tn_storage_maintenance.run()")
    assert await db.fetchval("SELECT count(*) FROM public.market_intel_digests") == 2
    assert await db.fetchval("SELECT count(*) FROM public.positions") == before
    status = await db.fetchrow("SELECT * FROM tn_storage_maintenance.status")
    assert status['digests_deleted'] == 1 and status['candles_deleted'] == 0
    assert status['level'] == 'OK' and status['db_bytes'] > 0


@pytest.mark.parametrize('role', ['anon', 'authenticated', 'tn_paper_engine', 'tn_market_intel_writer'])
async def test_maintenance_is_private(db, role):
    assert not await db.fetchval("SELECT has_schema_privilege($1, 'tn_storage_maintenance', 'USAGE')", role)
    assert not await db.fetchval("SELECT has_function_privilege($1, 'tn_storage_maintenance.run()', 'EXECUTE')", role)


async def test_api_can_read_status_but_not_modify_or_run_maintenance(db):
    assert await db.fetchval("SELECT has_schema_privilege('tn_paper_api','tn_storage_maintenance','USAGE')")
    assert await db.fetchval("SELECT has_table_privilege('tn_paper_api','tn_storage_maintenance.status','SELECT')")
    assert not await db.fetchval("SELECT has_table_privilege('tn_paper_api','tn_storage_maintenance.status','UPDATE')")
    assert not await db.fetchval("SELECT has_function_privilege('tn_paper_api','tn_storage_maintenance.run()','EXECUTE')")
