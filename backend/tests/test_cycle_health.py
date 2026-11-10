"""Un heartbeat vivant ne masque ni un cycle bloqué, ni son annulation."""

import asyncio
from datetime import datetime, timedelta, timezone
import json
import uuid

import asyncpg
import pytest

from app.insights.health import classify, cycle_health

NOW = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)
BODY = {"complete": True, "issues": 0, "runtime_expected": True,
        "notification_pending": False, "runtime_timeframe": "4h"}


@pytest.mark.parametrize(("started", "status", "reason"), [
    (NOW - timedelta(seconds=31), "RUNNING", "RUNTIME_STALLED"),
    (NOW - timedelta(days=1), "COMPLETE", "RUNTIME_LATE"),
    (NOW, "FAILED", "RUNTIME_INCOMPLETE"),
    (None, None, "RUNTIME_MISSING"),
    (NOW.replace(tzinfo=None), "COMPLETE", "RUNTIME_CYCLE_INVALID"),
    (NOW + timedelta(seconds=6), "RUNNING", "RUNTIME_CLOCK_SKEW"),
])
def test_cycle_progress_is_required_even_with_current_observer_and_heartbeat(started, status, reason):
    result = classify({"observed_at": NOW, "body": BODY, "runtime_heartbeat_at": NOW,
                       "runtime_cycle_started_at": started, "runtime_cycle_status": status},
                      now=NOW, max_age_seconds=30)
    assert result["status"] != "OK" and result["reason"] == reason


def test_thirty_second_budget_is_inclusive_and_respects_daily_schedule():
    assert cycle_health(NOW - timedelta(seconds=30), "RUNNING", now=NOW, timeframe="4h")["status"] == "OK"
    assert cycle_health(NOW - timedelta(hours=1), "COMPLETE", now=NOW, timeframe="1d")["status"] == "OK"


@pytest.mark.parametrize("submitted", [False, True])
@pytest.mark.asyncio
async def test_cancelled_cycle_is_recorded_failed_and_uncertainty_is_preserved(submitted):
    from app.runtime.orchestrator import Orchestrator
    from tests.test_insights import FeatureLedger, config

    ledger = FeatureLedger(now=NOW)
    engine = Orchestrator(config(), ledger, object(), object(), object(), now=lambda: NOW)
    entered = asyncio.Event()

    async def blocked():
        engine._submission = submitted
        entered.set()
        await asyncio.Event().wait()

    engine._run_once = blocked
    task = asyncio.create_task(engine.run_once())
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    packet = next(iter(ledger.packets.values()))
    assert packet["status"] == "FAILED" and packet["error"].startswith("CancelledError:")
    assert ledger.events[-1]["stage"] == ("EXECUTION_UNCERTAIN" if submitted else "CYCLE_CANCELLED")
    assert engine._recorder is None and engine._cycle_id is None


@pytest.mark.postgres_integration
@pytest.mark.asyncio
async def test_probe_reads_stalled_cycle_directly_instead_of_trusting_old_healthy_body(postgres_test_url):
    from app.core.db import from_env
    from scripts.watchdog import read_observation

    conn = await asyncpg.connect(postgres_test_url)
    cycle_id = uuid.uuid4()
    original = await conn.fetchval("SELECT last_heartbeat_at FROM bot_state WHERE mode='TESTNET'")
    try:
        await conn.execute("UPDATE bot_state SET last_heartbeat_at=$1 WHERE mode='TESTNET'", NOW)
        await conn.execute("INSERT INTO protection_observations(mode,observed_at,body) VALUES('TESTNET',$1,$2::jsonb)", NOW, json.dumps(BODY))
        await conn.execute("INSERT INTO decision_cycles(id,mode,started_at,status,manifest) VALUES($1,'TESTNET',$2,'RUNNING','{}')",
                           cycle_id, NOW - timedelta(seconds=31))
        record = await read_observation(from_env({"DATABASE_URL": postgres_test_url}), "TESTNET")
        assert classify(record, now=NOW, max_age_seconds=30)["reason"] == "RUNTIME_STALLED"
        await conn.execute("UPDATE decision_cycles SET status='COMPLETE',started_at=$2 WHERE id=$1", cycle_id, NOW)
        record = await read_observation(from_env({"DATABASE_URL": postgres_test_url}), "TESTNET")
        assert classify(record, now=NOW, max_age_seconds=30)["status"] == "OK"
    finally:
        await conn.execute("DELETE FROM protection_observations WHERE mode='TESTNET'")
        await conn.execute("DELETE FROM decision_cycles WHERE id=$1", cycle_id)
        await conn.execute("UPDATE bot_state SET last_heartbeat_at=$1 WHERE mode='TESTNET'", original)
        await conn.close()
