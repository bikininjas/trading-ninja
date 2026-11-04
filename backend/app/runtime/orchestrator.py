"""Boucle d'exécution : un cycle = une décision, jamais deux.

Séquence d'un cycle, dans cet ordre et pas un autre :

    état → compte → position ouverte ? → surveillance / évaluation → pare-feu → fill papier

Invariants tenus ici :

- le pare-feu est le **seul** chemin vers une position : `evaluate()` est appelé
  avant tout `PaperBroker.entry()`, et la quantité vient de la décision ;
- une intention HOLD n'entre pas dans le pare-feu : elle n'a pas de sens pour lui
  et polluerait les statistiques de rejet ;
- le prix est rafraîchi **après** l'appel LLM : une inférence lente ne doit pas
  transformer une décision en donnée périmée ;
- une panne du modèle produit un HOLD de repli, pas un arrêt de la boucle ;
- un `EMERGENCY_HALT` ou un coupe-circuit ne se lève jamais tout seul.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from app.core.settings import Settings
from app.db.repository import AccountSnapshot, PositionRow, PostgresRepository
from app.engine.machine import (
    COOLDOWN, EVALUATING, EXECUTING, HALT, IDLE, IN_POSITION, LOCKED,
    POST_TRADE_SWEEP, StateMachine,
)
from app.engine.sweep import plan_sweep
from app.engine.risk_gate import (
    AccountState, Action, MarketRegime, RejectionCode, TradeIntent, evaluate,
)
from app.execution.paper import PaperBroker
from app.llm.client import LLMSignal, SignalClient
from app.market.feed import (
    TIMEFRAME_MS, FeedError, SymbolState, build_snapshot, closed_candles, last_price,
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
                 signal_client: SignalClient, broker: PaperBroker,
                 *, now: Any = None):
        self._settings = settings
        self._repo = repo
        self._exchange = exchange
        self._llm = signal_client
        self._broker = broker
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._mode = settings.execution_mode

    # -- helpers ---------------------------------------------------------

    def _account_state(self, account: AccountSnapshot, open_symbols: tuple[str, ...] = ()) -> AccountState:
        return AccountState(
            equity_quote=account.equity,
            free_quote=account.free_quote,
            open_position_symbols=open_symbols,
            daily_drawdown_pct=account.daily_drawdown_pct,
            weekly_drawdown_pct=account.weekly_drawdown_pct,
            drawdown_from_hwm_pct=account.from_hwm_drawdown_pct,
            consecutive_losses=account.consecutive_losses,
            orders_last_hour=account.orders_last_hour,
        )

    def _client_id(self, symbol: str, purpose: str) -> str:
        return f"{self._mode}-{symbol.replace('/', '')}-{purpose}-{uuid.uuid4().hex[:10]}"

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

    # -- cycle -----------------------------------------------------------

    async def run_once(self) -> CycleReport:
        started = time.perf_counter()
        await self._repo.ensure_mode(self._mode)
        state, cooldown_until = await self._repo.state(self._mode)
        machine = StateMachine(self._repo, self._mode, state)
        report = CycleReport(mode=self._mode, state_before=state, state_after=state)

        if state in (LOCKED, HALT):
            report.actions.append(f"état {state} : aucune action automatique")
            report.elapsed_ms = int((time.perf_counter() - started) * 1000)
            return report

        # Reprise après arrêt brutal : l'état EVALUATING/EXECUTING n'est pas un état
        # de repos. On le normalise avant d'agir, sinon la machine refuserait sa
        # propre transition et le bot resterait bloqué jusqu'à intervention.
        if state in (EVALUATING, EXECUTING):
            position = await self._repo.open_position(self._mode)
            target = IN_POSITION if position is not None else IDLE
            await machine.go(target, "reprise après interruption")
            state = machine.current

        now = self._now()
        if state == COOLDOWN:
            if cooldown_until is not None and now < cooldown_until:
                report.actions.append(f"cooldown jusqu'à {cooldown_until:%H:%M} UTC")
                report.elapsed_ms = int((time.perf_counter() - started) * 1000)
                return report
            await machine.go(IDLE, "cooldown expiré")

        account = await self._repo.account(self._mode, await self._starting_equity())
        position = await self._repo.open_position(self._mode)

        if position is not None:
            await self._monitor(machine, position, account, report)
        else:
            await self._evaluate_cycle(machine, account, report)

        report.state_after = machine.current
        report.elapsed_ms = int((time.perf_counter() - started) * 1000)
        return report

    async def _monitor(self, machine: StateMachine, position: PositionRow,
                       account: AccountSnapshot, report: CycleReport) -> None:
        if machine.current != IN_POSITION:
            # Redémarrage pendant une position : la base fait autorité sur l'état.
            await machine.go(IN_POSITION, "position ouverte retrouvée en base")

        candles = closed_candles(
            self._exchange, position.symbol, self._settings.exchange.daily_timeframe_decision, 10
        )
        if not candles:
            report.errors.append(f"{position.symbol} : aucune bougie clôturée")
            return
        last = candles[-1]
        close_at = datetime.fromtimestamp(
            (last[0] + TIMEFRAME_MS[self._settings.exchange.daily_timeframe_decision]) / 1000,
            tz=timezone.utc,
        )
        if close_at <= position.opened_at:
            report.actions.append("aucune bougie clôturée depuis l'entrée")
            return

        trigger = self._broker.exit_trigger(position.stop_loss_price,
                                            position.take_profit_price, last)
        if trigger is None:
            price = last_price(self._exchange, position.symbol)
            value = position.amount * price
            await self._repo.snapshot_equity(
                self._mode, equity=account.free_quote + value + account.reserve_quote,
                free_quote=account.free_quote,
                position_value_quote=value, symbol=position.symbol, source="MONITOR",
            )
            report.actions.append(
                f"{position.symbol} : stop {position.stop_loss_price} · "
                f"prix {price} · aucune sortie"
            )
            return

        exit_price, reason = trigger
        fill = self._broker.exit(position.amount, exit_price)
        entry_fee = self._broker.fee(position_initial_notional(position))
        gross = (fill.price - position.entry_price) * position.amount
        fees = entry_fee + fill.fee_quote
        net = gross - fees

        await machine.go(POST_TRADE_SWEEP, f"sortie {reason}", code="NONE",
                         context={"exit_price": str(fill.price), "reason": reason})
        await self._repo.insert_order(
            self._mode, client_order_id=self._client_id(position.symbol, "EXIT"),
            symbol=position.symbol, side="SELL", order_type="MARKET",
            purpose="STOP_LOSS" if reason == "STOP_LOSS" else "TAKE_PROFIT",
            status="FILLED", requested_amount=position.amount,
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
        cash = account.free_quote + fill.notional_quote - fill.fee_quote
        reserve = account.reserve_quote
        sweep_note = ""

        deferred = await self._repo.deferred_sweep_quote(self._mode)
        plan = plan_sweep(net, self._settings.sweep_ratio,
                          self._settings.sweep_min_transfer_quote,
                          deferred_quote=deferred)
        if plan.applies:
            # SUCCESS/SIMULATED : l'argent a quitté le capital de trading.
            # PENDING : le virement reste à faire, le cash ne bouge pas encore.
            if plan.status in ("SIMULATED", "SUCCESS"):
                cash -= plan.swept_quote
                reserve += plan.swept_quote
            await self._repo.insert_sweep(
                self._mode, position_id=position.id,
                quote_currency=self._settings.exchange.quote_currency,
                realized_net_pnl=net, sweep_ratio=self._settings.sweep_ratio,
                computed_sweep_quote=plan.computed_quote,
                swept_amount=plan.swept_quote, retained_trading_capital=cash,
                status=plan.status,
                next_retry_at=(self._now() + timedelta(days=1))
                if plan.status == "SKIPPED_BELOW_MINIMUM" else None,
                error_message=plan.reason,
            )
            sweep_note = (f" · écrémé {plan.swept_quote}"
                          if plan.swept_quote > 0
                          else f" · écrémage reporté ({plan.reason})")

        equity = cash + reserve
        await self._repo.update_after_close(
            self._mode, consecutive_losses=consecutive, equity=equity
        )
        await self._repo.snapshot_equity(
            self._mode, equity=equity, free_quote=cash, position_value_quote=Decimal(0),
            symbol=position.symbol, source="EXIT",
        )
        await self._repo.set_cooldown(self._mode, self._now() + timedelta(minutes=COOLDOWN_MINUTES))
        await machine.go(COOLDOWN, "post-trade, refroidissement")
        report.position_closed = (
            f"{position.symbol} {reason} @ {fill.price} · net {net:.4f} "
            f"{self._settings.exchange.quote_currency}{sweep_note}"
        )

    async def _evaluate_cycle(self, machine: StateMachine, account: AccountSnapshot,
                              report: CycleReport) -> None:
        await machine.go(EVALUATING, "cycle d'évaluation")
        chosen: tuple[SymbolState, LLMSignal, Any, uuid.UUID, uuid.UUID] | None = None
        account_state = self._account_state(account)

        for symbol in self._settings.exchange.allowed_symbols:
            try:
                state = build_snapshot(
                    self._exchange, symbol,
                    decision_timeframe=self._settings.exchange.daily_timeframe_decision,
                    entry_timeframe=self._settings.exchange.entry_timeframe,
                )
            except FeedError as exc:
                report.errors.append(f"{symbol} : {exc}")
                continue

            # Une bougie clôturée = une évaluation. Sans ce garde, une boucle qui
            # tourne toutes les minutes redemande au modèle la même bougie, pollue
            # les statistiques et peut rouvrir une position sitôt la précédente
            # fermée. La comparaison traverse les redémarrages : le dernier signal
            # est relu en base.
            last_signal = await self._repo.last_signal_at(self._mode, symbol)
            if last_signal is not None and last_signal >= state.last_close_at:
                report.skipped += 1
                report.actions.append(
                    f"{symbol} : bougie clôturée à "
                    f"{state.last_close_at:%Y-%m-%d %H:%M} UTC déjà évaluée"
                )
                continue

            signal = await self._llm.propose(
                symbol, state.snapshot, state.context, has_position=False,
                trend_buffer_pct=self._settings.risk.trend_buffer_pct,
            )
            report.signals += 1
            if signal.degraded:
                report.degraded_calls += 1
            signal_id, signal_at = await self._repo.insert_signal(
                self._mode, symbol,
                f"{self._settings.llm.model}{' (repli)' if signal.degraded else ''}",
                action=signal.action, confidence=signal.confidence,
                market_regime=signal.market_regime, thesis=signal.thesis,
                prompt_payload=dict(signal.prompt_payload),
                raw_response=dict(signal.raw),
                inference_time_ms=signal.inference_ms,
                prompt_sha256=signal.prompt_sha256 or None,
                temperature=self._settings.llm.temperature,
            )
            if signal.degraded:
                report.actions.append(f"{symbol} : HOLD de repli ({signal.degradation_reason})")
                continue
            if signal.action == "HOLD":
                report.actions.append(f"{symbol} : HOLD ({signal.confidence:.2f})")
                continue

            # Prix rafraîchi après l'inférence : la décision porte sur le prix
            # d'exécution, pas sur celui d'il y a quatre secondes.
            price = last_price(self._exchange, symbol)
            evaluated_at = self._now()
            fresh = replace(state.snapshot, price=price, tick_at=evaluated_at, now=evaluated_at)
            intent = TradeIntent(
                Action(signal.action), float(signal.confidence),
                MarketRegime(signal.market_regime),
            )
            eval_started = time.perf_counter()
            decision = evaluate(
                intent, fresh, account_state, state.info.constraints, self._settings.risk
            )
            eval_ms = int((time.perf_counter() - eval_started) * 1000)
            evaluation_id, _ = await self._repo.insert_evaluation(
                self._mode, symbol, signal_id=signal_id, signal_created_at=signal_at,
                decision=decision, snapshot=fresh, account=account,
                evaluation_time_ms=eval_ms,
                decision_timeframe=self._settings.exchange.daily_timeframe_decision,
            )
            report.evaluations += 1
            if decision.approved:
                report.approved += 1
                report.actions.append(
                    f"{symbol} : APPROUVÉ {decision.amount} @ {price} · "
                    f"risque {decision.effective_risk_quote:.4f}"
                )
                if chosen is None:
                    chosen = (state, signal, decision, signal_id, evaluation_id)
            else:
                report.actions.append(f"{symbol} : refusé {decision.rejection_code}")

        if chosen is None:
            await machine.go(IDLE, "aucune décision approuvée")
            return

        state, signal, decision, signal_id, evaluation_id = chosen
        await machine.go(EXECUTING, f"entrée approuvée sur {state.symbol}")
        await self._enter(machine, state, decision, signal_id, evaluation_id, account, report)

    async def _enter(self, machine: StateMachine, state: SymbolState, decision: Any,
                     signal_id: uuid.UUID, evaluation_id: uuid.UUID,
                     account: AccountSnapshot, report: CycleReport) -> None:
        price = last_price(self._exchange, state.symbol)
        fill = self._broker.entry(decision, price)
        entry_client_id = self._client_id(state.symbol, "ENTRY")
        stop_client_id = self._client_id(state.symbol, "STOP")

        if decision.take_profit_price is not None:
            r_multiple = (decision.take_profit_price - fill.price) / (
                fill.price - decision.stop_loss_price
            )
        else:
            r_multiple = None

        position_id = await self._repo.create_position(
            self._mode, symbol=state.symbol, entry_price=fill.price, amount=fill.amount,
            notional_quote=fill.notional_quote, stop_loss_price=decision.stop_loss_price,
            take_profit_price=decision.take_profit_price,
            risk_amount_quote=decision.effective_risk_quote or decision.intended_risk_quote,
            r_multiple_at_tp=r_multiple, signal_id=signal_id, evaluation_id=evaluation_id,
            protective_order_id=stop_client_id,
        )
        await self._repo.insert_order(
            self._mode, client_order_id=entry_client_id, symbol=state.symbol, side="BUY",
            order_type="MARKET", purpose="ENTRY", status="FILLED",
            requested_amount=fill.amount, filled_amount=fill.amount, requested_price=None,
            stop_price=None, average_fill_price=fill.price, fee_amount=fill.fee_quote,
            fee_currency=self._settings.exchange.quote_currency,
            slippage_quote=fill.slippage_quote, position_id=position_id,
            risk_evaluation_id=evaluation_id,
        )
        await self._repo.insert_order(
            self._mode, client_order_id=stop_client_id, symbol=state.symbol, side="SELL",
            order_type="STOP_LOSS", purpose="STOP_LOSS", status="SUBMITTED",
            requested_amount=fill.amount, filled_amount=Decimal(0), requested_price=None,
            stop_price=decision.stop_loss_price, average_fill_price=Decimal(0),
            fee_amount=Decimal(0), fee_currency=self._settings.exchange.quote_currency,
            slippage_quote=None, position_id=position_id,
            risk_evaluation_id=evaluation_id,
        )
        cash = account.free_quote - fill.notional_quote - fill.fee_quote
        value = fill.amount * price
        await self._repo.snapshot_equity(
            self._mode, equity=cash + value + account.reserve_quote,
            free_quote=cash, position_value_quote=value,
            symbol=state.symbol, source="ENTRY",
        )
        await machine.go(IN_POSITION, f"position ouverte sur {state.symbol}")
        report.position_opened = (
            f"{state.symbol} {fill.amount} @ {fill.price} · stop "
            f"{decision.stop_loss_price} · TP {decision.take_profit_price}"
        )


def position_initial_notional(position: PositionRow) -> Decimal:
    """Notionnel initial d'une position relue depuis la base."""
    return position.entry_price * position.amount
