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
import asyncpg
from contextlib import asynccontextmanager
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from app.core.coordination import serialized_financial

from app.api.security import (
    TOKEN_TTL_SECONDS, AttemptLimiter, AuthError, TotpVerifier, create_token,
    decode_token, require_secrets, verify_password,
)
from app.api.service import ControlRefused, build_status, jsonable, pause, resume
from app.runtime.reconciliation import inspect_exchange
from app.runtime.scheduler import decision_delay_seconds, next_candle_close
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

class OptionalTotpRequest(BaseModel):
    totp: str = Field(default="", max_length=6)


class SettingsUpdate(BaseModel):
    expected_revision: int | None = Field(default=None, ge=0)
    """Réglages du dashboard. Les pourcentages sont des fractions (0,50 = 50 %)."""

    profile: str | None = None
    max_per_trade_pct: float | None = Field(default=None, ge=0.05, le=1.0)
    max_daily_loss_pct: float | None = Field(default=None, ge=0.01, le=0.20)
    max_orders_per_hour: int | None = Field(default=None, ge=1, le=20)
    confidence_floor: float | None = Field(default=None, ge=0.40, le=0.95)
    entry_rule_enabled: bool | None = None
    halt_auto_resume_hours: int | None = Field(default=None, ge=0, le=168)


class PaperBalanceUpdate(BaseModel):
    balance: Decimal = Field(gt=0, le=1000000, max_digits=18, decimal_places=2)


@serialized_financial(mode_from=lambda v:v['config'].execution_mode,halt_reason='liquidation administrateur')
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
    broker = make_broker(config, exchange, config.exchange.allowed_symbols, repo=repo)
    if isinstance(broker, LiveBroker):
        from app.runtime.execution_recovery import recover_executions
        await recover_executions(repo,broker,config.execution_mode,config.exchange.quote_currency,include_trades=True)
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
        capital_policy=config.capital_policy,
    )



async def _default_kill_switch(repo: Any, config: Settings) -> dict:
    """Suspend les entrées sans retirer les protections ni signer chez l’exchange."""
    control=await repo.entry_control(config.execution_mode)
    await repo.set_entry_permission(config.execution_mode,False,control['revision'],reason='kill-switch administrateur')
    position=await repo.open_position(config.execution_mode)
    if position is None:
        await repo.force_halt(config.execution_mode,'kill-switch administrateur',auto_resume_at=None)
    state,_=await repo.state(config.execution_mode)
    return {'canceled_orders':[],'errors':[],'state':state,'entries_enabled':False,
            'protection_management':'CONTINUED' if position else 'NO_POSITION'}


async def _default_reconcile(repo: Any, config: Settings) -> dict:
    """Lecture seule : compare l'état distant et l'état local, renvoie un diff."""
    from app.exchange.kraken import build_exchange

    now = datetime.now(timezone.utc)
    exchange = build_exchange(
        api_key=config.exchange.api_key, secret=config.exchange.secret
    )
    await asyncio.to_thread(exchange.load_markets)
    try:
        plan = await inspect_exchange(
            repo, exchange, config.execution_mode,
            config.exchange.allowed_symbols,
            quote_currency=config.exchange.quote_currency,
        )
    except Exception as exc:  # noqa: BLE001 — on rend l'échec visible
        return {
            "has_divergence": True,
            "exchange_balances": {},
            "ledger_balances": {},
            "orphan_orders": [],
            "untracked_positions": [],
            "findings": [{"code": "RECONCILE_ERROR", "severity": "CRITICAL",
                          "details": f"{type(exc).__name__}: {exc}"}],
            "reconciled_at": now.isoformat(),
        }

    account = await repo.account(config.execution_mode, config.paper_start_equity)
    positions = await repo.open_positions(config.execution_mode)
    ledger_balances: dict[str, str] = {
        config.exchange.quote_currency: str(account.free_quote),
    }
    for position in positions:
        base = position.symbol.split("/")[0]
        ledger_balances[base] = str(
            Decimal(ledger_balances.get(base, "0")) + position.amount
        )

    exchange_balances: dict[str, str] = {}
    quote_balance = plan.quote_balance
    if quote_balance is not None:
        exchange_balances[config.exchange.quote_currency] = str(quote_balance)
    for symbol in config.exchange.allowed_symbols:
        base = symbol.split("/")[0]
        # Le plan ne porte pas les soldes détaillés ; on relit via read_account.
    try:
        from app.runtime.reconciliation import read_account
        balances, qbalance = await asyncio.to_thread(
            read_account, exchange, config.exchange.allowed_symbols,
            config.exchange.quote_currency,
        )
        exchange_balances = {
            config.exchange.quote_currency: str(qbalance if qbalance is not None else Decimal(0)),
        }
        for symbol, amount in balances.items():
            base = symbol.split("/")[0]
            exchange_balances[base] = str(amount)
    except Exception:  # noqa: BLE001
        pass

    orphan_orders = [
        {"id": f.details} for f in plan.findings
        if f.code in {"ORPHAN_STOP", "UNMATCHED_REMOTE_ORDER"}
    ]
    untracked = [
        f.code for f in plan.findings if f.code in {"BALANCE_TOO_SMALL"}
    ]
    return {
        "has_divergence": not plan.clean,
        "exchange_balances": exchange_balances,
        "ledger_balances": ledger_balances,
        "orphan_orders": orphan_orders,
        "untracked_positions": untracked,
        "findings": [
            {"code": f.code, "severity": f.severity, "symbol": f.symbol,
             "details": f.details}
            for f in plan.findings
        ],
        "reconciled_at": now.isoformat(),
    }


async def _default_system_health(repo: Any, config: Settings) -> dict:
    """Télémétrie best-effort : ne doit jamais faire planter l'API."""
    from app.exchange.kraken import build_exchange

    now = datetime.now(timezone.utc)
    kraken_status = "OFFLINE"
    kraken_latency_ms = None
    last_tick_at = None
    try:
        exchange = build_exchange(
            api_key=config.exchange.api_key, secret=config.exchange.secret
        )
        await asyncio.to_thread(exchange.load_markets)
        symbol = config.exchange.allowed_symbols[0] if config.exchange.allowed_symbols else None
        if symbol:
            started = now
            ticker = await asyncio.to_thread(exchange.fetch_ticker, symbol)
            kraken_latency_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
            last_tick_at = datetime.now(timezone.utc).isoformat()
            if ticker is not None:
                kraken_status = "CONNECTED"
    except Exception:  # noqa: BLE001
        kraken_status = "OFFLINE"

    # Scheduler : prochaine clôture et dernière bougie clôturée.
    timeframe = getattr(config.exchange, "daily_timeframe_decision", "4h")
    try:
        next_close = next_candle_close(now, timeframe)
        next_run = next_close + timedelta(seconds=decision_delay_seconds(timeframe))
        duration = {"1h": 3600, "4h": 14400, "1d": 86400}.get(timeframe, 14400)
        last_closed = next_close - timedelta(seconds=duration)
    except Exception:  # noqa: BLE001
        next_run = None
        last_closed = None

    # Ollama : sonde HTTP légère.
    ollama_status = "DOWN"
    ollama_latency_ms = None
    ollama_url = getattr(config.llm, "base_url", None) or getattr(config.llm, "ollama_url", None)
    if not ollama_url:
        ollama_url = "http://127.0.0.1:11434"
    try:
        import urllib.request
        started = datetime.now(timezone.utc)
        def _probe():
            with urllib.request.urlopen(ollama_url.rstrip("/").removesuffix("/v1") + "/api/tags", timeout=2) as response:
                return response.status
        status_code = await asyncio.to_thread(_probe)
        ollama_latency_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        if status_code == 200:
            ollama_status = "UP"
    except Exception:  # noqa: BLE001
        ollama_status = "DOWN"

    storage={'status':'UNKNOWN','db_size_bytes':None,'db_limit_bytes':524288000,
             'free_space_bytes':None,'quota_alert_triggered':True}
    try:
        observed=await repo.storage_state()
        if not isinstance(observed,dict) or observed.get('free_space_bytes') is None:raise ValueError('quota inconnu')
        storage={**observed,'status':'KNOWN','quota_alert_triggered':int(observed['free_space_bytes'])<100*1024*1024}
    except Exception:
        pass

    return {
        "kraken": {
            "status": kraken_status,
            "avg_latency_ms": kraken_latency_ms,
            "last_tick_at": last_tick_at,
        },
        "scheduler": {
            "next_run_utc": next_run.isoformat() if next_run else None,
            "last_closed_candle_at": last_closed.isoformat() if last_closed else None,
        },
        "ollama": {
            "status": ollama_status,
            "model": getattr(config.llm, "model", None),
            "last_inference_ms": None,
            "avg_latency_ms": ollama_latency_ms,
        },
        "supabase": storage,
    }


async def _default_position(repo: Any, config: Settings) -> dict:
    position = await repo.open_position(config.execution_mode)
    if position is None:
        return {"has_active_position": False}
    current_price = position.entry_price
    try:
        from app.exchange.kraken import build_exchange
        exchange = build_exchange(
            api_key=config.exchange.api_key, secret=config.exchange.secret
        )
        await asyncio.to_thread(exchange.load_markets)
        ticker = await asyncio.to_thread(exchange.fetch_ticker, position.symbol)
        if ticker and ticker.get("last") is not None:
            current_price = Decimal(str(ticker["last"]))
    except Exception:  # noqa: BLE001
        pass
    amount = position.amount
    unrealized = (current_price - position.entry_price) * amount
    unrealized_pct = (
        (current_price / position.entry_price - Decimal(1)) * Decimal(100)
        if position.entry_price else Decimal(0)
    )
    stop_order_id = None
    try:
        getter = getattr(repo, "stop_order_id", None)
        if callable(getter):
            stop_order_id = await getter(position.id)
    except Exception:  # noqa: BLE001
        stop_order_id = None
    return {
        "has_active_position": True,
        "symbol": position.symbol,
        "entry_price": str(position.entry_price),
        "current_price": str(current_price),
        "amount": str(amount),
        "unrealized_pnl_quote": str(unrealized),
        "unrealized_pnl_pct": str(unrealized_pct),
        "stop_loss_order_id": stop_order_id,
        "stop_loss_price": str(position.stop_loss_price),
        "is_breakeven_active": position.stop_loss_price >= position.entry_price,
        "opened_at": position.opened_at.isoformat(),
    }


def _mask_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if any(token in str(key).lower() for token in ("token", "secret", "password", "api_key", "apikey")):
                out[key] = "***"
            else:
                out[key] = _mask_sensitive(item)
        return out
    if isinstance(value, list):
        return [_mask_sensitive(item) for item in value]
    return value


def create_app(
    settings: Settings | None = None,
    *,
    repo: Any | None = None,
    liquidate: Any | None = None,
    kill_switch: Any | None = None,
    reconcile: Any | None = None,
    system_health: Any | None = None,
    position_provider: Any | None = None,
    settings_loader: Any | None = None,
) -> FastAPI:
    config = settings or load_settings()
    load = settings_loader or (
        lambda overrides=None: load_settings(
            overrides={**config._raw_env, **(overrides or {})}, use_environment=False
        )
    )
    require_secrets(config.jwt_secret, config.totp_secret, config.admin_password_hash)

    totp = TotpVerifier(config.totp_secret)
    limiter = AttemptLimiter()
    liquidate_fn = liquidate or _default_liquidate
    kill_switch_fn = kill_switch or _default_kill_switch
    reconcile_fn = reconcile or _default_reconcile
    health_fn = system_health or _default_system_health
    position_fn = position_provider or _default_position
    owned_repo = repo is None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.repo = repo
        app.state.pool = None
        if owned_repo:
            app.state.pool = await asyncpg.create_pool(
                min_size=1, max_size=4, **config.database().asyncpg_kwargs()
            )
        try:
            yield
        finally:
            if app.state.pool is not None:
                await app.state.pool.close()

    app = FastAPI(
        title="Trading Ninja — administration",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    @asynccontextmanager
    async def repository_for(app):
        if app.state.pool is None:
            yield app.state.repo
        else:
            async with app.state.pool.acquire() as connection:
                yield PostgresRepository(
                    connection,
                    lock_connection_factory=lambda: asyncpg.connect(
                        **config.database().asyncpg_kwargs()
                    ),
                )

    async def get_repo(request: Request):
        async with repository_for(request.app) as repository:
            yield repository

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
            if hasattr(repo,'runtime_snapshot'):
                snapshot=await repo.runtime_snapshot(config.execution_mode)
                payload,updated_at=snapshot['payload'],snapshot['updated_at']
            else:payload,updated_at=await repo.runtime_settings(config.execution_mode)
            candidate = validate_payload(payload, loader=load)
            view=friendly_settings(candidate,updated_at=updated_at.isoformat() if updated_at else None)
            if hasattr(repo,'runtime_snapshot'):view['application']=jsonable({k:v for k,v in snapshot.items() if k not in {'payload','updated_at'}})
            elif hasattr(repo,'settings_state'):view['application']=jsonable(await repo.settings_state(config.execution_mode))
            return view
        except SettingsRefused as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except StateTransitionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/settings")
    async def settings_update(payload: SettingsUpdate,
                              repo=Depends(get_repo),
                              _: dict = Depends(session)) -> dict:
        try:
            current, _ = await repo.runtime_settings(config.execution_mode)
            new_payload = build_payload(current, payload.model_dump(exclude_none=True))
            version_getter=getattr(repo,'settings_state',None)
            if version_getter:
                if payload.expected_revision is None:raise HTTPException(428,'révision attendue requise')
                version=await version_getter(config.execution_mode)
                if payload.expected_revision is not None and version['revision']!=payload.expected_revision:
                    raise HTTPException(409,'révision des réglages obsolète')
            candidate = validate_payload(new_payload, loader=load)
            if hasattr(repo,'capital_state'):
                capital=await repo.capital_state(config.execution_mode)
                if capital is not None and not candidate.capital_policy.enabled:
                    raise HTTPException(409,'capital par paliers existant : changement incompatible')
            updated_at = await repo.update_runtime_settings(
                config.execution_mode, new_payload, updated_by="dashboard",
                **({"expected_revision":payload.expected_revision} if payload.expected_revision is not None else {})
            )
            view=friendly_settings(candidate,updated_at=updated_at.isoformat() if updated_at else None)
            if hasattr(repo,'settings_state'):view['application']=jsonable(await repo.settings_state(config.execution_mode))
            return view
        except SettingsRefused as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except StateTransitionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/control/paper-balance")
    async def paper_balance_update(payload: PaperBalanceUpdate, repo=Depends(get_repo),
                                   _: dict = Depends(session)) -> dict:
        if config.execution_mode != "PAPER":
            raise HTTPException(409, "ajustement du solde réservé au mode PAPER")
        try:
            async with repo.cycle_lock("PAPER"):
                if await repo.open_position("PAPER") is not None:
                    raise HTTPException(409, "ferme la position PAPER avant d’ajuster le solde")
                current = await repo.account("PAPER", config.paper_start_equity)
                result = await repo.set_paper_balance(payload.balance, current)
                return {"balance": str(result), "mode": "PAPER"}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    # -- contrôle --------------------------------------------------------

    @app.post("/api/control/pause")
    async def control_pause(repo=Depends(get_repo), _: dict = Depends(session)) -> dict:
        try:
            return await pause(repo, config)
        except (ControlRefused,StateTransitionError) as exc:
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


    # -- contrôle étendu -------------------------------------------------

    @app.post("/api/control/kill-switch")
    async def control_kill_switch(payload: OptionalTotpRequest, repo=Depends(get_repo),
                                 _: dict = Depends(session)) -> dict:
        if not totp.verify(payload.totp):
            raise HTTPException(status_code=403, detail="code TOTP invalide ou déjà utilisé")
        try:
            result = await kill_switch_fn(repo, config)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"kill-switch impossible : {exc}") from exc
        state, _cooldown = await repo.state(config.execution_mode)
        return {"state": state, "canceled_orders": result.get("canceled_orders", []),
                "errors": result.get("errors", [])}

    @app.post("/api/control/circuit-breaker/reset")
    async def control_circuit_breaker_reset(payload: OptionalTotpRequest,
                                           repo=Depends(get_repo),
                                           _: dict = Depends(session)) -> dict:
        if not totp.verify(payload.totp):
            raise HTTPException(status_code=403, detail="code TOTP invalide ou déjà utilisé")
        try:
            await repo.reset_circuit_breaker(config.execution_mode)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return await build_status(repo, config)

    @app.post("/api/control/reconcile")
    async def control_reconcile(repo=Depends(get_repo),
                                _: dict = Depends(session)) -> dict:
        try:
            return await reconcile_fn(repo, config)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"réconciliation impossible : {exc}") from exc

    # -- télémétrie ------------------------------------------------------

    @app.get("/api/system/health")
    async def system_health(repo=Depends(get_repo), _: dict = Depends(session)) -> dict:
        try:
            return await health_fn(repo, config)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/api/portfolio/position")
    async def portfolio_position(repo=Depends(get_repo), _: dict = Depends(session)) -> dict:
        try:
            return await position_fn(repo, config)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/api/history/llm_signals/{signal_id}/prompt")
    async def llm_signal_prompt(signal_id: str, repo=Depends(get_repo),
                                _: dict = Depends(session)) -> dict:
        getter = getattr(repo, "llm_signal_prompt", None)
        if not callable(getter):
            raise HTTPException(status_code=501, detail="inspection non disponible")
        try:
            payload = await getter(config.execution_mode, signal_id)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        if payload is None:
            raise HTTPException(status_code=404, detail="signal introuvable")
        return {"signal_id": signal_id, "prompt_payload": _mask_sensitive(payload)}

    # -- flux temps réel -------------------------------------------------

    @app.websocket("/ws/status")
    async def ws_status(websocket: WebSocket, interval: int = Query(default=5, ge=1, le=60)):
        await websocket.accept()
        try:
            first = await asyncio.wait_for(websocket.receive_json(), timeout=5)
        except (asyncio.TimeoutError, WebSocketDisconnect, ValueError):
            await websocket.close(code=4401)
            return
        if not isinstance(first,dict):
            await websocket.close(code=4401);return
        token=str(first.get("token",""))
        try:
            decode_token(config.jwt_secret, token)
        except AuthError:
            await websocket.close(code=4401)
            return
        try:
            while True:
                try:decode_token(config.jwt_secret,token)
                except AuthError:
                    await websocket.close(code=4401);return
                async with repository_for(websocket.app) as repository:
                    payload=await build_status(repository,config)
                await websocket.send_json(payload)
                await asyncio.sleep(interval)
        except WebSocketDisconnect:
            return

    from app.api.insights import router as insights_router
    app.include_router(insights_router(config,load,session,get_repo,totp))
    return app


