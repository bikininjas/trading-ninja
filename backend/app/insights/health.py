"""Verdict commun API/sonde : une preuve inconnue ne vaut jamais santé."""

from datetime import datetime
import json

from app.runtime.heartbeat import MAX_AGE_SECONDS


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
    return {"status": "OK", "reason": "OBSERVER_CURRENT"}
