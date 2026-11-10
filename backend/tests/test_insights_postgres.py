"""Preuves SQL des contrôles, paquets, incidents et révisions ; cible locale stricte."""

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
import uuid

import asyncpg
import httpx
import pytest
import pytest_asyncio

from app.db.repository import PostgresRepository, StateTransitionError
from app.insights.codec import digest
from app.insights.policy import position_policy
from app.insights.replay import replay_cycle
from tests.test_insights import config, FeatureLedger, historical_data
from app.insights.experiments import HistoricalExchange, ExperimentSignal
from app.execution.paper import PaperBroker
from app.runtime.orchestrator import Orchestrator

pytestmark = [pytest.mark.postgres_integration, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def pg(postgres_test_url):
    conn = await asyncpg.connect(postgres_test_url)
    tx = conn.transaction()
    await tx.start()
    repo = PostgresRepository(
        conn, lock_connection_factory=lambda: asyncpg.connect(postgres_test_url)
    )
    await repo.ensure_mode("PAPER")
    try:
        yield repo, conn
    finally:
        await tx.rollback()
        await conn.close()


async def add_position(repo):
    return await repo.create_position(
        "PAPER",
        symbol="BTC/EUR",
        entry_price=D(100),
        amount=D(1),
        notional_quote=D(100),
        stop_loss_price=D(95),
        take_profit_price=D(120),
        risk_amount_quote=D(5),
        r_multiple_at_tp=D(4),
        signal_id=None,
        evaluation_id=None,
        protective_order_id="paper-stop",
    )


async def test_entry_control_is_persistent_revisioned_and_does_not_flatten_positions(
    pg,
):
    repo, conn = pg
    pid = await add_position(repo)
    original = await repo.entry_control("PAPER")
    disabled = await repo.set_entry_permission(
        "PAPER", False, original["revision"], reason="maintenance"
    )
    same = await repo.set_entry_permission(
        "PAPER", False, original["revision"], reason="maintenance"
    )
    assert disabled["revision"] == same["revision"]
    assert (await repo.open_position("PAPER")).id == pid
    await repo.force_halt("PAPER", "urgence surveillant")
    with pytest.raises(StateTransitionError, match="bloqué"):
        await repo.set_entry_permission("PAPER", True, disabled["revision"])
    await repo.force_halt(
        "PAPER", "LLM", auto_resume_at=datetime.now(timezone.utc) + timedelta(days=1)
    )
    assert (await repo.state("PAPER"))[1] is None
    assert await repo.halt_details("PAPER") == "urgence surveillant"


async def test_policy_is_saved_with_the_position_and_cannot_be_silently_replaced(pg):
    repo, conn = pg
    pid = await add_position(repo)
    policy = position_policy(config())
    assert await repo.attach_policy("PAPER", pid, policy) == pid
    assert await repo.position_policy(pid) == policy
    assert await repo.attach_policy("PAPER", pid, policy) is None
    assert (await repo.open_position("PAPER")).policy_id == policy["id"]
    assert await conn.fetchval("SELECT COUNT(*) FROM position_policies") == 1


async def test_packet_survives_jsonb_roundtrip_and_replays_the_runtime(pg):
    repo, conn = pg
    data = historical_data()
    now = datetime.fromtimestamp(
        (data["BTC/EUR"]["candles"]["4h"][259][0] + 14400000) / 1000, timezone.utc
    )
    memory = FeatureLedger(now=now)
    runtime = Orchestrator(
        config(),
        memory,
        HistoricalExchange(data, now),
        ExperimentSignal({"kind": "mechanical"}),
        PaperBroker(D(".008"), 5),
        now=lambda: now,
    )
    await runtime.run_once()
    cid, packet = next(iter(memory.packets.items()))
    await repo.begin_cycle(cid, "PAPER", now, packet["manifest"])
    await repo.finish_cycle(cid, packet)
    exported = await repo.cycle_packet("PAPER", cid)
    assert (
        exported == packet and (await replay_cycle(exported))["status"] == "IDENTICAL"
    )
    assert await repo.cycle_packet("LIVE", cid) is None
    assert await conn.fetchval("SELECT COUNT(*) FROM decision_artifacts") >= 1


async def test_opportunity_events_are_unique_scoped_and_keep_distinct_denominators(pg):
    repo, _ = pg
    cid = uuid.uuid4()
    at = datetime.now(timezone.utc)
    await repo.begin_cycle(cid, "PAPER", at, {"schema_version": 1})
    await repo.append_opportunity(
        cid, 1, "PAPER", "APPROVED", at, symbol="BTC/EUR", opportunity_id="one"
    )
    await repo.append_opportunity(
        cid,
        2,
        "PAPER",
        "NOT_SELECTED",
        at,
        symbol="BTC/EUR",
        opportunity_id="one",
        terminal=True,
    )
    events = await repo.opportunities(
        "PAPER", at - timedelta(seconds=1), at + timedelta(seconds=1)
    )
    assert len(events) == 2 and events[0]["opportunity_id"] == "one"
    assert await repo.cycle_events("LIVE", cid) == []
    with pytest.raises(asyncpg.UniqueViolationError):
        async with repo.transaction():
            await repo.append_opportunity(cid, 2, "PAPER", "FILLED", at)


async def test_settings_acknowledgment_cannot_claim_a_newer_revision(pg):
    repo, _ = pg
    before = await repo.settings_state("PAPER")
    await repo.update_runtime_settings(
        "PAPER", {"MIN_CONFIDENCE": ".6"}, expected_revision=before["revision"]
    )
    first = await repo.runtime_snapshot("PAPER")
    await repo.update_runtime_settings(
        "PAPER", {"MIN_CONFIDENCE": ".7"}, expected_revision=first["revision"]
    )
    await repo.acknowledge_settings("PAPER", first["revision"], {"risk": "old"})
    state = await repo.settings_state("PAPER")
    assert state["applied_revision"] != state["revision"]
    await repo.acknowledge_settings(
        "PAPER", state["revision"], {}, error="cadence incompatible"
    )
    assert (await repo.settings_state("PAPER"))["apply_error"] == "cadence incompatible"
    with pytest.raises(StateTransitionError):
        await repo.update_runtime_settings(
            "PAPER", {}, expected_revision=first["revision"]
        )


async def test_incidents_deduplicate_commands_ack_is_not_resolution_and_stale_reads_do_not_override(
    pg,
):
    repo, conn = pg
    now = datetime.now(timezone.utc)
    observation = {"observed_at": now, "complete": True}
    issue = {"key": "STOP:one", "severity": "CRITICAL", "reason": "stop manquant"}
    await repo.record_protection("PAPER", observation, [issue])
    await repo.record_protection("PAPER", observation, [issue])
    assert await conn.fetchval("SELECT COUNT(*) FROM observation_commands") == 1
    health = await repo.protection_health("PAPER")
    iid = health["incidents"][0]["id"]
    await repo.acknowledge_incident("PAPER", iid)
    assert (await repo.protection_health("PAPER"))["incidents"][0][
        "resolved_at"
    ] is None
    await repo.record_protection(
        "PAPER", {"observed_at": now + timedelta(seconds=1), "complete": False}, []
    )
    assert (await repo.protection_health("PAPER"))["incidents"][0][
        "resolved_at"
    ] is None
    assert (
        await repo.record_protection(
            "PAPER", {"observed_at": now - timedelta(seconds=1), "complete": True}, []
        )
        is False
    )
    await repo.record_protection(
        "PAPER", {"observed_at": now + timedelta(seconds=2), "complete": True}, []
    )
    assert (await repo.protection_health("PAPER"))["incidents"][0][
        "resolved_at"
    ] is not None
    assert await repo.consume_observation_commands("PAPER") == 1
    assert await repo.consume_observation_commands("PAPER") == 0
    assert (await repo.state("PAPER"))[0] == "EMERGENCY_HALT"


async def test_failed_experiments_remain_visible_with_immutable_manifest(pg):
    repo, _ = pg
    manifest = {
        "schema_version": 1,
        "variants": [{"name": "negative", "kind": "recorded"}],
    }
    eid = await repo.start_experiment("PAPER", manifest)
    await repo.finish_experiment(
        "PAPER", eid, {"reason": "signal manquant"}, failed=True
    )
    found = await repo.experiments("PAPER")
    assert found[0]["state"] == "FAILED" and found[0]["manifest_digest"] == digest(
        manifest
    )
    await repo.finish_experiment("PAPER", eid, {"reason": "overwrite"}, failed=False)
    assert (await repo.experiments("PAPER"))[0]["state"] == "FAILED"


@pytest.mark.parametrize(
    "table",
    [
        "position_policies",
        "entry_controls",
        "decision_cycles",
        "decision_artifacts",
        "decision_cycle_artifacts",
        "opportunity_events",
        "observation_experiments",
        "protection_observations",
        "protection_incidents",
        "observation_commands",
    ],
)
async def test_observation_data_is_not_available_to_the_public_role(pg, table):
    repo, conn = pg
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with repo.transaction():
            await conn.execute("SET LOCAL ROLE anon")
            await conn.fetch(f"SELECT * FROM {table} LIMIT 1")


async def test_parallel_api_reads_acquire_distinct_pool_connections(postgres_test_url):
    from app.api.main import create_app
    from app.api.security import create_token, hash_password

    settings = config(
        DATABASE_URL=postgres_test_url,
        JWT_SECRET_KEY="j" * 48,
        TOTP_SECRET="JBSWY3DPEHPK3PXP",
        ADMIN_PASSWORD_HASH=hash_password("test-only"),
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            responses = await asyncio.gather(
                *[
                    client.get(
                        path,
                        headers={
                            "Authorization": "Bearer "
                            + create_token(settings.jwt_secret)
                        },
                    )
                    for path in [
                        "/api/status",
                        "/api/insights/overview",
                        "/api/settings",
                    ]
                    * 8
                ]
            )
    assert all(r.status_code == 200 for r in responses)

async def test_public_cannot_execute_privileged_maintenance(pg):
    repo,conn=pg
    for role in ('anon','authenticated'):
        assert not await conn.fetchval("SELECT has_function_privilege($1,'public.ensure_partitions(integer)','EXECUTE')",role)
    with pytest.raises(asyncpg.RaiseError):
        async with repo.transaction():await conn.fetchval('SELECT public.ensure_partitions(25)')

async def test_canceled_partial_fills_remain_in_trade_diagnostics(pg):
    repo,conn=pg;pid=await add_position(repo)
    await conn.execute("INSERT INTO orders(position_id,client_order_id,symbol,mode,side,order_type,purpose,status,requested_amount,filled_amount,average_fill_price,fee_amount,fee_currency) VALUES($1,'canceled-partial','BTC/EUR','PAPER','SELL','MARKET','LLM_EXIT','CANCELED',1,.4,110,.1,'EUR')",pid)
    data=await repo.diagnostics_input('PAPER',pid)
    assert len(data['orders'])==1 and data['orders'][0]['filled_amount']==D('.4')

async def test_live_partial_fills_are_cumulative_idempotent_and_keep_remaining_quantity(pg):
    from app.execution.paper import Fill
    repo,conn=pg;pid=await add_position(repo)
    partial=Fill(price=D(110),amount=D('.4'),notional_quote=D(44),fee_quote=D('.1'),slippage_quote=D(0),order_id='partial-exchange-order')
    result=await repo.account_exit('PAPER','BTC/EUR',partial,purpose='LLM_EXIT',reason='TEST',quote='EUR',taker_fee=D('.008'))
    assert result['remaining']==D('.6') and (await repo.open_position('PAPER')).amount==D('.6')
    assert await repo.account_exit('PAPER','BTC/EUR',partial,purpose='LLM_EXIT',reason='TEST',quote='EUR',taker_fee=D('.008')) is None
    from dataclasses import replace
    final=replace(partial,amount=D(1),notional_quote=D(110),fee_quote=D('.25'))
    result=await repo.account_exit('PAPER','BTC/EUR',final,purpose='LLM_EXIT',reason='TEST',quote='EUR',taker_fee=D('.008'))
    assert result['remaining']==0 and await repo.open_position('PAPER') is None
    assert await conn.fetchval('SELECT COUNT(*) FROM orders WHERE exchange_order_id=$1',partial.order_id)==1

async def test_unresolved_intent_blocks_human_resume(pg):
    repo,conn=pg
    await conn.execute("INSERT INTO execution_requests(client_id,mode,symbol,request,state) VALUES('unknown-request','PAPER','BTC/EUR','{}','UNKNOWN')")
    await repo.force_halt('PAPER','incertain')
    with pytest.raises(StateTransitionError,match='non rapprochées'):await repo.force_resume('PAPER','reprise')
    assert (await repo.state('PAPER'))[0]=='EMERGENCY_HALT'

async def test_prepared_execution_survives_ledger_transaction_rollback(postgres_test_url):
    from app.core.db import from_env
    repo=await PostgresRepository.connect(from_env({'DATABASE_URL':postgres_test_url}))
    rid=None
    try:
        await repo.ensure_mode('PAPER')
        async with repo.cycle_lock('PAPER'):
            rid=await repo.prepare_request('PAPER','BTC/EUR',{'type':'market','side':'buy','amount':'1'})
            await repo.update_request(rid,'ACKNOWLEDGED',response={'id':'audit-durable-order'})
            with pytest.raises(RuntimeError):
                async with repo.transaction():
                    await repo.book_request('PAPER','audit-durable-order')
                    raise RuntimeError('bookkeeping rollback')
            assert (await repo.pending_requests('PAPER'))[0]['state']=='ACKNOWLEDGED'
            async with repo.transaction():
                with pytest.raises(StateTransitionError,match='avant'):
                    await repo.prepare_request('PAPER','BTC/EUR',{'type':'market','side':'buy'})
    finally:
        if rid:await repo._conn.execute('DELETE FROM execution_requests WHERE client_id=$1',rid)
        await repo.close()
