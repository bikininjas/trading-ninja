"""Boucle d'exécution : un cycle = une décision, jamais deux.

Séquence d'un cycle, dans cet ordre et pas un autre :

    état → compte → position ouverte ? → surveillance / évaluation → pare-feu → fill papier

Invariants tenus ici :

- le pare-feu est le **seul** chemin vers une position : `evaluate()` est appelé
  avant tout `PaperBroker.entry()`, et la quantité vient de la décision ;
- la règle d'entrée déterministe choisit **quand** le modèle est consulté ; le
  modèle choisit **quoi faire** (BUY / SELL / HOLD / HALT) ;
- une intention HOLD n'entre pas dans le pare-feu : elle n'a pas de sens pour lui
  et polluerait les statistiques de rejet ;
- un SELL ou un HALT du modèle ne peut que **solder une position existante** :
  jamais de vente à découvert, jamais une taille proposée par le modèle ;
- le prix est rafraîchi **après** l'appel LLM : une inférence lente ne doit pas
  transformer une décision en donnée périmée ;
- une panne du modèle produit un HOLD de repli, pas un arrêt de la boucle ;
- un `EMERGENCY_HALT` ou un coupe-circuit ne se lève jamais tout seul.
"""

from __future__ import annotations

import json
import asyncio
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from inspect import isawaitable
from types import SimpleNamespace
from app.insights.codec import digest,encode,public
from app.insights.policy import position_policy,apply_policy
from app.insights.costs import estimate_costs
from app.insights.ranking import rank_candidates
from typing import Any

from app.core.settings import RUNTIME_ALLOWED_KEYS, Settings, load_settings
from app.db.repository import AccountSnapshot, PositionRow, PostgresRepository
from app.engine.entry_rule import evaluate_entry
from app.engine.exit_guard import OpenPosition, evaluate_exit
from app.engine.exit_rule import advance_profit_stop, should_exit_chandelier, update_chandelier_stop
from app.engine.machine import (
    COOLDOWN, EVALUATING, EXECUTING, HALT, IDLE, IN_POSITION, LOCKED,
    POST_TRADE_SWEEP, StateMachine,
)
from app.engine.sweep import plan_sweep
from app.engine.risk_gate import (
    AccountState, Action, EntryMode, MarketRegime, SwingExitMode, TradeIntent,
    evaluate,
)
from app.execution.live import LiveExecutionError
from app.execution.paper import PaperBroker, Fill
from app.llm.client import LLMSignal, SignalClient, SYSTEM_PROMPT, D1_SYSTEM_PROMPT
from app.market.feed import (
    FeedError, SymbolState, build_snapshot, closed_candles, last_price,
)

__all__ = ["CycleReport", "Orchestrator"]

COOLDOWN_MINUTES = 15


@dataclass
class CycleReport:
    mode: str
    state_before: str
    state_after: str
    actions: list[str] = field(default_factory=list)
    signals: int = 0
    evaluations: int = 0
    approved: int = 0
    skipped: int = 0
    degraded_calls: int = 0
    position_opened: str | None = None
    position_closed: str | None = None
    errors: list[str] = field(default_factory=list)
    elapsed_ms: int = 0

    def lines(self) -> list[str]:
        out = [
            f"mode {self.mode} · état {self.state_before} → {self.state_after}",
            f"signaux {self.signals} · évaluations {self.evaluations} · "
            f"approuvés {self.approved} · replis LLM {self.degraded_calls}"
            + (f" · bougies déjà évaluées {self.skipped}" if self.skipped else ""),
        ]
        out.extend(f"  {action}" for action in self.actions)
        if self.position_opened:
            out.append(f"  POSITION OUVERTE : {self.position_opened}")
        if self.position_closed:
            out.append(f"  POSITION FERMÉE : {self.position_closed}")
        out.extend(f"  ERREUR : {error}" for error in self.errors)
        return out


class Orchestrator:
    def __init__(self, settings: Settings, repo: PostgresRepository, exchange: Any,
                 signal_client: SignalClient, broker: Any,
                 *, now: Any = None, settings_loader: Any = None, insights: bool | None = None,
                 id_factory=None, perf_clock=None):
        fee = getattr(broker, "execution_fee_pct", settings.risk.execution_fee_pct)
        self._settings = replace(settings, risk=replace(settings.risk, execution_fee_pct=fee))
        self._repo = repo
        self._exchange = exchange
        self._llm = signal_client
        self._broker = broker
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._mode = settings.execution_mode
        self._settings_updated_at: datetime | None = None
        self._base_settings=self._settings
        self._load_settings = settings_loader or (lambda overrides=None:load_settings(overrides={**settings._raw_env,**(overrides or {})},use_environment=False))
        self._lessons: list[str] = []
        self._capture = settings.insights_enabled if insights is None else insights
        self._id_factory = id_factory or uuid.uuid4
        self._perf = perf_clock or time.perf_counter
        self._feature_repo = repo
        self._cycle_id = None
        self._event_seq = 0
        self._opportunities = {}
        self._legacy_policy = False
        self._model_digest = None
        self._stop_ids = {}
        self._submission = False
        self._observation_error = None
        self._recorder = None

    # -- helpers ---------------------------------------------------------

    @staticmethod
    async def _maybe_await(value: Any) -> Any:
        """Le broker papier est synchrone, le broker réel est asynchrone.

        Cette petite indirection permet aux deux d'implémenter le même contrat
        sans forcer tous les tests papier à devenir async.
        """
        if isawaitable(value):
            return await value
        return value

    async def _entry_fee(self, position: Any) -> Decimal:
        """Frais d'entrée réellement enregistrés, sinon estimation du broker.

        En live, le frais vient de la réponse de l'exchange et a été écrit dans
        l'ordre d'entrée. En papier, il vient du même chemin ; le repli ne sert
        qu'aux positions anciennes ou importées sans ordre.
        """
        getter = getattr(self._repo, "entry_fee", None)
        if getter is not None:
            recorded = await self._maybe_await(getter(position.id))
            if recorded is not None and recorded > 0:
                return recorded
        return self._broker.fee(position.entry_price * position.amount)

    def _account_state(self, account: AccountSnapshot, open_symbols: tuple[str, ...] = ()) -> AccountState:
        return AccountState(
            equity_quote=account.trading_equity,
            free_quote=account.free_quote,
            open_position_symbols=open_symbols,
            daily_drawdown_pct=account.daily_drawdown_pct,
            weekly_drawdown_pct=account.weekly_drawdown_pct,
            drawdown_from_hwm_pct=account.from_hwm_drawdown_pct,
            consecutive_losses=account.consecutive_losses,
            orders_last_hour=account.orders_last_hour,
        )

    def _client_id(self, symbol: str, purpose: str) -> str:
        return f"{self._mode}-{symbol.replace('/', '')}-{purpose}-{self._id_factory().hex[:10]}"

    async def _starting_equity(self) -> Decimal:
        if self._settings.paper_start_equity > 0:
            return self._settings.paper_start_equity
        if self._settings.exchange.api_key:
            balance = self._exchange.fetch_balance()
            value = balance["total"].get(self._settings.exchange.quote_currency, 0)
            equity = Decimal(str(value))
            if equity > 0:
                return equity
        raise RuntimeError(
            "équité paper inconnue : renseigner PAPER_START_EQUITY ou fournir une clé "
            "exchange en lecture"
        )

    async def _current_account(self):
        account=await self._repo.account(self._mode,await self._starting_equity())
        if self._mode=='LIVE' and hasattr(self._broker,'free_quote'):
            free=await self._maybe_await(self._broker.free_quote())
            positions=await self._repo.open_positions(self._mode)
            value=sum((p.amount*last_price(self._exchange,p.symbol) for p in positions),Decimal(0))
            cash=max(Decimal(0),free-account.reserve_quote-account.growth_quote)
            await self._repo.snapshot_equity(self._mode,equity=free+value,free_quote=cash,
                position_value_quote=value,source='LIVE_REFRESH')
            account=await self._repo.account(self._mode,await self._starting_equity())
        return account

    async def _refresh_settings(self) -> tuple[bool, str | None]:
        """Relit les réglages en base et les applique si le payload a changé.

        Le rechargement est limité aux réglages : le mode, la base et les clés ne
        bougent pas à chaud. Un payload invalide est refusé et l'ancienne
        configuration reste active.
        """
        version = None
        try:
            snapshot = getattr(self._repo, "runtime_snapshot", None)
            if snapshot:
                version = await snapshot(self._mode)
                payload, updated_at = version["payload"], version["updated_at"]
            else:
                payload, updated_at = await self._repo.runtime_settings(self._mode)
        except Exception as exc:
            return False, f"réglages en base illisibles : {exc}"

        async def refused(reason):
            ack = getattr(self._repo, "acknowledge_settings", None)
            if ack and version is not None:
                await ack(self._mode, version["revision"], {}, error=reason)
            return False, reason

        if updated_at == self._settings_updated_at:
            return False, None
        try:
            fresh = (
                self._base_settings
                if not payload
                else self._load_settings(
                    overrides={
                        key: str(value)
                        for key, value in payload.items()
                        if key in RUNTIME_ALLOWED_KEYS
                    }
                )
            )
        except RuntimeError as exc:
            return await refused(f"réglages en base refusés : {exc}")
        if fresh.execution_mode != self._settings.execution_mode:
            return await refused(
                "réglages en base ignorés : le mode ne peut pas changer à chaud"
            )
        if (
            fresh.exchange.daily_timeframe_decision
            != self._settings.exchange.daily_timeframe_decision
        ):
            return await refused("changement de cadence : redémarrage requis")
        fee = getattr(self._broker, "execution_fee_pct", fresh.risk.execution_fee_pct)
        capital = await self._repo.capital_state(self._mode)
        if capital is not None and not fresh.capital_policy.enabled:
            return await refused(
                "capital existant : réconciliation requise avant changement de profil"
            )
        self._settings = replace(
            self._settings,
            risk=replace(fresh.risk, execution_fee_pct=fee),
            risk_profile=fresh.risk_profile,
            capital_policy=fresh.capital_policy,
            halt_auto_resume_hours=fresh.halt_auto_resume_hours,
        )
        ack = getattr(self._repo, "acknowledge_settings", None)
        if ack:
            if version is None:
                version = await self._repo.settings_state(self._mode)
            await ack(
                self._mode,
                version["revision"],
                {
                    "risk": public(self._settings.risk),
                    "profile": self._settings.risk_profile,
                },
            )
        self._settings_updated_at = updated_at
        return True, None

    async def _log_signal(
        self, symbol: str, signal: LLMSignal, report: CycleReport
    ) -> tuple[uuid.UUID, datetime]:
        """Persiste toute décision du modèle, y compris HOLD, SELL et HALT."""
        report.signals += 1
        if signal.degraded:
            report.degraded_calls += 1
        return await self._repo.insert_signal(
            self._mode,
            symbol,
            f"{self._settings.llm.model}{' (repli)' if signal.degraded else ''}",
            action=signal.action,
            confidence=signal.confidence,
            market_regime=signal.market_regime,
            thesis=signal.thesis,
            prompt_payload=dict(signal.prompt_payload),
            raw_response={
                **dict(signal.raw),
                "model_digest": self._model_digest,
                "lessons_digest": digest(self._lessons),
                "prompt_template_digest": digest(
                    {
                        "system": D1_SYSTEM_PROMPT
                        if self._settings.exchange.daily_timeframe_decision == "1d"
                        else SYSTEM_PROMPT,
                        "temperature": self._settings.llm.temperature,
                    }
                ),
            },
            inference_time_ms=signal.inference_ms,
            prompt_sha256=signal.prompt_sha256 or None,
            temperature=self._settings.llm.temperature,
        )

    async def _record_shadow(self, *, kind: str, symbol: str, signal: LLMSignal,
                             signal_id: uuid.UUID,
                             evaluation_id: uuid.UUID | None,
                             decision: Any | None, snapshot: Any,
                             account_state: AccountState,
                             constraints: Any) -> None:
        """Enregistre un achat non pris et son plan contrefactuel.

        Le plan est recalculé par le même pare-feu avec une confiance forcée à 1,0
        et le régime du signal. Si le pare-feu refuse encore (contre-tendance,
        volatilité, minimum exchange), la ligne reste `NO_PLAN` : on mesure le
        refus, pas un trade que le bot n'aurait jamais pu prendre.
        """
        forced = evaluate(
            TradeIntent(
                Action.BUY, 1.0,
                MarketRegime(signal.market_regime), "shadow trade",
            ),
            snapshot, account_state, constraints, self._settings.risk,
        )
        has_plan = bool(
            forced.approved
            and forced.amount is not None and forced.amount > 0
            and forced.stop_loss_price is not None
        )
        rejection_code = (
            str(decision.rejection_code) if decision is not None else "NONE"
        )
        rejection_details = (
            decision.rejection_details if decision is not None
            else "HOLD du modèle sur un setup d'entrée valide"
        )
        await self._repo.insert_shadow_trade(
            self._mode, symbol,
            kind=kind,
            action=signal.action,
            signal_id=signal_id,
            evaluation_id=evaluation_id,
            rejection_code=rejection_code,
            rejection_details=rejection_details,
            decision_price=snapshot.price,
            amount=forced.amount if has_plan else None,
            notional_quote=forced.notional_quote if has_plan else None,
            stop_loss_price=forced.stop_loss_price if has_plan else None,
            take_profit_price=forced.take_profit_price if has_plan else None,
            risk_amount_quote=(
                forced.effective_risk_quote or forced.intended_risk_quote
            ) if has_plan else None,
            plan_status="OPEN" if has_plan else "NO_PLAN",
            simulation_context={
                'version':'forward-v1', 'observed_at':self._now().isoformat(),
                'timeframe':self._settings.exchange.daily_timeframe_decision,
                'horizon_bars':42,
                'taker_fee':str(self._settings.exchange.taker_fee),
                'slippage_bps':self._settings.paper_slippage_bps,
                'policy_id':position_policy(self._settings)['id'],
                'exit_model':'FIXED_STOP_TP',
            },
        )

    async def _force_halt(self, machine: StateMachine, report: CycleReport,
                          reason: str) -> None:
        """Arrêt demandé par le modèle : même chemin que le bouton rouge, sans 2FA.

        Le modèle ne peut pas repartir tout de suite : `EMERGENCY_HALT` reste
        bloqué pendant le délai configuré (24 h par défaut), puis le cycle suivant
        reprend automatiquement. Le bouton « Reprendre » du dashboard permet de
        repartir avant l'échéance ; un panic-sell humain, lui, n'a pas d'échéance.
        """
        auto_resume_at = None
        if self._settings.halt_auto_resume_hours > 0:
            auto_resume_at = self._now() + timedelta(
                hours=self._settings.halt_auto_resume_hours
            )
        await self._repo.force_halt(
            self._mode, reason, auto_resume_at=auto_resume_at
        )
        machine.current = HALT
        report.actions.append(f"ARRÊT LLM : {reason}")

    # -- cycle -----------------------------------------------------------

    async def _event(self, stage, *, symbol=None, at=None, terminal=False, body=None):
        if self._cycle_id is None:
            return
        self._event_seq += 1
        try:
            await self._feature_repo.append_opportunity(
                self._cycle_id,
                self._event_seq,
                self._mode,
                stage,
                at or datetime.now(timezone.utc),
                symbol=symbol,
                opportunity_id=self._opportunities.get(symbol),
                terminal=terminal,
                body=body,
            )
        except Exception as exc:
            self._observation_error = type(exc).__name__
            if self._recorder:
                self._recorder.failure = "EVENT_PERSISTENCE_FAILED"

    async def _entry_enabled(self):
        if self._observation_error or (self._recorder and self._recorder.failure):
            return False
        getter = getattr(self._repo, "entry_control", None)
        return (await getter(self._mode))["enabled"] if getter else True

    async def run_once(self) -> CycleReport:
        ensure_connected = getattr(self._repo, "ensure_connected", None)
        if ensure_connected:
            await ensure_connected()
        self._observation_error = None
        if not self._capture or not hasattr(self._feature_repo, "begin_cycle"):
            async with self._repo.cycle_lock(self._mode):
                return await self._run_once()
        from app.insights.replay import Recorder

        recorder = Recorder(self._settings)
        recorder.manifest["runtime_state"] = {
            "base_settings": encode(self._base_settings),
            "settings_updated_at": encode(self._settings_updated_at),
        }
        identity = getattr(self._llm, "model_identity", None)
        if identity:
            self._model_digest = await identity()
            recorder.manifest["model_digest"] = self._model_digest
        raw = (
            self._repo,
            self._exchange,
            self._llm,
            self._broker,
            self._now,
            self._load_settings,
            self._id_factory,
            self._perf,
        )
        clock = SimpleNamespace(
            now=self._now,
            settings=self._load_settings,
            new_id=self._id_factory,
            perf=self._perf,
        )
        self._cycle_id = uuid.uuid4()
        self._event_seq = 0
        self._opportunities = {}
        self._submission = False
        try:
            await self._feature_repo.begin_cycle(
                self._cycle_id, self._mode, self._now(), recorder.manifest
            )
        except Exception as exc:
            self._cycle_id = None
            self._observation_error = type(exc).__name__
            async with self._repo.cycle_lock(self._mode):
                report = await self._run_once()
            report.errors.append(
                "observation indisponible : nouveaux achats suspendus, gestion existante maintenue"
            )
            return report
        self._recorder = recorder
        self._repo, self._exchange, self._llm, self._broker = [
            recorder.proxy(k, v)
            for k, v in zip(("repo", "exchange", "llm", "broker"), raw[:4])
        ]
        clock = recorder.proxy("clock", clock)
        self._now, self._load_settings, self._id_factory, self._perf = (
            clock.now,
            clock.settings,
            clock.new_id,
            clock.perf,
        )
        report = error = None
        try:
            async with self._repo.cycle_lock(self._mode):
                report = await self._run_once()
            return report
        except asyncio.CancelledError as exc:
            error = exc
            try:
                await self._event(
                    "EXECUTION_UNCERTAIN" if self._submission else "CYCLE_CANCELLED",
                    terminal=True, body={"error_type": "CancelledError"},
                )
            except Exception:
                pass  # L'échec de télémétrie ne doit pas effacer l'annulation.
            raise
        except Exception as exc:
            error = exc
            await self._event(
                "EXECUTION_UNCERTAIN" if self._submission else "CYCLE_FAILED",
                terminal=True,
                body={"error_type": type(exc).__name__},
            )
            raise
        finally:
            (
                self._repo,
                self._exchange,
                self._llm,
                self._broker,
                self._now,
                self._load_settings,
                self._id_factory,
                self._perf,
            ) = raw
            try:
                packet = recorder.packet(report, error)
                await self._feature_repo.finish_cycle(self._cycle_id, packet)
            except Exception:
                if report is not None:
                    report.errors.append(
                        "paquet d’observation non persisté ; cycle non rejouable"
                    )
            finally:
                self._cycle_id = None
                self._recorder = None

    async def _run_once(self) -> CycleReport:
        started = self._perf()
        await self._repo.ensure_mode(self._mode)
        if self._mode=='LIVE' and hasattr(self._broker,'recover_requests'):
            from app.runtime.execution_recovery import recover_executions
            await recover_executions(self._repo,self._broker,self._mode,self._settings.exchange.quote_currency,include_trades=True)
        commands = getattr(self._repo, "consume_observation_commands", None)
        if commands:
            await commands(self._mode)
        reloaded, reload_error = await self._refresh_settings()
        lessons_error: str | None = None
        try:
            self._lessons = await self._repo.active_lessons(self._mode)
        except Exception as exc:  # noqa: BLE001 — une base indisponible ne doit pas
            # empêcher le cycle de continuer sans mémoire.
            self._lessons = []
            lessons_error = f"leçons illisibles : {exc}"
        state, cooldown_until = await self._repo.state(self._mode)
        now = self._now()

        # Un arrêt demandé par le modèle porte une échéance. Passée cette échéance,
        # le bot repart seul ; un arrêt humain (panic-sell, kill-switch) n'a pas
        # d'échéance et reste bloqué jusqu'à une reprise explicite.
        auto_resumed = False
        if state == HALT:
            details = await self._repo.halt_details(self._mode)
            if (
                details and details.startswith("LLM HALT")
                and cooldown_until is not None
                and now >= cooldown_until
            ):
                await self._repo.force_resume(
                    self._mode,
                    "reprise automatique après arrêt du modèle",
                    who="auto_llm_cooldown",
                )
                state, cooldown_until = await self._repo.state(self._mode)
                auto_resumed = True

        machine = StateMachine(self._repo, self._mode, state)
        report = CycleReport(mode=self._mode, state_before=state, state_after=state)
        if reloaded:
            report.actions.append("réglages du dashboard rechargés")
        if reload_error:
            report.errors.append(reload_error)
        if lessons_error:
            report.errors.append(lessons_error)
        if auto_resumed:
            report.actions.append("reprise automatique après arrêt du modèle")

        if state == POST_TRADE_SWEEP:
            await self._repo.force_halt(self._mode, "ancienne clôture incomplète : réconciliation comptable requise")
            report.errors.append("clôture historique incomplète : aucune vente ni reprise automatique")
            report.state_after = HALT
            return report
        if state in (LOCKED, HALT):
            report.actions.append(f"état {state} : aucune action automatique")
            report.elapsed_ms = int((self._perf() - started) * 1000)
            return report

        # Reprise après arrêt brutal : l'état EVALUATING/EXECUTING n'est pas un état
        # de repos. On le normalise avant d'agir, sinon la machine refuserait sa
        # propre transition et le bot resterait bloqué jusqu'à intervention.
        if state in (EVALUATING, EXECUTING):
            position = await self._repo.open_position(self._mode)
            target = IN_POSITION if position is not None else IDLE
            await machine.go(target, "reprise après interruption")
            state = machine.current

        if state == COOLDOWN:
            if cooldown_until is not None and now < cooldown_until:
                report.actions.append(f"cooldown jusqu'à {cooldown_until:%H:%M} UTC")
                report.elapsed_ms = int((self._perf() - started) * 1000)
                return report
            await machine.go(IDLE, "cooldown expiré")

        account = await self._current_account()
        capital = await self._repo.capital_state(self._mode)
        if capital is not None and not self._settings.capital_policy.enabled:
            raise RuntimeError("capital par paliers existant : désactivation interdite sans clôture comptable")
        if self._settings.capital_policy.enabled:
            capital = await self._repo.ensure_capital(
                self._mode, self._settings.exchange.quote_currency, account.equity,
                account.reserve_quote, now)
            account = replace(account, reserve_quote=capital.reserve_quote, growth_quote=capital.growth_quote)
        position = await self._repo.open_position(self._mode)

        if position is not None:
            prior=self._settings
            getter=getattr(self._repo,'position_policy',None)
            self._legacy_policy=False
            if getter:
                policy=await getter(position.id)
                if policy:self._settings=apply_policy(self._settings,policy)
                else:
                    self._legacy_policy=True
                    await self._event('LEGACY_POLICY_MISSING',symbol=position.symbol,body={'position_id':str(position.id)})
            try:
                await self._monitor(machine,position,account,report)
            finally:
                self._settings=prior;self._legacy_policy=False
        else:
            await self._evaluate_cycle(machine, account, report)

        report.state_after = machine.current
        report.elapsed_ms = int((self._perf() - started) * 1000)
        return report

    # -- position ouverte ------------------------------------------------

    async def _monitor(self, machine: StateMachine, position: PositionRow,
                       account: AccountSnapshot, report: CycleReport) -> None:
        if machine.current != IN_POSITION:
            # Redémarrage pendant une position : la base fait autorité sur l'état.
            await machine.go(IN_POSITION, "position ouverte retrouvée en base")

        if self._mode == "LIVE":
            pending = await self._repo.pending_exit(position.id)
            if pending:
                if pending["fill"] is None:
                    await self._repo.force_halt(self._mode, "sortie réelle au résultat inconnu : réconciliation obligatoire")
                    machine.current = HALT
                    report.errors.append("sortie incertaine : aucune seconde vente envoyée")
                    return
                values = json.loads(pending["fill"]) if isinstance(pending["fill"], str) else dict(pending["fill"])
                for key in ("price", "amount", "notional_quote", "fee_quote", "slippage_quote"):
                    values[key] = Decimal(values[key])
                fill = Fill(**values)
                await self._close_trade(machine, position, account, fill.price, pending["reason"], report,
                                        purpose=pending["purpose"], halt=pending["halt"], fill=fill)
                return

        # En live, l'exchange fait autorité avant toute décision locale. Sans
        # cette lecture, un stop déclenché pendant l'arrêt du bot ferait envoyer
        # une seconde vente sur une position déjà plate.
        live_state = None
        if hasattr(self._broker, "position_state"):
            try:
                live_state = await self._broker.position_state(
                    position.symbol, position.amount, position.stop_loss_price
                )
            except LiveExecutionError as exc:
                report.errors.append(f"{position.symbol} : {exc}")
                return
            if live_state.base_total < position.amount * Decimal("0.999"):
                if live_state.base_total > Decimal("0.00000001"):
                    report.errors.append(
                        f"{position.symbol} : position partielle "
                        f"({live_state.base_total} sur {position.amount}) — "
                        "aucune vente automatique"
                    )
                    return
                try:
                    fill = await self._broker.last_sell_fill(
                        position.symbol,
                        since=int(position.opened_at.timestamp() * 1000),
                    )
                except LiveExecutionError as exc:
                    report.errors.append(f"{position.symbol} : {exc}")
                    return
                if fill is None:
                    report.errors.append(
                        f"{position.symbol} : base plate mais aucune vente "
                        "retrouvée dans l'historique — réconciliation requise"
                    )
                    return
                if fill.price <= position.stop_loss_price:
                    reason, purpose = "STOP_LOSS", "STOP_LOSS"
                elif (position.take_profit_price is not None
                      and fill.price >= position.take_profit_price):
                    reason, purpose = "TAKE_PROFIT", "TAKE_PROFIT"
                else:
                    reason, purpose = "EXCHANGE_EXIT", "EMERGENCY_EXIT"
                await self._close_trade(
                    machine, position, account, fill.price, reason, report,
                    purpose=purpose, fill=fill,
                )
                return
            if live_state.base_total > position.amount * Decimal("1.001"):
                report.errors.append(
                    f"{position.symbol} : solde de base {live_state.base_total} "
                    f"supérieur à la position {position.amount} — compte partagé ? "
                    "aucune vente automatique"
                )
                return
            if not live_state.stop_ok:
                await self._close_trade(machine, position, account, Decimal(0), "STOP_MISSING", report,
                                        purpose="EMERGENCY_EXIT", halt=True)
                return

        try:
            state = build_snapshot(
                self._exchange, position.symbol, now=self._now(),
                decision_timeframe=self._settings.exchange.daily_timeframe_decision,
                entry_timeframe=self._settings.exchange.entry_timeframe,
                with_d1=self._settings.risk.entry_mode is EntryMode.SWING_MACRO_D1,
                chandelier_period=self._settings.risk.swing_chandelier_period,
            )
        except FeedError as exc:
            report.errors.append(f"{position.symbol} : {exc}")
            return

        candles = closed_candles(
            self._exchange, position.symbol,
            self._settings.exchange.daily_timeframe_decision, 720,
        )
        if not candles:
            report.errors.append(f"{position.symbol} : aucune bougie clôturée")
            return
        last = candles[-1]
        if state.last_close_at <= position.opened_at:
            report.actions.append("aucune bougie clôturée depuis l'entrée")
            return
        if live_state is None:
            # Stop et curseur forment une seule écriture. Aucun signal LLM ne sert
            # de preuve d'application d'un stop ; les bougies manquées sont rejouées.
            if position.monitor_cursor_at is None:
                raise RuntimeError("position papier historique sans curseur : réconciliation requise")
            from app.market.feed import TIMEFRAME_MS
            period_ms = TIMEFRAME_MS[self._settings.exchange.daily_timeframe_decision]
            cursor = position.monitor_cursor_at
            pending_candles = [c for c in candles if datetime.fromtimestamp((c[0]+period_ms)/1000, timezone.utc) > cursor]
            for candle in pending_candles:
                close_at = datetime.fromtimestamp((candle[0]+period_ms)/1000, timezone.utc)
                # Un trou ne peut pas être interprété comme une absence de stop touché.
                expected = datetime.fromtimestamp(((int(cursor.timestamp()*1000)//period_ms)+1)*period_ms/1000, timezone.utc)
                if close_at != expected:
                    raise RuntimeError("trou de surveillance papier : historique à réconcilier avant reprise")
                trigger = self._broker.exit_trigger(position.stop_loss_price, position.take_profit_price, candle)
                if trigger:
                    exit_price, reason = trigger
                    await self._close_trade(machine, position, account, exit_price, reason, report,
                                            purpose="STOP_LOSS" if reason == "STOP_LOSS" else "TAKE_PROFIT")
                    return
                candidate = position.stop_loss_price if self._legacy_policy else advance_profit_stop(
                    entry_price=position.entry_price,
                    initial_distance=position.risk_amount_quote / position.amount,
                    current_stop=position.stop_loss_price, close=Decimal(str(candle[4])),
                    price_tick=state.info.constraints.price_tick, config=self._settings.risk)
                await self._repo.advance_paper_position(position, candidate, close_at)
                if candidate > position.stop_loss_price:
                    report.actions.append(f"{position.symbol} : stop remonté à {candidate}")
                position = replace(position, stop_loss_price=candidate, monitor_cursor_at=close_at)
                cursor = close_at
        elif position.take_profit_price is not None and Decimal(str(last[2])) >= position.take_profit_price:
            await self._close_trade(machine, position, account, position.take_profit_price,
                                    "TAKE_PROFIT", report, purpose="TAKE_PROFIT")
            return

        if self._legacy_policy:
            report.actions.append("position historique : stop/TP conservés ; adoption de politique requise")
            price=last_price(self._exchange,position.symbol)
            await self._repo.snapshot_equity(self._mode,equity=account.free_quote+position.amount*price+account.reserve_quote+account.growth_quote,
                free_quote=account.free_quote,position_value_quote=position.amount*price,symbol=position.symbol,source='LEGACY_MONITOR')
            return

        # -- gestion swing D1 : seuil de rentabilité puis sortie de tendance ----
        if self._settings.risk.entry_mode is EntryMode.SWING_MACRO_D1:
            d1 = state.context.d1
            if d1 is not None:
                price_now = last_price(self._exchange, position.symbol)
                current_stop = position.stop_loss_price
                if (self._settings.risk.swing_breakeven_r > 0
                        and current_stop < position.entry_price):
                    r_distance = position.entry_price - current_stop
                    target = (
                        position.entry_price
                        + self._settings.risk.swing_breakeven_r * r_distance
                    )
                    if price_now >= target:
                        breakeven = position.entry_price * (
                            Decimal(1) + self._settings.exchange.taker_fee * Decimal(2)
                        )
                        moved = await self._move_stop(
                            position, breakeven, report, live_state=live_state,
                            label="+1 R atteint",
                        )
                        if moved > current_stop:
                            current_stop = moved
                            position = replace(position, stop_loss_price=moved)

                if self._settings.risk.swing_exit_mode is SwingExitMode.CHANDELIER:
                    candidate = update_chandelier_stop(
                        current_stop,
                        d1.highest_high_chandelier,
                        d1.atr_14,
                        self._settings.risk.swing_chandelier_atr_multiple,
                    )
                    if should_exit_chandelier(d1.last_close, candidate):
                        await self._close_trade(
                            machine, position, account, price_now,
                            "TREND_EXIT_CHANDELIER", report, purpose="LLM_EXIT",
                        )
                        return
                    if candidate > current_stop:
                        moved = await self._move_stop(
                            position, candidate, report, live_state=live_state,
                            label="trailing Chandelier",
                        )
                        if moved > current_stop:
                            current_stop = moved
                            position = replace(position, stop_loss_price=moved)
                else:
                    if (self._settings.risk.swing_exit_below_ema20
                            and d1.last_close < d1.ema_20):
                        await self._close_trade(
                            machine, position, account, price_now,
                            "TREND_EXIT_EMA20", report, purpose="LLM_EXIT",
                        )
                        return
                    if d1.bearish_divergence:
                        await self._close_trade(
                            machine, position, account, price_now,
                            "TREND_EXIT_BOLLINGER", report, purpose="LLM_EXIT",
                        )
                        return

        # Une bougie clôturée = une décision de sortie au maximum. Sans ce garde,
        # un cycle toutes les minutes redemanderait au modèle la même bougie.
        last_signal = await self._repo.last_signal_at(self._mode, position.symbol)
        if last_signal is None or last_signal < state.last_close_at:
            signal = await self._llm.propose(
                position.symbol, state.snapshot, state.context,
                has_position=True,
                trend_buffer_pct=self._settings.risk.trend_buffer_pct,
                lessons=self._lessons,
                decision_timeframe=self._settings.exchange.daily_timeframe_decision,
            )
            await self._log_signal(position.symbol, signal, report)
            action = Action(signal.action)
            confidence = Decimal(str(signal.confidence))

            if action is Action.HALT and confidence >= self._settings.risk.min_halt_confidence:
                price = last_price(self._exchange, position.symbol)
                await self._close_trade(
                    machine, position, account, price, "LLM_HALT", report,
                    purpose="LLM_EXIT", halt=True,
                )
                return

            if action is Action.SELL:
                decision = evaluate_exit(
                    TradeIntent(action, float(confidence),
                                MarketRegime(signal.market_regime), signal.thesis),
                    position.symbol,
                    OpenPosition(position.symbol, position.amount, position.entry_price),
                    self._settings.risk,
                )
                if decision.approved:
                    price = last_price(self._exchange, position.symbol)
                    await self._close_trade(
                        machine, position, account, price, "LLM_SELL", report,
                        purpose="LLM_EXIT",
                    )
                    return
                report.actions.append(
                    f"{position.symbol} : SELL refusé {decision.rejection_code} "
                    f"({decision.rejection_details})"
                )
            elif action is Action.HALT:
                report.actions.append(
                    f"{position.symbol} : HALT ignoré (confidence {confidence} < "
                    f"{self._settings.risk.min_halt_confidence})"
                )

        price = last_price(self._exchange, position.symbol)
        value = position.amount * price
        await self._repo.snapshot_equity(
            self._mode, equity=account.free_quote + value + account.reserve_quote + account.growth_quote,
            free_quote=account.free_quote,
            position_value_quote=value, symbol=position.symbol, source="MONITOR",
        )
        report.actions.append(
            f"{position.symbol} : stop {position.stop_loss_price} · "
            f"prix {price} · aucune sortie"
        )

    async def _move_stop(self, position: PositionRow, new_stop: Decimal,
                         report: CycleReport, *, live_state: Any,
                         label: str) -> Decimal:
        """Déplace le stop local, et le stop distant en live, sans jamais le baisser.

        En papier, la base fait foi. En live, l'ordre distant est remplacé
        **avant** la mise à jour locale : si l'exchange refuse, la base garde
        l'ancien niveau et la position reste protégée par lui.
        """
        if new_stop <= position.stop_loss_price:
            return position.stop_loss_price
        if live_state is not None:
            stop_id = self._stop_ids.get(position.id) or getattr(live_state, "stop_order_id", None)
            update = getattr(self._broker, "update_stop_loss_order", None)
            if not stop_id or not callable(update):
                report.errors.append(
                    f"{position.symbol} : stop distant non déplaçable "
                    f"(id={stop_id!r}, broker={type(self._broker).__name__})"
                )
                return position.stop_loss_price
            try:
                new_id=await self._maybe_await(update(position.symbol,position.amount,stop_id,new_stop))
                self._stop_ids[position.id]=new_id
                persist=getattr(self._repo,'update_protective_stop',None)
                if persist:await persist(position.id,new_stop,new_id)

            except LiveExecutionError as exc:
                report.errors.append(
                    f"{position.symbol} : {label} — stop distant inchangé : {exc}"
                )
                return position.stop_loss_price
        await self._repo.update_position_stop(position.id, new_stop)
        report.actions.append(
            f"{position.symbol} : {label}, stop remonté à {new_stop:.4f}"
        )
        return new_stop

    async def _close_trade(self, machine: StateMachine, position: PositionRow,
                           account: AccountSnapshot, exit_price: Decimal, reason: str,
                           report: CycleReport, *, purpose: str,
                           halt: bool = False, fill: Any = None) -> None:
        """Séquence unique de clôture : stop, TP, SELL LLM et HALT LLM."""
        live = self._mode == "LIVE"
        if fill is None:
            if live:
                await self._repo.prepare_exit(self._mode, position.id, reason, purpose, halt)
            if reason == "STOP_MISSING":
                fill = await self._broker.emergency_exit(position.amount, symbol=position.symbol,
                                                         stop_price=position.stop_loss_price)
            else:
                fill = await self._maybe_await(self._broker.exit(
                    position.amount, exit_price, symbol=position.symbol, stop_price=position.stop_loss_price))
            if live:
                await self._repo.confirm_exit(position.id, fill)
        if live and hasattr(self._repo,'account_exit'):
            live_cash=await self._broker.free_quote()
            async with self._repo.transaction():
                result=await self._repo.account_exit(self._mode,position.symbol,fill,purpose=purpose,reason=reason,
                    quote=self._settings.exchange.quote_currency,taker_fee=self._broker.fee(Decimal(1)),emergency=halt)
                await self._repo.clear_exit(position.id)
                reserve=account.reserve_quote
                if result and result['remaining']==0:
                    deferred=await self._repo.deferred_sweep_quote(self._mode)
                    plan=plan_sweep(result['total_net'],self._settings.sweep_ratio,self._settings.sweep_min_transfer_quote,deferred_quote=deferred)
                    if plan.applies:
                        reserve+=plan.swept_quote
                        await self._repo.insert_sweep(self._mode,position_id=position.id,quote_currency=self._settings.exchange.quote_currency,
                            realized_net_pnl=result['total_net'],sweep_ratio=self._settings.sweep_ratio,computed_sweep_quote=plan.computed_quote,
                            swept_amount=plan.swept_quote,retained_trading_capital=live_cash-reserve,status=plan.status,
                            next_retry_at=None,error_message=plan.reason)
                remaining=result['remaining'] if result else Decimal(0)
                value=remaining*fill.price
                await self._repo.snapshot_equity(self._mode,equity=live_cash+value,free_quote=max(Decimal(0),live_cash-reserve),
                    position_value_quote=value,symbol=position.symbol,source='LIVE_EXIT')
                if remaining>0 or halt:
                    await self._repo.force_halt(self._mode,'sortie partielle ou arrêt : vérifier les protections et le compte')
                    machine.current=HALT
                else:
                    await machine.go(POST_TRADE_SWEEP,'sortie réelle comptabilisée')
                    await self._repo.set_cooldown(self._mode,self._now()+timedelta(minutes=COOLDOWN_MINUTES))
                    await machine.go(COOLDOWN,'refroidissement après sortie réelle')
                report.position_closed=f'{position.symbol} : fill {fill.amount}, reliquat {remaining}'
            return
        if fill.amount != position.amount:
            raise RuntimeError("fill partiel : réconciliation requise, aucune comptabilité complète inventée")
        live_cash = await self._maybe_await(self._broker.free_quote()) if hasattr(self._broker, "free_quote") else None
        async with self._repo.transaction():
            await self._book_close(machine, position, account, reason, report,
                                   purpose=purpose, halt=halt, fill=fill, live_cash=live_cash)

    async def _book_close(self, machine, position, account, reason, report, *, purpose, halt, fill, live_cash):
        entry_fee = await self._entry_fee(position)
        gross = (fill.price - position.entry_price) * position.amount
        fees = entry_fee + fill.fee_quote
        net = gross - fees
        if self._settings.capital_policy.enabled:
            net = net.quantize(Decimal("0.0000000001"))

        await machine.go(POST_TRADE_SWEEP, f"sortie {reason}", code="NONE",
                         context={"exit_price": str(fill.price), "reason": reason})
        await self._repo.insert_order(
            self._mode, client_order_id=self._client_id(position.symbol, "EXIT"),
            symbol=position.symbol, side="SELL", order_type="MARKET",
            purpose=purpose, status="FILLED", requested_amount=position.amount,
            filled_amount=position.amount, requested_price=None,
            stop_price=position.stop_loss_price if reason == "STOP_LOSS" else None,
            average_fill_price=fill.price, fee_amount=fill.fee_quote,
            fee_currency=self._settings.exchange.quote_currency,
            slippage_quote=fill.slippage_quote, position_id=position.id,
        )
        await self._repo.close_position(
            position.id, exit_price=fill.price, closed_amount=position.amount,
            gross_pnl_quote=gross, total_fees_quote=fees, net_pnl_quote=net,
            exit_reason=reason,
        )
        consecutive = account.consecutive_losses + 1 if net < 0 else 0
        reserve = account.reserve_quote
        growth = account.growth_quote
        cash = (live_cash - reserve - growth if live_cash is not None
                else account.free_quote + fill.notional_quote - fill.fee_quote)
        sweep_note = ""
        if self._settings.capital_policy.enabled:
            capital = await self._repo.allocate_capital(
                self._mode, position.id, net, self._now(), self._settings.capital_policy,
                allow_promotion=not halt)
            cash, reserve, growth = capital.active_quote, capital.reserve_quote, capital.growth_quote
            sweep_note = f" · réserve {reserve} · cagnotte {growth} · paliers {capital.promotions}"
        else:
            deferred = await self._repo.deferred_sweep_quote(self._mode)
            plan = plan_sweep(net, self._settings.sweep_ratio,
                              self._settings.sweep_min_transfer_quote, deferred_quote=deferred)
            if plan.applies:
                cash -= plan.swept_quote
                reserve += plan.swept_quote
                await self._repo.insert_sweep(
                    self._mode, position_id=position.id,
                    quote_currency=self._settings.exchange.quote_currency,
                    realized_net_pnl=net, sweep_ratio=self._settings.sweep_ratio,
                    computed_sweep_quote=plan.computed_quote, swept_amount=plan.swept_quote,
                    retained_trading_capital=cash, status=plan.status,
                    next_retry_at=(self._now()+timedelta(days=1)) if plan.status == "SKIPPED_BELOW_MINIMUM" else None,
                    error_message=plan.reason)
                sweep_note = f" · écrémé {plan.swept_quote}" if plan.swept_quote > 0 else f" · écrémage reporté ({plan.reason})"
        if cash < 0:
            raise RuntimeError("réserve supérieure au cash : réconciliation requise")
        equity = cash + reserve + growth
        await self._repo.update_after_close(
            self._mode, consecutive_losses=consecutive, equity=equity
        )
        await self._repo.snapshot_equity(
            self._mode, equity=equity, free_quote=cash, position_value_quote=Decimal(0),
            symbol=position.symbol, source="EXIT",
        )
        if self._mode == "LIVE":
            await self._repo.clear_exit(position.id)
        report.position_closed = (
            f"{position.symbol} {reason} @ {fill.price} · net {net:.4f} "
            f"{self._settings.exchange.quote_currency}{sweep_note}"
        )

        if halt:
            auto_resume_at = None
            if reason == "LLM_HALT" and self._settings.halt_auto_resume_hours > 0:
                auto_resume_at = self._now() + timedelta(
                    hours=self._settings.halt_auto_resume_hours
                )
            await self._repo.force_halt(
                self._mode, f"{'LLM HALT' if reason == 'LLM_HALT' else reason} : {position.symbol} soldé à {fill.price}",
                auto_resume_at=auto_resume_at,
            )
            machine.current = HALT
            report.actions.append("ARRÊT LLM : position soldée, bot en EMERGENCY_HALT")
            return

        await self._repo.set_cooldown(
            self._mode, self._now() + timedelta(minutes=COOLDOWN_MINUTES)
        )
        await machine.go(COOLDOWN, "post-trade, refroidissement")

    # -- pas de position -------------------------------------------------

    async def _evaluate_cycle(self, machine: StateMachine, account: AccountSnapshot,
                              report: CycleReport) -> None:
        if not await self._entry_enabled():
            await self._event('ENTRY_DISABLED',terminal=True)
            report.actions.append("nouveaux achats suspendus ; gestion des positions maintenue")
            return
        await machine.go(EVALUATING, "cycle d'évaluation")
        chosen: tuple[SymbolState, LLMSignal, Any, uuid.UUID, uuid.UUID, datetime] | None = None
        account_state = self._account_state(account)

        candidates=[]
        for symbol in self._settings.exchange.allowed_symbols:
            try:
                state = build_snapshot(
                    self._exchange, symbol, now=self._now(),
                    decision_timeframe=self._settings.exchange.daily_timeframe_decision,
                    entry_timeframe=self._settings.exchange.entry_timeframe,
                    with_d1=self._settings.risk.entry_mode is EntryMode.SWING_MACRO_D1,
                    chandelier_period=self._settings.risk.swing_chandelier_period,
                    validate_windows=True,
                )
            except FeedError as exc:
                await self._event("DATA_UNAVAILABLE",symbol=symbol,terminal=True,body={"reason":str(exc)})
                report.errors.append(f"{symbol} : {exc}")
                continue

            from app.market.feed import TIMEFRAME_MS
            period=TIMEFRAME_MS[self._settings.exchange.daily_timeframe_decision]
            expected=int(self._now().timestamp()*1000)//period*period
            if int(state.last_close_at.timestamp()*1000)<expected:
                await self._event('DATA_UNAVAILABLE',symbol=symbol,terminal=True,body={'reason':'clôture attendue absente'})
                report.errors.append(f'{symbol} : clôture attendue absente ; achat refusé')
                continue

            # Une bougie clôturée = une évaluation. Sans ce garde, une boucle qui
            # tourne toutes les minutes redemande au modèle la même bougie, pollue
            # les statistiques et peut rouvrir une position sitôt la précédente
            # fermée. La comparaison traverse les redémarrages : le dernier signal
            # est relu en base.
            self._opportunities[symbol]=digest({'symbol':symbol,'candle':state.last_close_at.isoformat(),'policy':position_policy(self._settings)['id']})
            await self._event('OBSERVED',symbol=symbol,at=state.last_close_at)
            last_signal = await self._repo.last_signal_at(self._mode, symbol)
            if last_signal is not None and last_signal >= state.last_close_at:
                await self._event("ALREADY_EVALUATED",symbol=symbol,terminal=True)
                report.skipped += 1
                report.actions.append(
                    f"{symbol} : bougie clôturée à "
                    f"{state.last_close_at:%Y-%m-%d %H:%M} UTC déjà évaluée"
                )
                continue

            # La règle déterministe décide si la structure mérite un appel au
            # modèle. Elle est plus stricte que le pare-feu : elle cherche un
            # pullback dans une tendance, pas n'importe quelle zone légale.
            if self._settings.risk.entry_rule_enabled:
                setup = evaluate_entry(state.snapshot, state.context, self._settings.risk)
                if not setup.triggered:
                    await self._event("NO_SETUP",symbol=symbol,terminal=True,body={"reason":setup.reason})
                    report.actions.append(f"{symbol} : pas de setup ({setup.reason})")
                    continue

            signal = await self._llm.propose(
                symbol, state.snapshot, state.context, has_position=False,
                trend_buffer_pct=self._settings.risk.trend_buffer_pct,
                lessons=self._lessons,
                decision_timeframe=self._settings.exchange.daily_timeframe_decision,
            )
            signal_id, signal_at = await self._log_signal(symbol, signal, report)
            await self._event('SIGNAL',symbol=symbol,body={'signal_id':str(signal_id),'action':signal.action,'confidence':signal.confidence})
            action = Action(signal.action)
            confidence = Decimal(str(signal.confidence))

            if signal.degraded:
                await self._event("LLM_UNAVAILABLE",symbol=symbol,terminal=True)
                report.actions.append(
                    f"{symbol} : HOLD de repli ({signal.degradation_reason})"
                )
                continue
            if action is Action.HALT:
                await self._event("LLM_HALT",symbol=symbol,terminal=True)
                if confidence >= self._settings.risk.min_halt_confidence:
                    await self._force_halt(
                        machine, report,
                        f"LLM HALT sur {symbol} : {signal.thesis[:120]}",
                    )
                    return
                report.actions.append(
                    f"{symbol} : HALT ignoré (confidence {confidence} < "
                    f"{self._settings.risk.min_halt_confidence})"
                )
                continue
            if action is Action.SELL:
                await self._event("SELL_WITHOUT_POSITION",symbol=symbol,terminal=True)
                # Un SELL sans position ne peut pas créer un short : il est loggué
                # puis ignoré. Le garde-fou de sortie n'a rien à liquider.
                report.actions.append(f"{symbol} : SELL sans position, ignoré")
                continue
            if action is Action.HOLD:
                await self._event("LLM_HOLD",symbol=symbol,terminal=True)
                await self._record_shadow(
                    kind="MODEL_HOLD", symbol=symbol, signal=signal,
                    signal_id=signal_id, evaluation_id=None, decision=None,
                    snapshot=state.snapshot, account_state=account_state,
                    constraints=state.info.constraints,
                )
                report.actions.append(f"{symbol} : HOLD ({confidence:.2f})")
                continue

            # Prix rafraîchi après l'inférence : la décision porte sur le prix
            # d'exécution, pas sur celui d'il y a quatre secondes.
            price = last_price(self._exchange, symbol)
            evaluated_at = self._now()
            fresh = replace(state.snapshot, price=price, tick_at=evaluated_at, now=evaluated_at)
            intent = TradeIntent(
                action, float(confidence),
                MarketRegime(signal.market_regime), signal.thesis,
            )
            eval_started = self._perf()
            decision = evaluate(
                intent, fresh, account_state, state.info.constraints, self._settings.risk
            )
            eval_ms = int((self._perf() - eval_started) * 1000)
            evaluation_id, _ = await self._repo.insert_evaluation(
                self._mode, symbol, signal_id=signal_id, signal_created_at=signal_at,
                decision=decision, snapshot=fresh, account=account,
                evaluation_time_ms=eval_ms,
                decision_timeframe=self._settings.exchange.daily_timeframe_decision,
            )
            report.evaluations += 1
            if decision.approved:
                await self._event('APPROVED',symbol=symbol,body={'decision_id':str(evaluation_id)})
                cost={'available':False,'reason':'BOOK_UNAVAILABLE'}
                if hasattr(self._exchange,'fetch_order_book'):
                    try:
                        cost=estimate_costs(self._exchange.fetch_order_book(symbol,limit=20),decision.amount,
                            self._settings.risk.execution_fee_pct,now=self._now())
                    except Exception:
                        cost={'available':False,'reason':'BOOK_UNAVAILABLE'}
                candidates.append({'symbol':symbol,'observed_at':self._now(),'approved':True,'cost':cost,
                    'structure_atr':abs(state.snapshot.price-state.context.ema_50_h4)/state.snapshot.atr_14_h4,
                    'choice':(state,signal,decision,signal_id,evaluation_id,signal_at)})
                report.approved += 1
                report.actions.append(
                    f"{symbol} : APPROUVÉ {decision.amount} @ {price} · "
                    f"risque {decision.effective_risk_quote:.4f}"
                )
                if chosen is None:
                    chosen = (state, signal, decision, signal_id, evaluation_id, signal_at)
            else:
                await self._event('RISK_REJECTED',symbol=symbol,terminal=True,body={'code':str(decision.rejection_code)})
                await self._record_shadow(
                    kind="GATE_REJECTED_BUY", symbol=symbol, signal=signal,
                    signal_id=signal_id, evaluation_id=evaluation_id,
                    decision=decision, snapshot=fresh,
                    account_state=account_state,
                    constraints=state.info.constraints,
                )
                report.actions.append(f"{symbol} : refusé {decision.rejection_code}")

        ranked=rank_candidates(candidates,now=self._now())
        if self._settings.ranking_mode=='paper_select':
            if self._mode!='PAPER':raise RuntimeError('sélection interdite hors papier')
            chosen=next((r['choice'] for r in ranked if r['eligible']),None)
        await self._event('RANKING',body={'mode':self._settings.ranking_mode,'candidates':[
            {k:v for k,v in r.items() if k!='choice'} for r in ranked]})
        for candidate in candidates:
            if chosen is None or candidate['symbol']!=chosen[0].symbol:
                await self._event('NOT_SELECTED',symbol=candidate['symbol'],terminal=True)
        if chosen is None:
            await machine.go(IDLE, "aucune décision approuvée")
            return

        state, signal, decision, signal_id, evaluation_id, signal_at = chosen
        self._selected_confidence = signal.confidence
        self._selected_regime = MarketRegime(signal.market_regime)
        await machine.go(EXECUTING, f"entrée approuvée sur {state.symbol}")
        await self._enter(
            machine,
            state,
            decision,
            signal_id,
            evaluation_id,
            account,
            report,
            signal_created_at=signal_at,
        )

    async def _enter(
        self,
        machine: StateMachine,
        state: SymbolState,
        decision: Any,
        signal_id: uuid.UUID,
        evaluation_id: uuid.UUID,
        account: AccountSnapshot,
        report: CycleReport,
        *,
        signal_created_at: datetime,
    ) -> None:
        current, _ = await self._repo.state(self._mode)
        if current != EXECUTING or not await self._entry_enabled():
            await self._event("ENTRY_DISABLED", symbol=state.symbol, terminal=True)
            if current == EXECUTING:
                await machine.go(IDLE, "achat suspendu avant envoi")
            else:
                machine.current = current
            return
        policy = position_policy(self._settings)
        saver = getattr(self._repo, "save_policy", None)
        if saver:
            await saver(policy)
        price = last_price(self._exchange, state.symbol)
        fresh = replace(
            state.snapshot, price=price, tick_at=self._now(), now=self._now()
        )
        # Every candidate, including the selected ranked candidate, is evaluated again.
        latest = await self._current_account()
        position = await self._repo.open_position(self._mode)
        decision = evaluate(
            TradeIntent(
                Action.BUY,
                float(self._selected_confidence),
                getattr(self, "_selected_regime", MarketRegime.TRENDING_UP),
            ),
            fresh,
            self._account_state(latest, (position.symbol,) if position else ()),
            state.info.constraints,
            self._settings.risk,
        )
        evaluation_id, _ = await self._repo.insert_evaluation(
            self._mode,
            state.symbol,
            signal_id=signal_id,
            signal_created_at=signal_created_at,
            decision=decision,
            snapshot=fresh,
            account=latest,
            evaluation_time_ms=0,
            decision_timeframe=self._settings.exchange.daily_timeframe_decision,
        )
        if not decision.approved:
            await self._event(
                "REVALIDATION_REJECTED",
                symbol=state.symbol,
                terminal=True,
                body={"code": str(decision.rejection_code)},
            )
            await machine.go(IDLE, "revalidation refusée")
            return
        current, _ = await self._repo.state(self._mode)
        if current != EXECUTING or not await self._entry_enabled():
            machine.current = current
            if current == EXECUTING:
                await machine.go(IDLE, "achat suspendu pendant la revalidation")
            await self._event("ENTRY_DISABLED", symbol=state.symbol, terminal=True)
            return
        await self._event(
            "SUBMITTED", symbol=state.symbol, body={"policy_id": policy["id"]}
        )
        if not await self._entry_enabled():
            await machine.go(IDLE,'achat suspendu avant autorisation de l’envoi')
            await self._event('ENTRY_DISABLED',symbol=state.symbol,terminal=True)
            return
        self._submission = True
        try:
            fill = await self._maybe_await(
                self._broker.entry(decision, price, symbol=state.symbol)
            )
            entry_client_id = self._client_id(state.symbol, "ENTRY")
            stop_client_id = self._client_id(state.symbol, "STOP")
            distance = fill.price - decision.stop_loss_price
            r_multiple = (
                (decision.take_profit_price - fill.price) / distance
                if decision.take_profit_price is not None and distance > 0
                else None
            )
            async with self._repo.transaction():
                position_id = await self._repo.create_position(
                    self._mode,
                    symbol=state.symbol,
                    entry_price=fill.price,
                    amount=fill.amount,
                    notional_quote=fill.notional_quote,
                    stop_loss_price=decision.stop_loss_price,
                    take_profit_price=decision.take_profit_price,
                    risk_amount_quote=decision.effective_risk_quote
                    or decision.intended_risk_quote,
                    r_multiple_at_tp=r_multiple,
                    signal_id=signal_id,
                    evaluation_id=evaluation_id,
                    protective_order_id=fill.stop_order_id or stop_client_id,
                )
                attach = getattr(self._repo, "attach_policy", None)
                if attach:
                    await attach(self._mode, position_id, policy)
                await self._repo.insert_order(
                    self._mode,
                    client_order_id=entry_client_id,
                    symbol=state.symbol,
                    side="BUY",
                    order_type="MARKET",
                    purpose="ENTRY",
                    status="FILLED",
                    requested_amount=fill.amount,
                    filled_amount=fill.amount,
                    requested_price=None,
                    stop_price=None,
                    average_fill_price=fill.price,
                    fee_amount=fill.fee_quote,
                    fee_currency=self._settings.exchange.quote_currency,
                    slippage_quote=fill.slippage_quote,
                    exchange_order_id=fill.order_id,
                    position_id=position_id,
                    risk_evaluation_id=evaluation_id,
                )
                await self._repo.insert_order(
                    self._mode,
                    client_order_id=stop_client_id,
                    symbol=state.symbol,
                    side="SELL",
                    order_type="STOP_LOSS",
                    purpose="STOP_LOSS",
                    status="SUBMITTED",
                    requested_amount=fill.amount,
                    filled_amount=Decimal(0),
                    requested_price=None,
                    stop_price=decision.stop_loss_price,
                    average_fill_price=Decimal(0),
                    fee_amount=Decimal(0),
                    fee_currency=self._settings.exchange.quote_currency,
                    slippage_quote=None,
                    position_id=position_id,
                    risk_evaluation_id=evaluation_id,
                )
                cash = latest.free_quote - fill.notional_quote - fill.fee_quote
                value = fill.amount * price
                await self._repo.snapshot_equity(
                    self._mode,
                    equity=cash + value + latest.reserve_quote + latest.growth_quote,
                    free_quote=cash,
                    position_value_quote=value,
                    symbol=state.symbol,
                    source="ENTRY",
                )
                book=getattr(self._repo,'book_request',None)
                if book and fill.order_id:await book(self._mode,fill.order_id)
                await machine.go(IN_POSITION, f"position ouverte sur {state.symbol}")
        except Exception:
            if self._mode == "LIVE":
                try:
                    await self._repo.force_halt(
                        self._mode,
                        "achat au résultat incertain ou ledger incomplet : réconciliation obligatoire",
                    )
                    machine.current = HALT
                except Exception:
                    pass  # Database failure cannot authorize a compensating sell.
            raise
        self._submission = False
        await self._event(
            "FILLED",
            symbol=state.symbol,
            terminal=True,
            body={
                "position_id": str(position_id),
                "policy_id": policy["id"],
                "fill": public(fill),
            },
        )
        report.position_opened = f"{state.symbol} {fill.amount} @ {fill.price} · stop {decision.stop_loss_price} · TP {decision.take_profit_price}"


def position_initial_notional(position: PositionRow) -> Decimal:
    """Notionnel initial d'une position relue depuis la base."""
    return position.entry_price * position.amount
