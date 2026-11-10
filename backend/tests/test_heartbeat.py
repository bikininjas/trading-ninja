"""Vie continue du moteur : veille, panne SQL, sortie et arrêt brutal."""

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import subprocess
import sys

import asyncpg
import pytest

from app.core.db import from_env
from app.db.repository import PostgresRepository
from app.insights.health import classify
from app.runtime.heartbeat import run_with_heartbeat

NOW = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)
CONFIG = from_env({"DATABASE_URL": "postgresql://postgres:test@127.0.0.1/test?sslmode=disable"})


class Connection:
    def __init__(self, fail_at=None):
        self.count = 0
        self.fail_at = fail_at
        self.closed = False
        self.cleared = False
        self.renewed = asyncio.Event()

    async def fetchval(self, query, mode):
        self.count += 1
        if self.count == self.fail_at:
            raise OSError("database unavailable")
        if self.count >= 3:
            self.renewed.set()
        return NOW + timedelta(seconds=self.count)

    async def execute(self, query, mode, at):
        assert "last_heartbeat_at=$2" in query
        self.cleared = True

    async def close(self, **kwargs):
        self.closed = True


@pytest.mark.asyncio
async def test_heartbeat_renews_during_idle_and_stops_with_worker():
    conn = Connection()

    async def connect(**kwargs):
        assert kwargs["timeout"] == kwargs["command_timeout"] == 5
        return conn

    async def idle():
        await asyncio.wait_for(conn.renewed.wait(), 1)
        return "finished"

    assert await run_with_heartbeat(idle, CONFIG, "PAPER", interval=.001, connect=connect) == "finished"
    assert conn.count >= 3 and conn.closed and conn.cleared
    assert not any(t.get_name() == "engine-heartbeat" for t in asyncio.all_tasks())


@pytest.mark.parametrize("fail_at", [1, 2])
@pytest.mark.asyncio
async def test_database_failure_prevents_or_cancels_work(fail_at):
    conn = Connection(fail_at)
    started = cancelled = False

    async def connect(**kwargs):
        return conn

    async def idle():
        nonlocal started, cancelled
        started = True
        try:
            await asyncio.Event().wait()
        finally:
            cancelled = True

    with pytest.raises(OSError):
        await asyncio.wait_for(run_with_heartbeat(idle, CONFIG, "PAPER", interval=.001, connect=connect), 1)
    assert started is (fail_at == 2) and cancelled is (fail_at == 2)
    assert conn.closed


@pytest.mark.asyncio
async def test_worker_failure_does_not_leave_a_live_heartbeat():
    conn = Connection()

    async def connect(**kwargs):
        return conn

    async def fail():
        raise ValueError("cycle failed")

    with pytest.raises(ValueError, match="cycle failed"):
        await run_with_heartbeat(fail, CONFIG, "PAPER", connect=connect)
    assert conn.cleared and conn.closed


@pytest.mark.parametrize(("at", "reason"), [
    (None, "RUNTIME_HEARTBEAT_MISSING"),
    (NOW - timedelta(seconds=31), "RUNTIME_HEARTBEAT_STALE"),
    (NOW + timedelta(seconds=6), "RUNTIME_CLOCK_SKEW"),
    (NOW.replace(tzinfo=None), "RUNTIME_HEARTBEAT_INVALID"),
    (NOW.isoformat(), "RUNTIME_HEARTBEAT_INVALID"),
])
def test_recent_observer_without_current_runtime_never_passes(at, reason):
    record = {"observed_at": NOW, "body": {"complete": True, "issues": 0, "runtime_expected": True},
              "runtime_heartbeat_at": at}
    assert classify(record, now=NOW, max_age_seconds=35) == {"status": "UNKNOWN", "reason": reason}


@pytest.mark.asyncio
async def test_observer_halts_even_between_two_daily_decisions():
    from app.insights.watch import observe_protection
    from tests.test_insights import FeatureLedger

    ledger = FeatureLedger(now=NOW - timedelta(seconds=31))

    async def completed_daily_cycle(*args):
        return [{"status": "COMPLETE", "started_at": NOW - timedelta(hours=1)}]

    ledger.recent_cycles = completed_daily_cycle
    result = await observe_protection(ledger, None, "PAPER", now=NOW,
                                      expect_runtime=True, timeframe="1d")
    assert not result["observation"]["complete"]
    assert [i["key"] for i in result["issues"]] == ["RUNTIME_HEARTBEAT_STALE"]
    assert ledger.current == "EMERGENCY_HALT"


@pytest.mark.parametrize("status", ["FAILED", "INCOMPLETE"])
@pytest.mark.asyncio
async def test_alive_engine_with_failed_last_cycle_is_not_healthy(status):
    from app.insights.watch import observe_protection
    from tests.test_insights import FeatureLedger

    ledger = FeatureLedger(now=NOW)

    async def failed_cycle(*args):
        return [{"status": status, "started_at": NOW - timedelta(minutes=1)}]

    ledger.recent_cycles = failed_cycle
    result = await observe_protection(ledger, None, "PAPER", now=NOW, expect_runtime=True)
    assert not result["observation"]["complete"]
    assert [i["key"] for i in result["issues"]] == ["RUNTIME_INCOMPLETE"]


@pytest.mark.postgres_integration
@pytest.mark.asyncio
async def test_equity_snapshot_cannot_refresh_runtime_liveness(postgres_test_url):
    conn = await asyncpg.connect(postgres_test_url)
    tx = conn.transaction()
    await tx.start()
    try:
        await conn.execute("UPDATE bot_state SET last_heartbeat_at=$1 WHERE mode='TESTNET'", NOW)
        repo = PostgresRepository(conn)
        await repo.snapshot_equity("TESTNET", equity=Decimal(100), free_quote=Decimal(100),
                                   position_value_quote=Decimal(0))
        assert await repo.runtime_heartbeat("TESTNET") == NOW
    finally:
        await tx.rollback()
        await conn.close()


@pytest.mark.postgres_integration
@pytest.mark.asyncio
async def test_normal_exit_clears_only_its_own_heartbeat(postgres_test_url):
    conn = await asyncpg.connect(postgres_test_url)
    original = await conn.fetchval("SELECT last_heartbeat_at FROM bot_state WHERE mode='TESTNET'")
    try:
        config = from_env({"DATABASE_URL": postgres_test_url})

        async def exit_normally():
            assert await PostgresRepository(conn).runtime_heartbeat("TESTNET") is not None

        await run_with_heartbeat(exit_normally, config, "TESTNET")
        assert await PostgresRepository(conn).runtime_heartbeat("TESTNET") is None

        async def another_process_takes_over():
            await conn.execute("UPDATE bot_state SET last_heartbeat_at=$1 WHERE mode='TESTNET'", NOW)

        await run_with_heartbeat(another_process_takes_over, config, "TESTNET")
        assert await PostgresRepository(conn).runtime_heartbeat("TESTNET") == NOW
    finally:
        await conn.execute("UPDATE bot_state SET last_heartbeat_at=$1 WHERE mode='TESTNET'", original)
        await conn.close()


@pytest.mark.postgres_integration
@pytest.mark.asyncio
async def test_real_engine_process_death_is_detectable_without_new_cycle(postgres_test_url):
    from scripts.watchdog import read_observation

    conn = await asyncpg.connect(postgres_test_url)
    original = await conn.fetchval("SELECT last_heartbeat_at FROM bot_state WHERE mode='TESTNET'")
    child = subprocess.Popen(
        [sys.executable, "-c", """
import asyncio, sys
from app.core.db import from_env
from app.runtime.heartbeat import run_with_heartbeat
async def idle():
    print('READY', flush=True)
    await asyncio.Event().wait()
asyncio.run(run_with_heartbeat(idle, from_env({'DATABASE_URL':sys.argv[1]}), 'TESTNET'))
""", postgres_test_url], cwd=Path(__file__).resolve().parents[1],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        assert (await asyncio.wait_for(asyncio.to_thread(child.stdout.readline), 10)).strip() == "READY"
        at = await PostgresRepository(conn).runtime_heartbeat("TESTNET")
        child.kill()
        child.wait(timeout=5)
        # Le surveillant reste frais, sans nouveau cycle : seul le moteur manque.
        later = at + timedelta(seconds=31)
        await conn.execute(
            "INSERT INTO protection_observations(mode,observed_at,body) VALUES('TESTNET',$1,$2::jsonb)",
            later, json.dumps({"complete": True, "issues": 0, "runtime_expected": True}),
        )
        row = await read_observation(from_env({"DATABASE_URL": postgres_test_url}), "TESTNET")
        assert classify(row, now=later, max_age_seconds=35)["reason"] == "RUNTIME_HEARTBEAT_STALE"
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        child.stdout.close()
        child.stderr.close()
        await conn.execute("DELETE FROM protection_observations WHERE mode='TESTNET'")
        await conn.execute("UPDATE bot_state SET last_heartbeat_at=$1 WHERE mode='TESTNET'", original)
        await conn.close()


@pytest.mark.postgres_integration
@pytest.mark.asyncio
async def test_database_session_loss_stops_the_engine_worker(postgres_test_url):
    admin = await asyncpg.connect(postgres_test_url)
    original = await PostgresRepository(admin).runtime_heartbeat("TESTNET")
    heartbeat_connection = None
    cancelled = False

    async def connect(**kwargs):
        nonlocal heartbeat_connection
        heartbeat_connection = await asyncpg.connect(**kwargs)
        return heartbeat_connection

    async def idle():
        nonlocal cancelled
        try:
            await admin.fetchval("SELECT pg_terminate_backend($1)", heartbeat_connection.get_server_pid())
            await asyncio.Event().wait()
        finally:
            cancelled = True

    try:
        with pytest.raises(asyncpg.InterfaceError):
            await asyncio.wait_for(run_with_heartbeat(idle, from_env({"DATABASE_URL": postgres_test_url}),
                                                       "TESTNET", interval=.01, connect=connect), 2)
        assert cancelled
    finally:
        await admin.execute("UPDATE bot_state SET last_heartbeat_at=$1 WHERE mode='TESTNET'", original)
        await admin.close()


@pytest.mark.parametrize("serve", [True, False])
@pytest.mark.asyncio
async def test_main_wraps_all_engine_work_in_heartbeat(serve, monkeypatch):
    from types import SimpleNamespace
    from app import main as main_module
    from tests.test_insights import config

    calls = []

    class Repo:
        async def close(self):
            calls.append("closed")

    async def connect(config):
        return Repo()

    async def wrapped(work, database, mode):
        calls.append(("heartbeat", mode))
        await work()

    async def served(*args, **kwargs):
        calls.append("serve")

    async def once():
        calls.append("cycle")
        return SimpleNamespace(elapsed_ms=0, lines=lambda: [])

    monkeypatch.setattr(main_module, "load_settings", lambda **kwargs: config(DATABASE_URL="postgresql://postgres:test@127.0.0.1/test?sslmode=disable"))
    monkeypatch.setattr(main_module.PostgresRepository, "connect", connect)
    monkeypatch.setattr(main_module, "build_exchange", lambda **kwargs: SimpleNamespace(load_markets=lambda: None))
    monkeypatch.setattr(main_module, "make_broker", lambda *args, **kwargs: object())
    monkeypatch.setattr(main_module, "Orchestrator", lambda *args, **kwargs: SimpleNamespace(run_once=once))
    monkeypatch.setattr(main_module, "run_with_heartbeat", wrapped)
    monkeypatch.setattr(main_module, "_serve", served)
    assert await main_module._run(SimpleNamespace(config=None, status=False, no_warmup=True,
                                                  serve=serve, serve_cycles=0, cycles=1)) == 0
    assert calls == [("heartbeat", "PAPER"), "serve" if serve else "cycle", "closed"]
