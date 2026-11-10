"""Preuves de reprise : disque, arrêt brutal, récidive et réponse perdue."""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from app.insights.notifications import NotificationOutbox
from scripts.watchdog import check

NOW = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)
INCIDENT = {"observation": {"observed_at": NOW}, "issues": [{"key": "STOP:1"}]}
CLEAR = {"observation": {"observed_at": NOW}, "issues": []}
DB_ENV = {"DATABASE_URL": "postgresql://postgres:test@127.0.0.1/test?sslmode=disable"}


@pytest.mark.asyncio
async def test_committed_incident_survives_real_sigkill_and_resolution(tmp_path):
    path = tmp_path / "alerts.sqlite3"
    child = subprocess.Popen(
        [sys.executable, "-c", """
import sys
from app.insights.notifications import NotificationOutbox
outbox = NotificationOutbox(sys.argv[1], source='test', mode='PAPER')
outbox.observe({'observation': {'observed_at': 'original'}, 'issues': [{'key': 'STOP:1'}]})
print('COMMITTED', flush=True)
sys.stdin.readline()
""", str(path)], cwd=Path(__file__).resolve().parents[1],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        ready = await asyncio.wait_for(asyncio.to_thread(child.stdout.readline), 10)
        assert ready.strip() == "COMMITTED"
        child.kill()
        child.wait(timeout=5)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        for stream in (child.stdin, child.stdout, child.stderr):
            stream.close()
    sent = []

    async def send(url, payload):
        sent.append(payload)

    with NotificationOutbox(path, source="test", mode="PAPER") as outbox:
        outbox.observe(CLEAR)
        await outbox.deliver("https://alerts.example", CLEAR, send)
        assert not outbox.pending
    assert sent[0]["pending_incidents"][0]["observed_at"] == "original"
    assert sent[0]["issues"] == []


@pytest.mark.asyncio
async def test_lost_reply_retries_same_id_after_restart_and_deduplicates_ack(tmp_path):
    path = tmp_path / "alerts.sqlite3"
    sent = []

    async def lost_reply(url, payload):
        sent.append(payload)
        raise TimeoutError("lost reply after recipient accepted")

    async def send(url, payload):
        sent.append(payload)

    with NotificationOutbox(path, source="test", mode="PAPER") as outbox:
        outbox.observe(INCIDENT)
        with pytest.raises(TimeoutError):
            await outbox.deliver("https://alerts.example", INCIDENT, lost_reply)
    with NotificationOutbox(path, source="test", mode="PAPER") as outbox:
        outbox.observe(INCIDENT)
        await outbox.deliver("https://alerts.example", INCIDENT, send)
    with NotificationOutbox(path, source="test", mode="PAPER") as outbox:
        outbox.observe(INCIDENT)
        assert not outbox.pending
    assert len(sent) == 2
    assert sent[0]["pending_incidents"] == sent[1]["pending_incidents"]


@pytest.mark.asyncio
async def test_recurrence_does_not_overwrite_undelivered_episode(tmp_path):
    sent = []

    async def send(url, payload):
        sent.append(payload)

    with NotificationOutbox(tmp_path / "alerts.sqlite3", source="test", mode="PAPER") as outbox:
        outbox.observe(INCIDENT)
        outbox.observe(CLEAR)
        outbox.observe(INCIDENT)
        await outbox.deliver("https://alerts.example", INCIDENT, send)
    events = sent[0]["pending_incidents"]
    assert len(events) == 2 and events[0]["event_id"] != events[1]["event_id"]


def test_queue_excludes_concurrent_process_and_wrong_mode(tmp_path):
    path = tmp_path / "alerts.sqlite3"
    with NotificationOutbox(path, source="test", mode="PAPER"):
        with pytest.raises(BlockingIOError):
            NotificationOutbox(path, source="test", mode="PAPER")
        assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError, match="autre source"):
        NotificationOutbox(path, source="test", mode="LIVE")


def test_corrupt_spool_is_not_silently_reinitialized(tmp_path):
    path = tmp_path / "alerts.sqlite3"
    path.write_bytes(b"invalid database" * 100)
    import sqlite3

    with pytest.raises(sqlite3.DatabaseError):
        NotificationOutbox(path, source="test", mode="PAPER")
    assert path.read_bytes().startswith(b"invalid database")


@pytest.mark.asyncio
async def test_watchdog_retries_database_outage_after_restart_and_recovery(tmp_path):
    path = tmp_path / "alerts.sqlite3"

    async def failed_read(config, mode):
        raise OSError("secret")

    async def healthy_read(config, mode):
        return {"observed_at": NOW, "runtime_heartbeat_at": NOW,
                "body": {"complete": True, "issues": 0, "runtime_expected": True, "notification_pending": False}}

    calls = []

    async def fail(url, payload):
        calls.append(payload)
        raise TimeoutError("private URL")

    async def send(url, payload):
        calls.append(payload)

    kwargs = dict(interval=15, webhook="https://alerts.example", now=NOW,
                  env=DB_ENV, notification_state=path)
    first = await check("PAPER", read=failed_read, notify=fail, **kwargs)
    second = await check("PAPER", read=healthy_read, notify=fail, **kwargs)
    third = await check("PAPER", read=healthy_read, notify=send, **kwargs)
    assert first["reason"] == "DATABASE_UNAVAILABLE"
    assert second["status"] == "ALERT" and second["reason"] == "NOTIFICATION_PENDING"
    assert third["status"] == "OK" and third["notification"] == "SENT"
    assert calls[0]["pending_incidents"] == calls[2]["pending_incidents"]
    assert "secret" not in str(calls) and "private URL" not in str(second)


@pytest.mark.asyncio
async def test_observer_database_connect_failure_is_durable_and_alerts(tmp_path, monkeypatch):
    from app.core.settings import load_settings
    from app.db.repository import PostgresRepository
    from app.insights import watch as watch_module
    from scripts.insights import watch

    async def failed_connect(config):
        raise OSError("secret database")

    calls = []

    async def failed_send(url, payload):
        calls.append(payload)
        raise TimeoutError("private URL")

    monkeypatch.setattr(PostgresRepository, "connect", failed_connect)
    monkeypatch.setattr(watch_module, "notify_incident", failed_send)
    config = load_settings(env_file=tmp_path / "absent.env", overrides={"TRADING_MODE": "paper", **DB_ENV})
    path = tmp_path / "alerts.sqlite3"
    args = SimpleNamespace(notification_state=path, webhook="https://alerts.example",
                           expect_runtime=True, serve=False)
    result = await watch(config, args)
    assert result == {"status": "ALERT", "issues": 1}
    with NotificationOutbox(path, source="observer", mode="PAPER") as outbox:
        assert outbox.pending
    assert calls[0]["new_incidents"] == ["OBSERVATION_UNKNOWN"]
    assert "secret database" not in str(calls)


@pytest.mark.asyncio
async def test_incident_is_durable_before_remote_persistence_fails(tmp_path):
    from app.insights.watch import observe_protection

    class FailedStore:
        async def runtime_heartbeat(self, mode):
            return NOW

        async def open_positions(self, mode):
            return []

        async def recent_cycles(self, mode, limit):
            return []

        async def record_protection(self, *args):
            raise OSError("database lost after observation")

    path = tmp_path / "alerts.sqlite3"
    with NotificationOutbox(path, source="test", mode="PAPER") as outbox:
        with pytest.raises(OSError):
            await observe_protection(FailedStore(), None, "PAPER", now=NOW,
                                     expect_runtime=True, on_observation=outbox.observe)
    with NotificationOutbox(path, source="test", mode="PAPER") as outbox:
        assert outbox.pending


@pytest.mark.asyncio
async def test_observer_pending_delivery_remains_visible_after_incident_resolves(tmp_path, monkeypatch):
    from copy import deepcopy
    from datetime import timedelta
    from app.db.repository import PostgresRepository
    from app.insights import watch as watch_module
    from app.insights.health import classify
    from scripts.insights import watch
    from tests.test_insights import FeatureLedger, config

    class Store(FeatureLedger):
        alive = False

        async def runtime_heartbeat(self, mode):
            return datetime.now(timezone.utc) - timedelta(seconds=0 if self.alive else 31)

        async def recent_cycles(self, *args):
            return [{"status": "COMPLETE", "started_at": datetime.now(timezone.utc) - timedelta(hours=1)}]

        async def record_protection(self, mode, observation, issues):
            self.latest = {"observed_at": observation["observed_at"], "body": deepcopy(observation)}
            return True

        async def protection_notification_state(self, mode, at, pending):
            assert at == self.latest["observed_at"]
            self.latest["body"]["notification_pending"] = pending

        async def close(self):
            pass

    store = Store()

    async def connect(config):
        return store

    calls = []

    async def fail(url, payload):
        calls.append(payload)
        raise TimeoutError("webhook unavailable")

    async def send(url, payload):
        calls.append(payload)

    monkeypatch.setattr(PostgresRepository, "connect", connect)
    monkeypatch.setattr(watch_module, "notify_incident", fail)
    settings = config(**DB_ENV)
    args = SimpleNamespace(notification_state=tmp_path / "alerts.sqlite3",
                           webhook="https://alerts.example", expect_runtime=True, serve=False)
    assert (await watch(settings, args))["status"] == "ALERT"
    store.alive = True
    assert (await watch(settings, args))["status"] == "ALERT"
    record = {**store.latest, "runtime_heartbeat_at": datetime.now(timezone.utc)}
    assert record["body"]["issues"] == 0
    assert classify(record, now=datetime.now(timezone.utc), max_age_seconds=35)["reason"] == "OBSERVER_NOTIFICATION_PENDING"
    monkeypatch.setattr(watch_module, "notify_incident", send)
    assert (await watch(settings, args))["status"] == "OBSERVED"
    record = {**store.latest, "runtime_heartbeat_at": datetime.now(timezone.utc)}
    assert classify(record, now=datetime.now(timezone.utc), max_age_seconds=35)["status"] == "OK"
    assert calls[0]["pending_incidents"] == calls[2]["pending_incidents"]


@pytest.mark.postgres_integration
@pytest.mark.asyncio
async def test_late_webhook_response_cannot_clear_newer_observation(postgres_test_url):
    import asyncpg
    import json
    from datetime import timedelta
    from app.db.repository import PostgresRepository

    conn = await asyncpg.connect(postgres_test_url)
    tx = conn.transaction()
    await tx.start()
    try:
        await conn.execute("INSERT INTO protection_observations(mode,observed_at,body) VALUES('TESTNET',$1,$2::jsonb)",
                           NOW, json.dumps({"notification_pending": True}))
        repo = PostgresRepository(conn)
        await repo.protection_notification_state("TESTNET", NOW - timedelta(seconds=1), False)
        body = await conn.fetchval("SELECT body FROM protection_observations WHERE mode='TESTNET'")
        assert json.loads(body)["notification_pending"] is True
        await repo.protection_notification_state("TESTNET", NOW, False)
        body = await conn.fetchval("SELECT body FROM protection_observations WHERE mode='TESTNET'")
        assert json.loads(body)["notification_pending"] is False
    finally:
        await tx.rollback()
        await conn.close()
