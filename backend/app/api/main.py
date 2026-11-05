"""API d'administration — observation, pause, reprise, panic-sell.

    .venv/bin/python -m uvicorn app.api.main:create_app --factory \
        --host 127.0.0.1 --port 8080

Principes :

- l'API ne décide jamais d'acheter ; elle lit, arrête et liquide ;
- `/api/control/panic` exige un code TOTP **frais** en plus de la session ;
- la session dure 7 jours ; le dashboard ne pose qu'un cookie HttpOnly,
  SameSite=Strict, et ne transmet jamais le jeton au navigateur ;
- la documentation interactive (Swagger) est désactivée : une surface
  d'administration n'a pas à publier son propre mode d'emploi ;
- sans `JWT_SECRET_KEY`, `TOTP_SECRET` et `ADMIN_PASSWORD_HASH`, l'application
  refuse de démarrer (fail-closed).

Le panic-sell est câblé dans les deux modes : en papier il liquide le ledger, en
réel il annule tous les ordres de l'exchange puis vend au marché tout solde de
base détenu, avant de halter le bot.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict, replace
from decimal import Decimal
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from app.api.security import (
    TOKEN_TTL_SECONDS, AttemptLimiter, AuthError, TotpVerifier, create_token,
    decode_token, require_secrets, verify_password,
)
from app.api.service import ControlRefused, build_status, jsonable, pause, resume
from app.api.settings_store import (
    SettingsRefused, build_payload, friendly_settings, validate_payload,
)
from app.api.stats import build_stats
from app.core.settings import Settings, load_settings
from app.db.repository import PostgresRepository, StateTransitionError

__all__ = ["create_app"]


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=256)
    totp: str = Field(min_length=6, max_length=6)


class TotpRequest(BaseModel):
    totp: str = Field(min_length=6, max_length=6)


class SettingsUpdate(BaseModel):
    """Réglages du dashboard. Les pourcentages sont des fractions (0,50 = 50 %)."""

    profile: str | None = None
    max_per_trade_pct: float | None = Field(default=None, ge=0.05, le=0.50)
    max_daily_loss_pct: float | None = Field(default=None, ge=0.01, le=0.20)
    max_orders_per_hour: int | None = Field(default=None, ge=1, le=20)
    confidence_floor: float | None = Field(default=None, ge=0.40, le=0.95)
    entry_rule_enabled: bool | None = None
    halt_auto_resume_hours: int | None = Field(default=None, ge=0, le=168)


async def _default_liquidate(repo: PostgresRepository, config: Settings) -> list:
    """Liquidation d'urgence : ledger en papier, exchange réel en live.

    En live, l'exchange fait autorité : on annule tous les ordres, on vend tout
    solde de base (même non suivi en base), puis on écrit au ledger ce qui a
    réellement été exécuté.
    """
    from app.exchange.kraken import build_exchange
    from app.execution.factory import make_broker
    from app.execution.live import LiveBroker
    from app.runtime.recovery import liquidate_open_positions, record_panic_exits

    exchange = build_exchange(
        api_key=config.exchange.api_key, secret=config.exchange.secret
    )
    await asyncio.to_thread(exchange.load_markets)
    broker = make_broker(config, exchange, config.exchange.allowed_symbols)
    if isinstance(broker, LiveBroker):
        report = await broker.panic_sell(config.exchange.allowed_symbols)
        try:
            free_quote = await broker.free_quote()
        except Exception as exc:  # noqa: BLE001 — la halte doit avoir lieu même sans solde
            report = replace(
                report,
                errors=(*report.errors,
                        f"solde libre illisible : {type(exc).__name__}: {exc}"),
            )
            free_quote = None
        return await record_panic_exits(
            repo, report.exits, config.execution_mode,
            quote_currency=config.exchange.quote_currency,
            starting_equity=config.paper_start_equity,
            free_quote_after=free_quote,
            taker_fee=broker.fee(Decimal(1)),
            errors=list(report.errors),
        )
    return await liquidate_open_positions(
        repo, exchange, broker, config.execution_mode,
        quote_currency=config.exchange.quote_currency,
        starting_equity=config.paper_start_equity,
    )


def create_app(
    settings: Settings | None = None,
    *,
    repo: Any | None = None,
    liquidate: Any | None = None,
    settings_loader: Any | None = None,
) -> FastAPI:
    config = settings or load_settings()
    load = settings_loader or load_settings
    require_secrets(
        config.jwt_secret, config.totp_secret, config.admin_password_hash
    )

    totp = TotpVerifier(config.totp_secret)
    limiter = AttemptLimiter()
    liquidate_fn = liquidate or _default_liquidate
    owned_repo = repo is None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.repo = (
            await PostgresRepository.connect(config.database()) if owned_repo else repo
        )
        yield
        if owned_repo and app.state.repo is not None:
            await app.state.repo.close()

    app = FastAPI(
        title="Trading Ninja — administration",
        docs_url=None, redoc_url=None, openapi_url=None,
        lifespan=lifespan,
    )

    def get_repo(request: Request) -> Any:
        return request.app.state.repo

    async def session(authorization: str = Header(default="")) -> dict:
        if not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="session requise")
        try:
            return decode_token(config.jwt_secret, authorization[7:])
        except AuthError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

    # -- authentification ------------------------------------------------

    @app.post("/api/auth/login")
    async def login(payload: LoginRequest, request: Request) -> dict:
        client = request.client.host if request.client else "unknown"
        if not limiter.allow(client):
            raise HTTPException(status_code=429, detail="trop de tentatives, réessayer plus tard")
        if not verify_password(payload.password, config.admin_password_hash):
            limiter.fail(client)
            raise HTTPException(status_code=401, detail="identifiants invalides")
        if not totp.verify(payload.totp):
            limiter.fail(client)
            raise HTTPException(status_code=401, detail="identifiants invalides")
        limiter.reset(client)
        return {
            "token": create_token(config.jwt_secret),
            "token_type": "bearer",
            "expires_in": TOKEN_TTL_SECONDS,
        }

    # -- lecture ---------------------------------------------------------

    @app.get("/api/status")
    async def status(repo=Depends(get_repo), _: dict = Depends(session)) -> dict:
        return await build_status(repo, config)

    @app.get("/api/history/{table}")
    async def history(table: str, limit: int = Query(default=10, ge=1, le=50),
                      repo=Depends(get_repo), _: dict = Depends(session)) -> dict:
        try:
            rows = await repo.recent(table, config.execution_mode, limit)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"table": table, "rows": [jsonable(row) for row in rows]}

    @app.get("/api/stats")
    async def stats(repo=Depends(get_repo), _: dict = Depends(session)) -> dict:
        return await build_stats(repo, config)

    # -- réglages --------------------------------------------------------

    @app.get("/api/settings")
    async def settings_view(repo=Depends(get_repo),
                            _: dict = Depends(session)) -> dict:
        try:
            payload, updated_at = await repo.runtime_settings(config.execution_mode)
            candidate = validate_payload(payload, loader=load)
            return friendly_settings(
                candidate,
                updated_at=updated_at.isoformat() if updated_at else None,
            )
        except SettingsRefused as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/settings")
    async def settings_update(payload: SettingsUpdate,
                              repo=Depends(get_repo),
                              _: dict = Depends(session)) -> dict:
        try:
            current, _ = await repo.runtime_settings(config.execution_mode)
            new_payload = build_payload(current, payload.model_dump(exclude_none=True))
            candidate = validate_payload(new_payload, loader=load)
            updated_at = await repo.update_runtime_settings(
                config.execution_mode, new_payload, updated_by="dashboard"
            )
            return friendly_settings(
                candidate,
                updated_at=updated_at.isoformat() if updated_at else None,
            )
        except SettingsRefused as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    # -- contrôle --------------------------------------------------------

    @app.post("/api/control/pause")
    async def control_pause(repo=Depends(get_repo), _: dict = Depends(session)) -> dict:
        try:
            return await pause(repo, config)
        except ControlRefused as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/control/resume")
    async def control_resume(repo=Depends(get_repo), _: dict = Depends(session)) -> dict:
        try:
            return await resume(repo, config)
        except StateTransitionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/control/panic")
    async def control_panic(payload: TotpRequest, repo=Depends(get_repo),
                            _: dict = Depends(session)) -> dict:
        if not totp.verify(payload.totp):
            raise HTTPException(status_code=403, detail="code TOTP invalide ou déjà utilisé")
        from app.runtime.recovery import PanicLiquidationError

        try:
            results = await liquidate_fn(repo, config)
        except PanicLiquidationError as exc:
            # Les ventes réussies sont déjà en base et le bot est halte. On rend
            # l'échec visible (502) avec ce qui reste exposé, sans le masquer.
            state, _cooldown = await repo.state(config.execution_mode)
            raise HTTPException(
                status_code=502,
                detail={
                    "message": str(exc),
                    "liquidated": [jsonable(asdict(item)) for item in exc.liquidated],
                    "errors": exc.errors,
                    "state": state,
                },
            ) from exc
        state, _cooldown = await repo.state(config.execution_mode)
        return {
            "liquidated": [jsonable(asdict(item)) for item in results],
            "state": state,
        }

    # -- flux temps réel -------------------------------------------------

    @app.websocket("/ws/status")
    async def ws_status(websocket: WebSocket, interval: int = Query(default=5, ge=1, le=60)):
        await websocket.accept()
        try:
            first = await asyncio.wait_for(websocket.receive_json(), timeout=5)
        except (asyncio.TimeoutError, WebSocketDisconnect, ValueError):
            await websocket.close(code=4401)
            return
        try:
            decode_token(config.jwt_secret, str(first.get("token", "")))
        except AuthError:
            await websocket.close(code=4401)
            return
        try:
            while True:
                await websocket.send_json(
                    await build_status(websocket.app.state.repo, config)
                )
                await asyncio.sleep(interval)
        except WebSocketDisconnect:
            return

    return app


