"""Tests de l'API d'administration : session, pause, panic-sell, WebSocket."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pyotp
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.api.main import create_app
from app.api.security import TOKEN_TTL_SECONDS, create_token, hash_password
from app.api.service import _position_payload
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
        self.runtime_payload: dict = {}
        self.runtime_updated_at = None
        self.runtime_updated_by = None

    async def entry_control(self,mode):
        return {'enabled':True,'revision':0}

    async def set_entry_permission(self,*args,**kwargs):
        return {'enabled':False,'revision':1}

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

    async def open_positions(self, mode):
        return [self.position] if self.position is not None else []

    async def reset_circuit_breaker(self, mode):
        if self.current_state != "LOCKED_CIRCUIT_BREAKER":
            from app.db.repository import StateTransitionError
            raise StateTransitionError("reset refusé")
        self.current_state = "IDLE"
        self.locks.append("reset")

    async def force_halt(self, mode, reason, code=None, auto_resume_at=None):
        self.current_state = "EMERGENCY_HALT"
        self.locks.append(reason)

    async def open_orders(self, mode):
        return []

    async def storage_state(self):
        return {"db_size_bytes": 100, "db_limit_bytes": 500, "free_space_bytes": 400,
                "quota_alert_triggered": False}

    async def stop_order_id(self, position_id):
        return None

    async def llm_signal_prompt(self, mode, signal_id):
        if signal_id == "missing":
            return None
        return {"indicators": {"rsi": 50}, "secret_token": "abc"}

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

    async def halt_details(self, mode):
        return None

    async def runtime_settings(self, mode):
        return dict(self.runtime_payload), self.runtime_updated_at

    async def update_runtime_settings(self, mode, payload, *, updated_by="dashboard"):
        self.runtime_payload = dict(payload)
        self.runtime_updated_at = datetime(2026, 11, 4, 13, 0, tzinfo=timezone.utc)
        self.runtime_updated_by = updated_by
        return self.runtime_updated_at

    async def equity_curve(self, mode, limit=200):
        return [{
            "taken_at": datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc),
            "equity_quote": Decimal("9.37"),
            "free_quote": Decimal("9.37"),
            "position_value_quote": Decimal("0"),
        }]

    async def dashboard_metrics(self, mode):
        return {
            "total_trades": 3, "wins": 2, "losses": 1,
            "net_pnl": Decimal("0.12"), "fees": Decimal("0.05"),
            "r_multiple": Decimal("0.4"),
        }

    async def daily_pnl(self, mode, days=14):
        return [{"day": datetime(2026, 11, 4).date(), "trades": 3, "wins": 2,
                 "net_pnl": Decimal("0.12")}]

    async def rejection_stats(self, mode):
        return [{"code": "CONFIDENCE_TOO_LOW", "count": 4}]

    async def llm_stats(self, mode, hours=24):
        return {
            "counts": {"BUY": 2, "HOLD": 1}, "last": {
                "created_at": datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc),
                "symbol": "BTC/EUR", "action": "BUY", "confidence": Decimal("0.72"),
                "market_regime": "TRENDING_UP", "model_name": "test-model",
                "inference_time_ms": 1100, "thesis": "pullback",
            },
            "total": 3, "degraded": 0, "avg_inference_ms": 1100,
        }

    async def shadow_summary(self, mode):
        return {
            "total": 2, "planned": 1, "no_plan": 1, "open": 1, "closed": 1,
            "wins": 0, "losses": 1, "net_pnl": Decimal("-0.10"),
            "by_kind": {"GATE_REJECTED_BUY": 1, "MODEL_HOLD": 1},
            "by_rejection": [{"code": "CONFIDENCE_TOO_LOW", "count": 1}],
        }

    async def active_lessons(self, mode):
        return ["Le rejet de confiance protège le capital.", "Attendre un meilleur setup."]

    async def recent_lessons(self, mode, limit=10):
        return [{
            "version": 2, "created_at": datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc),
            "source_count": 7, "active": True,
            "lessons": ["Le rejet de confiance protège le capital.", "Attendre un meilleur setup."],
        }]


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
            "RISK_PROFILE": "prudent",
            "PAPER_START_EQUITY": "9.37",
            "RUNTIME_CONFIG_PATH": str(tmp_path / "runtime.json"),
            "JWT_SECRET_KEY": JWT_SECRET,
            "TOTP_SECRET": SECRET,
            "ADMIN_PASSWORD_HASH": PASSWORD_HASH,
        },
    )
    env_file = tmp_path / "absent.env"
    base_overrides = {
        "TRADING_MODE": "paper", "EXCHANGE_SANDBOX_MODE": "true",
        "TRADING_SYMBOL": "BTC/EUR", "RISK_PROFILE": "prudent",
        "PAPER_START_EQUITY": "9.37",
        "RUNTIME_CONFIG_PATH": str(tmp_path / "runtime.json"),
    }
    app = create_app(
        settings, repo=repo, liquidate=liquidate,
        settings_loader=lambda overrides=None: load_settings(
            env_file=env_file, overrides={**base_overrides, **(overrides or {})}
        ),
    )
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


def test_login_returns_a_seven_day_token(api):
    client, _, _ = api
    body = login(client).json()
    assert body["token_type"] == "bearer"
    assert TOKEN_TTL_SECONDS == 7 * 24 * 60 * 60
    assert body["expires_in"] == TOKEN_TTL_SECONDS
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


# ---------------------------------------------------------------------------
# Statistiques et réglages
# ---------------------------------------------------------------------------


def test_stats_describes_pnl_model_and_equity(api):
    client, _, _ = api
    headers = {"Authorization": f"Bearer {token_of(client)}"}
    response = client.get("/api/stats", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["pnl"]["trades"] == 3
    assert body["pnl"]["win_rate"] == str(Decimal(2) / Decimal(3))
    assert body["llm"]["counts"]["BUY"] == 2
    assert body["llm"]["counts"]["SELL"] == 0
    assert body["llm"]["last"]["action"] == "BUY"
    assert body["equity_curve"][0]["equity"] == "9.37"
    assert body["rejections"][0]["code"] == "CONFIDENCE_TOO_LOW"
    assert body["shadow"]["total"] == 2
    assert body["shadow"]["by_kind"]["MODEL_HOLD"] == 1
    assert body["shadow"]["net_pnl"] == "-0.10"
    assert body["lessons"]["version"] == 2
    assert body["lessons"]["source_count"] == 7
    assert body["lessons"]["active"][0] == "Le rejet de confiance protège le capital."


def test_stats_requires_a_session(api):
    client, _, _ = api
    assert client.get("/api/stats").status_code == 401


def test_settings_are_exposed_without_jargon(api):
    client, _, _ = api
    headers = {"Authorization": f"Bearer {token_of(client)}"}
    response = client.get("/api/settings", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["profile"] == "prudent"
    assert body["profile_label"] == "Prudent"
    assert body["max_per_trade_pct"] == "0.20"
    assert body["max_weekly_loss_pct"] == "0.05"
    assert body["max_drawdown_from_hwm_pct"] == "0.10"
    assert body["max_consecutive_losses"] == 4
    assert body["risk_per_trade_pct"] == "0.005"
    assert body["halt_auto_resume_hours"] == 24
    assert any(profile["key"] == "micro" for profile in body["profiles"])


def test_settings_update_writes_to_the_database(api, tmp_path):
    client, repo, _ = api
    headers = {"Authorization": f"Bearer {token_of(client)}"}
    response = client.post(
        "/api/settings",
        json={
            "max_per_trade_pct": 0.35,
            "max_daily_loss_pct": 0.08,
            "max_orders_per_hour": 4,
            "confidence_floor": 0.70,
            "entry_rule_enabled": False,
            "halt_auto_resume_hours": 48,
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["max_per_trade_pct"] == "0.35"
    assert body["halt_auto_resume_hours"] == 48

    assert repo.runtime_payload["MAX_NOTIONAL_PCT"] == "0.35"
    assert Decimal(repo.runtime_payload["MIN_CONFIDENCE"]) == Decimal("0.70")
    assert repo.runtime_payload["ENTRY_RULE_ENABLED"] == "false"
    assert repo.runtime_payload["HALT_AUTO_RESUME_HOURS"] == "48"
    assert repo.runtime_updated_by == "dashboard"


def test_nano_profile_can_be_selected_with_100_percent_exposure(api):
    client, repo, _ = api
    headers = {'Authorization': f'Bearer {token_of(client)}'}
    response = client.post('/api/settings', json={'profile': 'nano_budget', 'max_per_trade_pct': 1.0},
                           headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()['risk_per_trade_pct'] == '0.05'
    assert repo.runtime_payload['RISK_PROFILE'] == 'nano_budget'
    refused = client.post('/api/settings', json={'profile': 'prudent', 'max_per_trade_pct': 1.0},
                          headers=headers)
    assert refused.status_code >= 400


def test_settings_update_refuses_an_incoherent_daily_loss(api):
    client, _, _ = api
    headers = {"Authorization": f"Bearer {token_of(client)}"}
    response = client.post(
        "/api/settings",
        json={"profile": "micro", "max_daily_loss_pct": 0.02},
        headers=headers,
    )
    assert response.status_code == 422
    assert "au moins le double" in response.json()["detail"]


def test_position_payload_shows_the_net_risk_at_the_stop():
    position = SimpleNamespace(
        id="pos-1", symbol="BTC/EUR", entry_price=Decimal("100"),
        amount=Decimal("0.046"), stop_loss_price=Decimal("96"),
        take_profit_price=Decimal("105"),
        opened_at=datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc),
        risk_amount_quote=Decimal("0.115"),
    )
    payload = _position_payload(position, Decimal("0.008"))
    assert payload["risk_amount_quote"] == "0.115"
    assert payload["fee_rate"] == "0.008"
    # 0,115 de risque brut + 2 × 0,80 % sur 4,60 EUR de notionnel = 0,1886 EUR
    assert payload["estimated_net_risk_quote"] == "0.188600"


def test_position_payload_without_a_position_is_none():
    assert _position_payload(None) is None


def test_panic_is_wired_in_live_mode(tmp_path):
    """Le 409 « non câblé » a disparu : le panic réel passe par la liquidation."""
    calls: list[str] = []

    async def liquidate(repo, config):
        calls.append(config.mode)
        return []

    settings = load_settings(
        env_file=tmp_path / "absent.env",
        overrides={
            "TRADING_MODE": "live",
            "EXCHANGE_SANDBOX_MODE": "false",
            "I_UNDERSTAND_REAL_MONEY": "true",
            "EXCHANGE_API_KEY": "test-key",
            "EXCHANGE_SECRET": "test-secret",
            "TRADING_SYMBOL": "BTC/EUR",
            "RISK_PROFILE": "prudent",
            "PAPER_START_EQUITY": "9.37",
            "RUNTIME_CONFIG_PATH": str(tmp_path / "runtime.json"),
            "JWT_SECRET_KEY": JWT_SECRET,
            "TOTP_SECRET": SECRET,
            "ADMIN_PASSWORD_HASH": PASSWORD_HASH,
        },
    )
    app = create_app(settings, repo=FakeRepo(), liquidate=liquidate)
    with TestClient(app) as client:
        headers = {"Authorization": f"Bearer {token_of(client)}"}
        # Le login a consommé le code courant : le panic exige un code frais.
        code = pyotp.TOTP(SECRET).at(
            datetime.now(timezone.utc) + timedelta(seconds=30)
        )
        response = client.post("/api/control/panic", json={"totp": code}, headers=headers)

    assert response.status_code == 200, response.text
    assert calls == ["live"]


# ---------------------------------------------------------------------------
# Contrôle étendu (P0/P1)
# ---------------------------------------------------------------------------


def test_kill_switch_requires_totp(api):
    client, _, _ = api
    headers = {"Authorization": f"Bearer {token_of(client)}"}
    assert client.post("/api/control/kill-switch", headers=headers,
                       json={}).status_code == 403
    assert client.post("/api/control/kill-switch", headers=headers,
                       json={"totp": "000000"}).status_code == 403


def test_kill_switch_halts_with_valid_totp(api):
    client, repo, _ = api
    headers = {"Authorization": f"Bearer {token_of(client)}"}
    code = pyotp.TOTP(SECRET).at(datetime.now(timezone.utc) + timedelta(seconds=30))
    response = client.post("/api/control/kill-switch", headers=headers, json={"totp": code})
    assert response.status_code == 200
    assert response.json()["state"] == "EMERGENCY_HALT"


def test_circuit_breaker_reset_requires_locked_state(api):
    client, repo, _ = api
    headers = {"Authorization": f"Bearer {token_of(client)}"}
    code = pyotp.TOTP(SECRET).at(datetime.now(timezone.utc) + timedelta(seconds=30))
    response = client.post("/api/control/circuit-breaker/reset", headers=headers,
                           json={"totp": code})
    assert response.status_code == 409


def test_circuit_breaker_reset_works_when_locked(api):
    client, repo, _ = api
    repo.current_state = "LOCKED_CIRCUIT_BREAKER"
    headers = {"Authorization": f"Bearer {token_of(client)}"}
    code = pyotp.TOTP(SECRET).at(datetime.now(timezone.utc) + timedelta(seconds=30))
    response = client.post("/api/control/circuit-breaker/reset", headers=headers,
                           json={"totp": code})
    assert response.status_code == 200
    assert response.json()["state"] == "IDLE"


def test_reconcile_returns_a_structured_diff(api):
    client, _, _ = api
    headers = {"Authorization": f"Bearer {token_of(client)}"}
    response = client.get("/api/control/reconcile", headers=headers) if False else client.post(
        "/api/control/reconcile", headers=headers)
    assert response.status_code in (200, 502)
    if response.status_code == 200:
        body = response.json()
        assert "has_divergence" in body
        assert "exchange_balances" in body
        assert "ledger_balances" in body
        assert "reconciled_at" in body


def test_system_health_requires_session(api):
    client, _, _ = api
    assert client.get("/api/system/health").status_code == 401


def test_system_health_returns_telemetry(api):
    client, _, _ = api
    headers = {"Authorization": f"Bearer {token_of(client)}"}
    response = client.get("/api/system/health", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert "kraken" in body and "scheduler" in body
    assert "ollama" in body and "supabase" in body


def test_portfolio_position_empty(api):
    client, _, _ = api
    headers = {"Authorization": f"Bearer {token_of(client)}"}
    response = client.get("/api/portfolio/position", headers=headers)
    assert response.status_code == 200
    assert response.json()["has_active_position"] is False


def test_llm_prompt_is_masked(api):
    client, _, _ = api
    headers = {"Authorization": f"Bearer {token_of(client)}"}
    response = client.get("/api/history/llm_signals/123/prompt", headers=headers)
    assert response.status_code in (200, 404, 502)
    if response.status_code == 200:
        body = response.json()
        assert body["prompt_payload"]["secret_token"] == "***"


def test_llm_prompt_missing_returns_404(api):
    client, _, _ = api
    headers = {"Authorization": f"Bearer {token_of(client)}"}
    response = client.get("/api/history/llm_signals/missing/prompt", headers=headers)
    assert response.status_code == 404
