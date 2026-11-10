"""Une surveillance morte ou illisible ne peut jamais être annoncée saine."""

from datetime import datetime, timedelta, timezone

import pytest

from scripts.watchdog import check, classify, main

NOW = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)
HEALTHY = {"complete": True, "issues": 0, "runtime_expected": True}
DB_ENV = {"DATABASE_URL": "postgresql://postgres:test@127.0.0.1/test?sslmode=disable"}


def observation(body=HEALTHY, at=NOW):
    return {"observed_at": at, "body": body}


@pytest.mark.parametrize(("record", "reason"), [
    (None, "OBSERVER_MISSING"),
    (observation(at=NOW - timedelta(seconds=36)), "OBSERVER_STALE"),
    (observation(at=NOW + timedelta(seconds=6)), "CLOCK_SKEW"),
    (observation(body="invalid json"), "INVALID_OBSERVATION"),
    (observation(body={**HEALTHY, "issues": True}), "INVALID_OBSERVATION"),
    (observation(body={**HEALTHY, "runtime_expected": False}), "RUNTIME_NOT_MONITORED"),
])
def test_missing_stale_or_invalid_information_is_not_healthy(record, reason):
    result = classify(record, now=NOW, max_age_seconds=35)
    assert result == {"status": "UNKNOWN", "reason": reason}


def test_incomplete_or_critical_observation_is_an_alert():
    assert classify(observation({**HEALTHY, "complete": False}), now=NOW,
                    max_age_seconds=35)["reason"] == "INCOMPLETE_OBSERVATION"
    assert classify(observation({**HEALTHY, "issues": 1}), now=NOW,
                    max_age_seconds=35)["reason"] == "ACTIVE_INCIDENT"
    assert classify(observation(), now=NOW, max_age_seconds=35)["status"] == "OK"


@pytest.mark.asyncio
async def test_database_outage_notifies_without_leaking_exception_text(tmp_path):
    sent = []

    async def failed_read(config, mode):
        raise OSError("sensitive database detail")

    async def send(url, payload):
        sent.append(payload)

    result = await check("LIVE", interval=15, webhook="https://alerts.example",
                         read=failed_read, notify=send, now=NOW, env=DB_ENV,
                         notification_state=tmp_path / "outbox.sqlite3")
    assert result["status"] == "UNKNOWN"
    assert result["reason"] == "DATABASE_UNAVAILABLE"
    assert result["notification"] == "SENT"
    assert sent[0]["source"] == "observer_watchdog"
    assert "sensitive" not in str(result) and "sensitive" not in str(sent)


@pytest.mark.asyncio
async def test_failed_notification_keeps_an_unhealthy_exit_state(tmp_path):
    async def read(config, mode):
        return observation(at=NOW - timedelta(minutes=2))

    async def fail(url, payload):
        raise TimeoutError("private URL")

    result = await check("PAPER", interval=15, webhook="https://alerts.example",
                         read=read, notify=fail, now=NOW, env=DB_ENV,
                         notification_state=tmp_path / "outbox.sqlite3")
    assert result["status"] == "UNKNOWN" and result["notification"] == "FAILED"
    assert "private URL" not in str(result)


def test_live_cannot_disable_runtime_monitoring(monkeypatch):
    monkeypatch.setenv("WATCHDOG_WEBHOOK_URL", "https://alerts.example")
    with pytest.raises(SystemExit):
        main(["--mode", "LIVE", "--allow-idle"])


@pytest.mark.postgres_integration
@pytest.mark.asyncio
async def test_watchdog_reads_a_real_observation_from_disposable_postgres(postgres_test_url):
    import asyncpg
    import json

    from app.core.db import from_env
    from scripts.watchdog import read_observation

    conn = await asyncpg.connect(postgres_test_url)
    try:
        await conn.execute(
            "INSERT INTO protection_observations(mode, observed_at, body) "
            "VALUES('TESTNET', $1, $2::jsonb)", NOW, json.dumps(HEALTHY)
        )
        row = await read_observation(from_env({"DATABASE_URL": postgres_test_url}), "TESTNET")
        assert classify(row, now=NOW, max_age_seconds=35)["status"] == "OK"
    finally:
        await conn.execute("DELETE FROM protection_observations WHERE mode='TESTNET'")
        await conn.close()
