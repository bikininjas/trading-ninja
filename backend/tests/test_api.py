"""Tests de l'API d'administration : session, pause, panic-sell, WebSocket."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pyotp
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.api.main import create_app
from app.api.security import create_token, hash_password
from app.core.settings import load_settings
from app.db.repository import AccountSnapshot, StateTransitionError

SECRET = "JBSWY3DPEHPK3PXP"
PASSWORD = "correct horse battery"
PASSWORD_HASH = hash_password(PASSWORD)
JWT_SECRET = "k" * 48


class FakeRepo:
    def __init__(self) -> None:
        self.current_state = "IDLE"
        self.locks: list[str] = []
        self.resumes: list[str] = []
        self.position = None

    async def state(self, mode):
        return self.current_state, None

    async def account(self, mode, starting_equity):
        return AccountSnapshot(
            equity=Decimal("9.37"), free_quote=Decimal("9.37"), hwm=Decimal("9.37"),
            daily_drawdown_pct=Decimal(0), weekly_drawdown_pct=Decimal(0),
            from_hwm_drawdown_pct=Decimal(0), consecutive_losses=0, orders_last_hour=0,
            has_open_position=False, reserve_quote=Decimal("0"),
        )

    async def open_position(self, mode):
        return self.position

    async def recent(self, table, mode, limit):
        if table not in {"llm_signals", "risk_evaluations", "positions", "orders"}:
            raise ValueError(f"table non exposée : {table}")
        return []

    async def force_lock(self, mode, reason):
        self.locks.append(reason)
        self.current_state = "LOCKED_CIRCUIT_BREAKER"

    async def force_resume(self, mode, reason, who="admin"):
        if self.current_state not in ("LOCKED_CIRCUIT_BREAKER", "EMERGENCY_HALT"):
            raise StateTransitionError("reprise refusée : le bot n'est pas bloqué")
        self.resumes.append(reason)
        self.current_state = "IDLE"


@pytest.fixture
def api(tmp_path):
    repo = FakeRepo()
    calls: list[str] = []

    async def liquidate(repo, config):
        calls.append("liquidated")
        return []

    settings = load_settings(
        env_file=tmp_path / "absent.env",
        overrides={
            "TRADING_MODE": "paper",
            "EXCHANGE_SANDBOX_MODE": "true",
            "TRADING_SYMBOL": "BTC/EUR",
            "PAPER_START_EQUITY": "9.37",
            "JWT_SECRET_KEY": JWT_SECRET,
            "TOTP_SECRET": SECRET,
            "ADMIN_PASSWORD_HASH": PASSWORD_HASH,
        },
    )
    app = create_app(settings, repo=repo, liquidate=liquidate)
    with TestClient(app) as client:
        yield client, repo, calls


def login(client, password: str = PASSWORD, code: str | None = None):
    return client.post("/api/auth/login", json={
        "password": password,
        "totp": code or pyotp.TOTP(SECRET).now(),
    })


def token_of(client) -> str:
    response = login(client)
    assert response.status_code == 200, response.text
    return response.json()["token"]


# ---------------------------------------------------------------------------
# Authentification
# ---------------------------------------------------------------------------


def test_login_requires_both_factors(api):
    client, _, _ = api
    assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 422
    assert login(client, password="mauvais").status_code == 401
    assert login(client, code="000000").status_code == 401


def test_login_returns_a_fifteen_minute_token(api):
    client, _, _ = api
    body = login(client).json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 900
    assert body["token"]


def test_status_requires_a_valid_session(api):
    client, _, _ = api
    assert client.get("/api/status").status_code == 401
    assert client.get("/api/status", headers={"Authorization": "Bearer faux"}).status_code == 401

    expired = create_token(JWT_SECRET, ttl_seconds=-10)
    assert client.get("/api/status", headers={"Authorization": f"Bearer {expired}"}).status_code == 401


def test_status_describes_the_bot(api):
    client, _, _ = api
    response = client.get("/api/status", headers={"Authorization": f"Bearer {token_of(client)}"})
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "PAPER"
    assert body["state"] == "IDLE"
    assert body["equity"] == "9.37"
    assert body["position"] is None


def test_too_many_failed_logins_are_blocked(api):
    client, _, _ = api
    for _ in range(5):
        assert login(client, password="mauvais").status_code == 401
    assert login(client, password="mauvais").status_code == 429


# ---------------------------------------------------------------------------
# Contrôle
# ---------------------------------------------------------------------------


def test_pause_then_resume(api):
    client, repo, _ = api
    headers = {"Authorization": f"Bearer {token_of(client)}"}

    paused = client.post("/api/control/pause", headers=headers)
    assert paused.status_code == 200
    assert paused.json()["state"] == "LOCKED_CIRCUIT_BREAKER"
    assert repo.locks

    resumed = client.post("/api/control/resume", headers=headers)
    assert resumed.status_code == 200
    assert resumed.json()["state"] == "IDLE"
    assert repo.resumes


def test_pause_is_refused_with_an_open_position(api):
    client, repo, _ = api

    class OpenPosition:
        symbol = "BTC/EUR"

    repo.position = OpenPosition()
    response = client.post("/api/control/pause",
                           headers={"Authorization": f"Bearer {token_of(client)}"})
    assert response.status_code == 409
    assert "position ouverte" in response.json()["detail"]
    assert repo.locks == []


def test_resume_is_refused_when_the_bot_is_not_stopped(api):
    client, _, _ = api
    response = client.post("/api/control/resume",
                           headers={"Authorization": f"Bearer {token_of(client)}"})
    assert response.status_code == 409


def test_panic_requires_a_fresh_totp(api):
    client, _, calls = api
    used_code = pyotp.TOTP(SECRET).now()
    headers = {"Authorization": f"Bearer {token_of(client)}"}

    replayed = client.post("/api/control/panic", json={"totp": used_code}, headers=headers)
    assert replayed.status_code == 403
    assert calls == []

    next_code = pyotp.TOTP(SECRET).at(
        datetime.now(timezone.utc) + timedelta(seconds=30)
    )
    accepted = client.post("/api/control/panic", json={"totp": next_code}, headers=headers)
    assert accepted.status_code == 200
    assert calls == ["liquidated"]
    assert accepted.json()["liquidated"] == []


def test_history_rejects_unknown_tables(api):
    client, _, _ = api
    headers = {"Authorization": f"Bearer {token_of(client)}"}
    assert client.get("/api/history/passwords", headers=headers).status_code == 400
    assert client.get("/api/history/llm_signals", headers=headers).status_code == 200


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------


def test_websocket_refuses_a_bad_token(api):
    client, _, _ = api
    with client.websocket_connect("/ws/status?interval=1") as ws:
        ws.send_json({"token": "faux"})
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


def test_websocket_streams_the_status(api):
    client, _, _ = api
    token = token_of(client)
    with client.websocket_connect("/ws/status?interval=1") as ws:
        ws.send_json({"token": token})
        payload = ws.receive_json()
        assert payload["mode"] == "PAPER"
        assert payload["equity"] == "9.37"
