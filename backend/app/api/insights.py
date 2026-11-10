"""API privée des observations ; aucune route analytique n'envoie d'ordre."""

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, StrictBool

from app.api.settings_store import build_payload, validate_payload
from app.db.repository import StateTransitionError
from app.insights.codec import bounded, public
from app.insights.diagnostics import trade_diagnostics, opportunity_summary
from app.insights.replay import replay_cycle, Unreplayable, ReplayDivergence
from app.insights.experiments import run_experiment, validate_experiment
from app.insights.calibration import confidence_report
from app.insights.policy import position_policy, settings_impact
from app.insights.health import classify, cycle_deadline
from app.runtime.heartbeat import MAX_AGE_SECONDS


class EntryCommand(BaseModel):
    enabled: StrictBool
    expected_revision: int = Field(ge=0)
    reason: str = Field(default="", max_length=240)
    totp: str | None = None


class AdoptCommand(BaseModel):
    expected_policy_id: str = Field(min_length=64, max_length=64)
    totp: str


class SettingsPreview(BaseModel):
    update: dict
    expected_revision: int = Field(ge=0)


def router(config, loader, session, get_repo, totp):
    api = APIRouter(dependencies=[Depends(session)])
    research_slots = asyncio.Semaphore(1)

    def require(repo, name):
        method = getattr(repo, name, None)
        if method is None:
            raise HTTPException(
                503, "migration 0011 et repository d’observation requis"
            )
        return method

    def window(since, until):
        if (
            since.tzinfo is None
            or until.tzinfo is None
            or until <= since
            or until - since > timedelta(days=93)
        ):
            raise HTTPException(422, "période UTC positive limitée à 93 jours")

    def protection_freshness(data):
        latest = data.get("latest")
        health = classify(
            {**latest, **{key: data.get(key) for key in
                         ("runtime_heartbeat_at", "runtime_cycle_started_at", "runtime_cycle_status")}} if latest else None,
            now=datetime.now(timezone.utc),
            max_age_seconds=2 * config.observer_interval_seconds,
        )
        return {
            **data,
            **health,
            "valid_until": min(
                latest["observed_at"] + timedelta(seconds=2 * config.observer_interval_seconds),
                data["runtime_heartbeat_at"] + timedelta(seconds=MAX_AGE_SECONDS),
                cycle_deadline(data["runtime_cycle_started_at"], data["runtime_cycle_status"], latest["body"]["runtime_timeframe"]),
            ) if health["status"] == "OK" else None,
            "freshness": "FRESH" if health["status"] == "OK" else "STALE"
            if health["reason"] == "OBSERVER_STALE" else "UNKNOWN",
        }

    @api.get("/api/insights/overview")
    async def overview(days: int = Query(7, ge=1, le=93), repo=Depends(get_repo)):
        now = datetime.now(timezone.utc)
        since = now - timedelta(days=days)
        events = await require(repo, "opportunities")(config.execution_mode, since, now)
        from app.market.feed import TIMEFRAME_MS

        duration = TIMEFRAME_MS[config.exchange.daily_timeframe_decision]
        expected = (
            int(now.timestamp() * 1000) // duration
            - int(since.timestamp() * 1000) // duration
        ) * len(config.exchange.allowed_symbols)
        coverage = (
            await repo.observation_coverage(config.execution_mode, since, now)
            if hasattr(repo, "observation_coverage")
            else None
        )
        return public(
            {
                "opportunities": {
                    **opportunity_summary(events),
                    "expected_windows_current_config": expected,
                    "cycles": coverage,
                    "expected_basis": "THEORETICAL_CURRENT_UNIVERSE_NOT_UPTIME_PROOF",
                },
                "entries": await repo.entry_control(config.execution_mode),
                "cycles": await repo.recent_cycles(config.execution_mode, 10),
                "experiments": await repo.experiments(config.execution_mode, 5),
                "protection": protection_freshness(
                    await repo.protection_health(config.execution_mode)
                ),
                "settings": await repo.settings_state(config.execution_mode),
            }
        )

    @api.get("/api/diagnostics/opportunities")
    async def opportunities(
        since: datetime,
        until: datetime,
        symbol: str | None = Query(None, max_length=32),
        repo=Depends(get_repo),
    ):
        window(since, until)
        events = await require(repo, "opportunities")(
            config.execution_mode, since, until, symbol
        )
        return public({"summary": opportunity_summary(events), "events": events})

    @api.get("/api/positions/{position_id}/diagnostics")
    async def position(position_id: UUID, repo=Depends(get_repo)):
        data = await require(repo, "diagnostics_input")(
            config.execution_mode, position_id
        )
        if data is None:
            raise HTTPException(404, "position introuvable")
        return public(trade_diagnostics(data, config.exchange.taker_fee))

    @api.get("/api/insights/cycles")
    async def cycles(limit: int = Query(20, ge=1, le=100), repo=Depends(get_repo)):
        return public(
            {
                "cycles": await require(repo, "recent_cycles")(
                    config.execution_mode, limit
                )
            }
        )

    @api.get("/api/insights/cycles/{cycle_id}/events")
    async def events(cycle_id: UUID, repo=Depends(get_repo)):
        return public(
            {
                "events": await require(repo, "cycle_events")(
                    config.execution_mode, cycle_id
                )
            }
        )

    @api.get("/api/insights/cycles/{cycle_id}")
    async def export(cycle_id: UUID, repo=Depends(get_repo)):
        packet = await require(repo, "cycle_packet")(config.execution_mode, cycle_id)
        if packet is None:
            raise HTTPException(404, "cycle introuvable")
        return packet

    async def replay(packet):
        try:
            bounded(packet)
            if research_slots.locked():
                raise HTTPException(429, "une analyse est déjà en cours")
            async with research_slots:
                result = await asyncio.to_thread(
                    lambda: asyncio.run(replay_cycle(packet))
                )
            return public(result)
        except (Unreplayable, ReplayDivergence, ValueError, TypeError, KeyError) as exc:
            raise HTTPException(
                422, {"status": "UNREPLAYABLE", "reason": str(exc)}
            ) from exc

    @api.post("/api/insights/cycles/{cycle_id}/replay")
    async def replay_saved(cycle_id: UUID, repo=Depends(get_repo)):
        packet = await require(repo, "cycle_packet")(config.execution_mode, cycle_id)
        if packet is None:
            raise HTTPException(404, "cycle introuvable")
        return await replay(packet)

    @api.post("/api/insights/replay")
    async def replay_uploaded(packet: dict):
        return await replay(packet)

    @api.post("/api/control/entries")
    async def entries(command: EntryCommand, repo=Depends(get_repo)):
        if command.enabled and not totp.verify(command.totp or ""):
            raise HTTPException(
                403, "code TOTP frais requis pour réautoriser les achats"
            )
        try:
            if command.enabled:
                async with repo.cycle_lock(config.execution_mode):
                    result = await require(repo, "set_entry_permission")(
                        config.execution_mode,
                        True,
                        command.expected_revision,
                        reason=command.reason,
                    )
            else:
                result = await require(repo, "set_entry_permission")(
                    config.execution_mode,
                    False,
                    command.expected_revision,
                    reason=command.reason,
                )
            return public(result)
        except StateTransitionError as exc:
            raise HTTPException(409, str(exc)) from exc

    @api.post("/api/control/positions/{position_id}/adopt-policy")
    async def adopt(position_id: UUID, command: AdoptCommand, repo=Depends(get_repo)):
        if config.mode != "paper":
            raise HTTPException(
                409, "adoption historique disponible uniquement en papier"
            )
        if not totp.verify(command.totp):
            raise HTTPException(403, "code TOTP frais requis")
        payload, _ = await repo.runtime_settings(config.execution_mode)
        application = await require(repo, "settings_state")(config.execution_mode)
        if payload and application["applied_revision"] != application["revision"]:
            raise HTTPException(
                409, "réglages demandés pas encore appliqués par le moteur"
            )
        candidate = validate_payload(payload, loader=loader)
        policy = position_policy(candidate, legacy=True)
        if policy["id"] != command.expected_policy_id:
            raise HTTPException(409, "politique modifiée depuis la prévisualisation")
        async with repo.cycle_lock(config.execution_mode):
            changed = await require(repo, "attach_policy")(
                config.execution_mode, position_id, policy
            )
        if not changed:
            raise HTTPException(409, "position fermée, absente ou déjà versionnée")
        return {"position_id": str(changed), "policy_id": policy["id"], "adopted": True}

    @api.get("/api/insights/policy")
    async def policy(repo=Depends(get_repo)):
        payload, _ = await repo.runtime_settings(config.execution_mode)
        settings = validate_payload(payload, loader=loader)
        positions = await repo.open_positions(config.execution_mode)
        return public(
            {
                "application": await repo.settings_state(config.execution_mode),
                "next_position": position_policy(settings),
                "legacy_adoption": position_policy(settings, legacy=True),
                "positions": [
                    {
                        "id": p.id,
                        "symbol": p.symbol,
                        "policy_id": getattr(p, "policy_id", None),
                    }
                    for p in positions
                ],
            }
        )

    @api.post("/api/settings/preview")
    async def preview(command: SettingsPreview, repo=Depends(get_repo)):
        allowed = {
            "profile",
            "max_per_trade_pct",
            "max_daily_loss_pct",
            "max_orders_per_hour",
            "confidence_floor",
            "entry_rule_enabled",
            "halt_auto_resume_hours",
        }
        if set(command.update) - allowed:
            raise HTTPException(422, "réglage non pilotable")
        version = await require(repo, "settings_state")(config.execution_mode)
        if command.expected_revision != version["revision"]:
            raise HTTPException(409, "révision des réglages obsolète")
        current, _ = await repo.runtime_settings(config.execution_mode)
        before = validate_payload(current, loader=loader)
        try:
            candidate = validate_payload(
                build_payload(current, command.update), loader=loader
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        capital = await repo.capital_state(config.execution_mode)
        if capital is not None and not candidate.capital_policy.enabled:
            raise HTTPException(
                409, "capital par paliers existant : changement incompatible"
            )
        impact = settings_impact(
            before, candidate, await repo.open_positions(config.execution_mode)
        )
        return public(
            {
                **impact,
                "expected_revision": version["revision"],
                "candidate_policy": position_policy(candidate),
            }
        )

    @api.get("/api/insights/experiments")
    async def experiments(limit: int = Query(20, ge=1, le=50), repo=Depends(get_repo)):
        return public(
            {
                "experiments": await require(repo, "experiments")(
                    config.execution_mode, limit
                )
            }
        )

    @api.post("/api/insights/experiments")
    async def experiment(spec: dict, repo=Depends(get_repo)):
        try:
            bounded(spec, max_bytes=1048576)
            start, end = validate_experiment(spec)
            if end - start > timedelta(days=90):
                raise ValueError(
                    "interface limitée à 90 jours ; utiliser la CLI pour un historique long"
                )
            if config.mode != "paper":
                raise ValueError(
                    "lancement d’expérience disponible en mode papier uniquement"
                )
            snapshot = (
                await repo.runtime_snapshot(config.execution_mode)
                if hasattr(repo, "runtime_snapshot")
                else None
            )
            experiment_config = config
            if snapshot:
                if (
                    snapshot["payload"]
                    and snapshot["applied_revision"] != snapshot["revision"]
                ):
                    raise ValueError(
                        "attendre l’application des réglages par le moteur avant comparaison"
                    )
                experiment_config = validate_payload(snapshot["payload"], loader=loader)
            if research_slots.locked():
                raise HTTPException(429, "une analyse est déjà en cours")
            async with research_slots:
                eid = await require(repo, "start_experiment")(
                    config.execution_mode, spec
                )
                try:
                    result = await asyncio.to_thread(
                        lambda: asyncio.run(run_experiment(spec, experiment_config))
                    )
                    await repo.finish_experiment(config.execution_mode, eid, result)
                except BaseException as exc:
                    await repo.finish_experiment(
                        config.execution_mode,
                        eid,
                        {"error_type": type(exc).__name__, "reason": str(exc)[:300]},
                        failed=True,
                    )
                    raise
            return public({"id": str(eid), "result": result})
        except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @api.get("/api/insights/confidence")
    async def confidence(
        since: datetime,
        until: datetime,
        min_samples: int = Query(20, ge=10, le=1000),
        repo=Depends(get_repo),
    ):
        window(since, until)
        records = await require(repo, "calibration_input")(
            config.execution_mode, since, until
        )
        return public(
            {
                **confidence_report(records, until=until, min_samples=min_samples),
                "truncated": len(records) > 10000,
            }
        )

    @api.get("/api/insights/protection")
    async def protection(repo=Depends(get_repo)):
        return public(
            protection_freshness(
                await require(repo, "protection_health")(config.execution_mode)
            )
        )

    @api.post("/api/insights/incidents/{incident_id}/acknowledge")
    async def acknowledge(incident_id: UUID, repo=Depends(get_repo)):
        changed = await require(repo, "acknowledge_incident")(
            config.execution_mode, incident_id
        )
        if not changed:
            raise HTTPException(404, "incident introuvable")
        return {"acknowledged": True, "resolved": False}

    return api
