"""API d'administration — observation, pause, reprise, panic-sell.

    .venv/bin/python -m uvicorn app.api.main:create_app --factory \
        --host 127.0.0.1 --port 8080

Principes :

- l'API ne décide jamais d'acheter ; elle lit, arrête et liquide ;
- `/api/control/panic` exige un code TOTP **frais** en plus de la session ;
- la session expire en 15 minutes ;
- la documentation interactive (Swagger) est désactivée : une surface
  d'administration n'a pas à publier son propre mode d'emploi ;
- sans `JWT_SECRET_KEY`, `TOTP_SECRET` et `ADMIN_PASSWORD_HASH`, l'application
  refuse de démarrer (fail-closed).

Le panic-sell n'est câblé que pour le mode papier : le chemin réel passera par
l'exécuteur Kraken quand il sera revu, pas par une option d'API.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict
from decimal import Decimal
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from app.api.security import (
    TOKEN_TTL_SECONDS, AttemptLimiter, AuthError, TotpVerifier, create_token,
    decode_token, require_secrets, verify_password,
)
from app.api.service import ControlRefused, build_status, jsonable, pause, resume
from app.core.settings import Settings, load_settings
from app.db.repository import PostgresRepository, StateTransitionError

__all__ = ["create_app"]


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=256)
    totp: str = Field(min_length=6, max_length=6)


class TotpRequest(BaseModel):
    totp: str = Field(min_length=6, max_length=6)


async def _default_liquidate(repo: PostgresRepository, config: Settings) -> list:
    """Liquidation réelle de la position papier, au prix public courant."""
    from app.exchange.kraken import build_exchange, market_info
    from app.execution.paper import PaperBroker
    from app.runtime.recovery import liquidate_open_positions

    exchange = build_exchange(
        api_key=config.exchange.api_key, secret=config.exchange.secret
    )
    await asyncio.to_thread(exchange.load_markets)
    fees = [market_info(exchange, symbol).taker_fee
            for symbol in config.exchange.allowed_symbols]
    broker = PaperBroker(
        max(fees) if fees else Decimal("0.0026"), config.paper_slippage_bps
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
) -> FastAPI:
    config = settings or load_settings()
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
        if config.mode != "paper":
            raise HTTPException(
                status_code=409,
                detail="panic-sell réel non câblé : utiliser l'interface de l'exchange",
            )
        if not totp.verify(payload.totp):
            raise HTTPException(status_code=403, detail="code TOTP invalide ou déjà utilisé")
        results = await liquidate_fn(repo, config)
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


