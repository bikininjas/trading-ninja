"""Migration 0016, quota, RLS et cache OHLCV sur PostgreSQL jetable uniquement."""
from datetime import timedelta
from decimal import Decimal
import asyncpg
import pytest
import pytest_asyncio
from app.db.repository import PostgresRepository
from app.intel.models import neutral
from app.intel.store import store_within_budget, latest_digest, dashboard_digests, BudgetExceeded
from tests.test_risk_gate import NOW

pytestmark=[pytest.mark.postgres_integration,pytest.mark.asyncio]

@pytest_asyncio.fixture
async def db(postgres_test_url):
    conn=await asyncpg.connect(postgres_test_url)
    tx=conn.transaction(); await tx.start()
    try: yield conn
    finally: await tx.rollback(); await conn.close()


def row(**overrides):
    return {**neutral().model_dump(),'status':'OK','observed_at':NOW,'valid_until':NOW+timedelta(hours=2),
            'model':'fixture','source_hashes':[],**overrides}


async def test_writer_persists_and_purges_without_any_order_privilege(db):
    await db.execute('SET LOCAL ROLE tn_market_intel_writer')
    flags=await db.fetchrow("SELECT rolcanlogin,rolsuper,rolcreaterole,rolbypassrls FROM pg_roles WHERE rolname='tn_market_intel_writer'")
    assert not any(flags.values())
    assert not await db.fetchval("SELECT has_table_privilege(current_user,'orders','INSERT')")
    assert not await db.fetchval("SELECT has_table_privilege(current_user,'positions','UPDATE')")
    old=row(observed_at=NOW-timedelta(days=31),valid_until=NOW-timedelta(days=30))
    await store_within_budget(db,old)
    await store_within_budget(db,row(systemic_risk_detected=True,affected_assets=['BTC']))
    assert await db.fetchval('SELECT count(*) FROM market_intel_digests')==1
    await db.execute('RESET ROLE'); await db.execute('SET LOCAL ROLE tn_paper_engine')
    digest=await latest_digest(db,NOW)
    assert digest['systemic_risk_detected'] and isinstance(digest['sentiment_score'],Decimal)


@pytest.mark.parametrize('role',['anon','authenticated','tn_paper_probe','tn_paper_observer'])
async def test_public_and_unrelated_roles_cannot_read_intel(db,role):
    await db.execute(f'SET LOCAL ROLE {role}')
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with db.transaction(): await db.fetch('SELECT * FROM market_intel_digests')


async def test_degraded_neutral_never_clears_a_still_valid_systemic_digest(db):
    await store_within_budget(db,row(systemic_risk_detected=True))
    await store_within_budget(db,row(status='DEGRADED',observed_at=NOW+timedelta(minutes=1)))
    assert (await latest_digest(db,NOW+timedelta(minutes=2)))['systemic_risk_detected'] is True
    assert await latest_digest(db,NOW-timedelta(seconds=1)) is None
    assert await latest_digest(db,NOW+timedelta(hours=3)) is None
    view=await dashboard_digests(db,NOW+timedelta(minutes=2))
    assert view['latest_attempt']['status']=='DEGRADED' and view['selected']['systemic_risk_detected'] is True


async def test_source_snapshot_is_private_and_stays_linked_to_its_own_analysis(db):
    await db.execute('SET LOCAL ROLE tn_market_intel_writer')
    sources=[{'title':'Public headline','description':'Public excerpt','url':'https://cointelegraph.com/news/report',
              'published_at':NOW.isoformat(),'source':'https://cointelegraph.com/rss','sha256':'a'*64}]
    first=await store_within_budget(db,row(source_items=sources,source_hashes=['a'*64],analysis_response='{"catalyst_summary":"Contexte."}',fear_and_greed_value=0,fear_and_greed_at=NOW))
    await store_within_budget(db,row(observed_at=NOW+timedelta(minutes=1)))
    await db.execute('RESET ROLE'); await db.execute('SET LOCAL ROLE tn_paper_api')
    view=await dashboard_digests(db,NOW+timedelta(minutes=2),first)
    assert view['selected']['id']==first and view['active']['id']!=first
    assert view['selected']['source_items']==sources and view['selected']['fear_and_greed_value']==0
    assert view['selected']['analysis_response']=='{"catalyst_summary":"Contexte."}'
    assert view['active']['source_items']==[]
    with pytest.raises(LookupError): await dashboard_digests(db,NOW-timedelta(seconds=1),first)


@pytest.mark.parametrize('patch',[{'source_items':[{}]*81},{'fear_and_greed_value':101,'fear_and_greed_at':NOW},{'analysis_response':'x'*4097}])
async def test_sql_refuses_unbounded_or_invalid_source_metadata(db,patch):
    with pytest.raises(asyncpg.CheckViolationError):
        async with db.transaction(): await store_within_budget(db,row(**patch))
    assert await db.fetchval('SELECT count(*) FROM market_intel_digests')==0


async def test_real_database_quota_refuses_insert(db):
    with pytest.raises(BudgetExceeded): await store_within_budget(db,row(),quota_mb=1)
    assert await db.fetchval('SELECT count(*) FROM market_intel_digests')==0


async def test_cache_query_is_bounded_sorted_causal_and_readable_by_engine(db):
    from app.market.history import store_candles
    now=int(NOW.timestamp()*1000); step=3_600_000
    candles=[[now-(3-i)*step,1,2,1,2,1] for i in range(4)] # dernière non clôturée
    await store_candles(db,'BTC/EUR','1h',candles,'csv')
    await db.execute('SET LOCAL ROLE tn_paper_engine')
    result=await PostgresRepository(db).cached_market_candles('BTC/EUR','1h',2,now)
    assert [item[0] for item in result]==[now-2*step,now-step]
    assert isinstance(result[0][1],Decimal)
