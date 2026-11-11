"""Parcours administratifs privés : observation, révisions et aucune exécution."""

from datetime import datetime, timedelta, timezone
import uuid

from fastapi.testclient import TestClient
import pyotp
import pytest

from app.api.main import create_app
from app.api.security import create_token, hash_password
from tests.test_insights import config, FeatureLedger, NOW


@pytest.fixture
def api():
    settings = config(
        JWT_SECRET_KEY="j" * 48,
        TOTP_SECRET="JBSWY3DPEHPK3PXP",
        ADMIN_PASSWORD_HASH=hash_password("fake"),
    )
    ledger = FeatureLedger()
    app = create_app(
        settings,
        repo=ledger,
        settings_loader=lambda overrides=None: config(**(overrides or {})),
    )
    with TestClient(app) as client:
        yield (
            client,
            ledger,
            settings,
            {"Authorization": "Bearer " + create_token(settings.jwt_secret)},
        )


def test_observation_routes_are_private(api):
    client, ledger, settings, headers = api
    for path in [
        "/api/insights/overview",
        "/api/insights/cycles",
        "/api/insights/protection",
        "/api/insights/policy",
    ]:
        assert client.get(path).status_code == 401
        assert client.get(path, headers=headers).status_code == 200


def test_entry_permission_changes_do_not_resume_emergency_halts(api):
    client, ledger, settings, headers = api
    first = client.post(
        "/api/control/entries",
        headers=headers,
        json={"enabled": False, "expected_revision": 0},
    )
    assert first.status_code == 200 and ledger.control["enabled"] is False
    assert (
        client.post(
            "/api/control/entries",
            headers=headers,
            json={"enabled": True, "expected_revision": 1},
        ).status_code
        == 403
    )
    ledger.current = "EMERGENCY_HALT"
    response = client.post(
        "/api/control/entries",
        headers=headers,
        json={
            "enabled": True,
            "expected_revision": 1,
            "totp": pyotp.TOTP(settings.totp_secret).now(),
        },
    )
    assert response.status_code == 409 and ledger.current == "EMERGENCY_HALT"


def test_entry_revisions_and_boolean_type_are_validated(api):
    client, ledger, settings, headers = api
    assert (
        client.post(
            "/api/control/entries",
            headers=headers,
            json={"enabled": "false", "expected_revision": 0},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/control/entries",
            headers=headers,
            json={"enabled": False, "expected_revision": 42},
        ).status_code
        == 409
    )
    assert ledger.control["enabled"]


def test_preview_shows_position_policy_scope_and_rejects_credentials(api):
    client, ledger, settings, headers = api
    good = client.post(
        "/api/settings/preview",
        headers=headers,
        json={"expected_revision": 0, "update": {"confidence_floor": ".7"}},
    )
    assert good.status_code == 200 and "min_confidence" in good.json()["changes"]
    assert (
        client.post(
            "/api/settings/preview",
            headers=headers,
            json={"expected_revision": 1, "update": {}},
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/settings/preview",
            headers=headers,
            json={"expected_revision": 0, "update": {"EXCHANGE_API_KEY": "unsafe"}},
        ).status_code
        == 422
    )


def test_unreplayable_packet_is_a_clear_refusal_not_a_success(api):
    client, ledger, settings, headers = api
    response = client.post(
        "/api/insights/replay",
        headers=headers,
        json={"schema_version": 1, "status": "RUNNING"},
    )
    assert (
        response.status_code == 422
        and response.json()["detail"]["status"] == "UNREPLAYABLE"
    )
    missing = client.get("/api/insights/cycles/" + str(uuid.uuid4()), headers=headers)
    assert missing.status_code == 404


def test_diagnostic_ids_and_time_windows_are_bounded(api):
    client, ledger, settings, headers = api
    assert (
        client.get("/api/positions/invalid/diagnostics", headers=headers).status_code
        == 422
    )
    base = "/api/diagnostics/opportunities"
    assert (
        client.get(
            base,
            headers=headers,
            params={"since": "2026-01-01T00:00:00", "until": "2026-01-02T00:00:00"},
        ).status_code
        == 422
    )
    assert (
        client.get(
            base,
            headers=headers,
            params={"since": "2026-01-01T00:00:00Z", "until": "2026-12-01T00:00:00Z"},
        ).status_code
        == 422
    )


def test_old_protection_observation_never_looks_fresh(api):
    client, ledger, settings, headers = api
    ledger.protection = {
        "latest": {
            "observed_at": datetime.now(timezone.utc) - timedelta(hours=1),
            "body": {"status": "PROTECTED"},
        },
        "incidents": [],
    }
    result = client.get("/api/insights/protection", headers=headers)
    assert result.status_code == 200 and result.json()["freshness"] == "STALE"


@pytest.mark.parametrize("problem", ["dead_runtime", "missing_runtime", "incomplete", "incident", "not_monitored", "invalid", "notification_pending", "notification_unknown"])
def test_recent_observation_is_not_green_without_all_proofs(api, problem):
    client, ledger, settings, headers = api
    now = datetime.now(timezone.utc)
    body = {"complete": True, "issues": 0, "runtime_expected": True,
            "notification_pending": False, "runtime_timeframe": "4h"}
    heartbeat = now
    if problem == "dead_runtime":
        heartbeat -= timedelta(seconds=31)
    elif problem == "missing_runtime":
        heartbeat = None
    elif problem == "incomplete":
        body["complete"] = False
    elif problem == "incident":
        body["issues"] = 1
    elif problem == "not_monitored":
        body["runtime_expected"] = False
    elif problem == "invalid":
        body = {"complete": "true"}
    elif problem == "notification_pending":
        body["notification_pending"] = True
    elif problem == "notification_unknown":
        del body["notification_pending"]
    ledger.protection = {"latest": {"observed_at": now, "body": body},
                         "incidents": [], "runtime_heartbeat_at": heartbeat,
                         "runtime_cycle_started_at": now, "runtime_cycle_status": "COMPLETE"}
    result = client.get("/api/insights/protection", headers=headers).json()
    assert result["status"] != "OK" and result["freshness"] != "FRESH"


def test_api_and_probe_share_the_same_healthy_verdict(api):
    client, ledger, settings, headers = api
    now = datetime.now(timezone.utc)
    ledger.protection = {"latest": {"observed_at": now,
                                     "body": {"complete": True, "issues": 0, "runtime_expected": True,
                                              "notification_pending": False, "runtime_timeframe": "4h"}},
                         "incidents": [], "runtime_heartbeat_at": now,
                         "runtime_cycle_started_at": now, "runtime_cycle_status": "COMPLETE"}
    result = client.get("/api/insights/protection", headers=headers).json()
    assert result["status"] == "OK" and result["freshness"] == "FRESH"


def test_api_expires_healthy_running_cycle_at_its_progress_deadline(api):
    client, ledger, settings, headers = api
    now = datetime.now(timezone.utc)
    ledger.protection = {"latest": {"observed_at": now,
                                     "body": {"complete": True, "issues": 0, "runtime_expected": True,
                                              "notification_pending": False, "runtime_timeframe": "4h"}},
                         "incidents": [], "runtime_heartbeat_at": now,
                         "runtime_cycle_started_at": now - timedelta(seconds=20), "runtime_cycle_status": "RUNNING"}
    result = client.get("/api/insights/protection", headers=headers).json()
    assert result["status"] == "OK"
    deadline = datetime.fromisoformat(result["valid_until"])
    assert deadline == now + timedelta(seconds=10)


def test_legacy_policy_adoption_requires_fresh_totp_and_expected_fingerprint(api):
    from app.db.repository import PositionRow
    from decimal import Decimal as D

    client, ledger, settings, headers = api
    pid = uuid.uuid4()
    ledger.position = PositionRow(
        pid, "BTC/EUR", D(100), D(1), D(95), D(110), NOW, D(5)
    )
    response = client.post(
        f"/api/control/positions/{pid}/adopt-policy",
        headers=headers,
        json={
            "expected_policy_id": "0" * 64,
            "totp": pyotp.TOTP(settings.totp_secret).now(),
        },
    )
    assert response.status_code == 409 and not ledger.policies_by_position


def test_invalid_experiment_never_starts_a_financial_action(api):
    client, ledger, settings, headers = api
    result = client.post(
        "/api/insights/experiments",
        headers=headers,
        json={"schema_version": 1, "variants": []},
    )
    assert result.status_code == 422 and ledger.position is None and ledger.orders == []


def test_learning_status_is_private_read_only_and_tracks_service_freshness(api):
    client,ledger,settings,headers=api
    assert client.get('/api/insights/learning').status_code==401
    async def status():return {'run':None,'simulations':{'total':0},'history':[]}
    ledger.learning_status=status
    response=client.get('/api/insights/learning',headers=headers)
    assert response.status_code==200 and response.json()['state']=='NOT_STARTED'
    assert response.json()['policy']['min_samples']==20
    assert client.post('/api/insights/learning',headers=headers).status_code==405
