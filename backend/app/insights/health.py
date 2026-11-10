"""Verdict commun API/sonde : une preuve inconnue ne vaut jamais santé."""

from datetime import datetime, timedelta
import json

from app.runtime.heartbeat import MAX_AGE_SECONDS

CYCLE_BUDGET_SECONDS = 30


def cycle_deadline(started_at, status, timeframe):
    from app.runtime.scheduler import next_candle_close, decision_delay_seconds

    if status == "RUNNING":
        return started_at + timedelta(seconds=CYCLE_BUDGET_SECONDS)
    return next_candle_close(started_at, timeframe) + timedelta(
        seconds=decision_delay_seconds(timeframe) + CYCLE_BUDGET_SECONDS
    )


def cycle_health(started_at, status, *, now, timeframe):
    if started_at is None or status is None:
        return {"status": "UNKNOWN", "reason": "RUNTIME_MISSING"}
    if (not isinstance(started_at, datetime) or started_at.utcoffset() is None
            or not isinstance(status, str) or not isinstance(timeframe, str)
            or timeframe not in {"1h", "4h", "1d"}):
        return {"status": "UNKNOWN", "reason": "RUNTIME_CYCLE_INVALID"}
    if (started_at - now).total_seconds() > 5:
        return {"status": "UNKNOWN", "reason": "RUNTIME_CLOCK_SKEW"}
    if status not in {"RUNNING", "COMPLETE"}:
        return {"status": "ALERT", "reason": "RUNTIME_INCOMPLETE"}
    if now > cycle_deadline(started_at, status, timeframe):
        return {"status": "ALERT", "reason": "RUNTIME_STALLED" if status == "RUNNING" else "RUNTIME_LATE"}
    return {"status": "OK", "reason": "RUNTIME_CYCLE_CURRENT"}


def heartbeat_health(at, *, now):
    if at is None:
        return {"status": "UNKNOWN", "reason": "RUNTIME_HEARTBEAT_MISSING"}
    if not isinstance(at, datetime) or at.utcoffset() is None:
        return {"status": "UNKNOWN", "reason": "RUNTIME_HEARTBEAT_INVALID"}
    age = (now - at).total_seconds()
    if age < -5:
        return {"status": "UNKNOWN", "reason": "RUNTIME_CLOCK_SKEW"}
    if age > MAX_AGE_SECONDS:
        return {"status": "UNKNOWN", "reason": "RUNTIME_HEARTBEAT_STALE"}
    return {"status": "OK", "reason": "RUNTIME_CURRENT"}


def classify(record, *, now, max_age_seconds, require_runtime=True):
    if not record:
        return {"status": "UNKNOWN", "reason": "OBSERVER_MISSING"}
    observed_at = record.get("observed_at")
    if not isinstance(observed_at, datetime) or observed_at.utcoffset() is None:
        return {"status": "UNKNOWN", "reason": "INVALID_TIMESTAMP"}
    age = (now - observed_at).total_seconds()
    if age < -5:
        return {"status": "UNKNOWN", "reason": "CLOCK_SKEW"}
    if age > max_age_seconds:
        return {"status": "UNKNOWN", "reason": "OBSERVER_STALE"}
    body = record.get("body")
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except (TypeError, ValueError):
            return {"status": "UNKNOWN", "reason": "INVALID_OBSERVATION"}
    if not isinstance(body, dict) or type(body.get("complete")) is not bool:
        return {"status": "UNKNOWN", "reason": "INVALID_OBSERVATION"}
    issues = body.get("issues")
    if type(issues) is not int or issues < 0:
        return {"status": "UNKNOWN", "reason": "INVALID_OBSERVATION"}
    if not body["complete"]:
        return {"status": "ALERT", "reason": "INCOMPLETE_OBSERVATION"}
    if issues:
        return {"status": "ALERT", "reason": "ACTIVE_INCIDENT", "issues": issues}
    if require_runtime:
        if body.get("runtime_expected") is not True:
            return {"status": "UNKNOWN", "reason": "RUNTIME_NOT_MONITORED"}
        runtime = heartbeat_health(record.get("runtime_heartbeat_at"), now=now)
        if runtime["status"] != "OK":
            return runtime
        progress = cycle_health(record.get("runtime_cycle_started_at"), record.get("runtime_cycle_status"),
                                now=now, timeframe=body.get("runtime_timeframe"))
        if progress["status"] != "OK":
            return progress
    if type(body.get("notification_pending")) is not bool:
        return {"status": "UNKNOWN", "reason": "OBSERVER_NOTIFICATION_UNKNOWN"}
    if body["notification_pending"]:
        return {"status": "ALERT", "reason": "OBSERVER_NOTIFICATION_PENDING"}
    return {"status": "OK", "reason": "OBSERVER_CURRENT"}
