"""Rejeu historique — le vrai pare-feu, le vrai simulateur, aucune écriture en base.

Principes :

- **Aucune bougie future n'est visible.** À l'instant T, l'exchange de rejeu ne sert
  que les bougies dont la fenêtre est terminée à T ; `build_snapshot` calcule les
  indicateurs sur cette tranche, comme en production.
- **Le LLM est interrogé là où un achat est possible** (au-dessus de l'EMA200 + marge,
  RSI < 70, volatilité dans les bornes). Ailleurs, un BUY serait de toute façon refusé
  par les garde-fous : l'appeler n'apporterait aucune information.
- **Le dimensionnement vient de `evaluate()`**, jamais du backtest. Une décision
  refusée ne produit aucun trade, comme en réel.
- **Les fills viennent de `PaperBroker`** : slippage, frais taker, stop prioritaire sur
  le take-profit si la bougie touche les deux.

Le résultat sert à mesurer le comportement, pas à prouver une espérance de gain : un
échantillon de quelques semaines ne suffit pas, et le carnet d'ordres n'existe pas dans
l'historique (le modèle le voit à zéro).
"""

from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Awaitable, Callable

from app.engine.risk_gate import (
    AccountState,
    Action,
    ExchangeConstraints,
    MarketRegime,
    MarketSnapshot,
    RiskConfig,
    RiskDecision,
    TradeIntent,
    evaluate,
)
from app.engine.entry_rule import evaluate_entry
from app.execution.paper import Fill, PaperBroker
from app.engine.exit_rule import advance_profit_stop
from app.market.feed import (
    EMA_PERIOD,
    TIMEFRAME_MS,
    FeedError,
    MarketContext,
    build_snapshot,
)

__all__ = [
    "BacktestResult",
    "ReplayExchange",
    "TradeRecord",
    "run_backtest",
    "zone_is_open",
]

DECISION_TIMEFRAME = "4h"
DECISION_MS = TIMEFRAME_MS[DECISION_TIMEFRAME]
H1_WARMUP = 120
RSI_MAX_BUY = Decimal("70")


@dataclass(frozen=True, slots=True)
class TradeRecord:
    entry_at: datetime
    exit_at: datetime
    entry_price: Decimal
    exit_price: Decimal
    amount: Decimal
    notional_quote: Decimal
    fees_quote: Decimal
    pnl_quote: Decimal
    reason: str
    stop_loss_price: Decimal
    take_profit_price: Decimal | None
    confidence: Decimal
    thesis: str = ""


@dataclass(frozen=True, slots=True)
class Counterfactual:
    """Ce qu'aurait donné un achat forcé là où le modèle a dit HOLD."""

    at: datetime
    approved: bool
    rejection: str
    pnl_quote: Decimal
    reason: str
    llm_action: str


@dataclass
class BacktestResult:
    symbol: str
    model: str
    period_start: datetime
    period_end: datetime
    candles: int
    decisions: int
    buy: int
    hold: int
    downgraded: int
    degraded: int
    inference_ms: list[int]
    approvals: int
    rejections: Counter
    trades: list[TradeRecord]
    counterfactuals: list[Counterfactual]
    equity_start: Decimal
    equity_end: Decimal
    max_drawdown_pct: Decimal
    buy_and_hold_pct: Decimal
    baseline_trades: list[TradeRecord]
    baseline_equity_end: Decimal
    baseline_max_drawdown_pct: Decimal
    sell: int = 0
    halt: int = 0
    llm_exits: int = 0

    # -- dérivés ---------------------------------------------------------

    @property
    def wins(self) -> list[TradeRecord]:
        return [t for t in self.trades if t.pnl_quote > 0]

    @property
    def losses(self) -> list[TradeRecord]:
        return [t for t in self.trades if t.pnl_quote <= 0]

    @property
    def net_pnl(self) -> Decimal:
        return sum((t.pnl_quote for t in self.trades), Decimal(0))

    @property
    def win_rate(self) -> Decimal:
        if not self.trades:
            return Decimal(0)
        return Decimal(len(self.wins)) / Decimal(len(self.trades))

    # -- statistiques de validation (jalon 2) ----------------------------

    @property
    def gross_profit(self) -> Decimal:
        """Somme des gains bruts, frais déjà déduits par le broker."""
        return sum((t.pnl_quote for t in self.trades if t.pnl_quote > 0), Decimal(0))

    @property
    def gross_loss(self) -> Decimal:
        """Somme des pertes brutes, en valeur positive."""
        return -sum((t.pnl_quote for t in self.trades if t.pnl_quote < 0), Decimal(0))

    @property
    def profit_factor(self) -> Decimal | None:
        """Gains bruts / pertes brutes. `None` quand il n'y a pas de perte à opposer.

        Un PF sans perte n'est pas « infini » au sens statistique : c'est un
        échantillon qui ne contient pas encore de perte, donc une valeur non
        interprétable. Un PF sur zéro trade vaut `None` aussi.
        """
        if not self.trades or self.gross_loss == 0:
            return None
        return self.gross_profit / self.gross_loss

    @property
    def profit_factor_label(self) -> str:
        if not self.trades:
            return "n/a"
        if self.gross_loss == 0:
            return "∞" if self.gross_profit > 0 else "n/a"
        return f"{self.profit_factor:.2f}"

    @property
    def expectancy_quote(self) -> Decimal:
        """Espérance par trade, en devise de cotation, nette de frais."""
        if not self.trades:
            return Decimal(0)
        return self.net_pnl / Decimal(len(self.trades))

    @property
    def expectancy_pct(self) -> Decimal:
        """Espérance par trade rapportée à l'équité de départ."""
        if not self.trades or self.equity_start == 0:
            return Decimal(0)
        return self.expectancy_quote / self.equity_start

    @property
    def average_win(self) -> Decimal:
        return self.gross_profit / Decimal(len(self.wins)) if self.wins else Decimal(0)

    @property
    def average_loss(self) -> Decimal:
        return self.gross_loss / Decimal(len(self.losses)) if self.losses else Decimal(0)

    @property
    def return_pct(self) -> Decimal:
        if self.equity_start == 0:
            return Decimal(0)
        return (self.equity_end - self.equity_start) / self.equity_start

    @property
    def years(self) -> Decimal:
        """Durée de la fenêtre en années (365,25 jours), jamais nulle."""
        days = (self.period_end - self.period_start).total_seconds() / 86_400
        return Decimal(str(max(days, 1))) / Decimal("365.25")

    @property
    def trades_per_year(self) -> Decimal:
        return Decimal(len(self.trades)) / self.years if self.years > 0 else Decimal(0)

    @property
    def payoff_ratio(self) -> Decimal | None:
        """Gain moyen / perte moyenne. None si l'un des deux camps est vide."""
        if not self.wins or not self.losses:
            return None
        return self.average_win / self.average_loss

    @property
    def median_inference_ms(self) -> int:
        return int(statistics.median(self.inference_ms)) if self.inference_ms else 0

    @property
    def mean_inference_ms(self) -> int:
        return int(statistics.fmean(self.inference_ms)) if self.inference_ms else 0

    def summary(self) -> str:
        lines = [
            f"{self.symbol} · {self.period_start:%Y-%m-%d} → {self.period_end:%Y-%m-%d} "
            f"· {self.model}",
            f"bougies évaluées      {self.candles} · décisions LLM {self.decisions} "
            f"(BUY {self.buy}, HOLD {self.hold}, rétrogradés {self.downgraded}, "
            f"repli {self.degraded})",
            f"appels approuvés      {self.approvals}",
        ]
        if self.rejections:
            codes = ", ".join(f"{code} {n}" for code, n in self.rejections.most_common())
            lines.append(f"rejets                {codes}")
        lines += [
            f"trades                {len(self.trades)} · gagnants {len(self.wins)} · "
            f"perdants {len(self.losses)} · réussite {self.win_rate:.1%}",
            f"profit factor         {self.profit_factor_label} · "
            f"espérance {self.expectancy_quote:+.4f} EUR/trade "
            f"({self.expectancy_pct:+.2%}) · gain moy {self.average_win:.4f} / "
            f"perte moy {self.average_loss:.4f}",
            f"sorties LLM           {self.llm_exits} (SELL {self.sell} · HALT {self.halt})",
            f"PnL net               {self.net_pnl:+.4f} EUR ({self.return_pct:+.2%}) · "
            f"équité {self.equity_start:.2f} → {self.equity_end:.2f}",
            f"drawdown max          {self.max_drawdown_pct:.2%} · "
            f"buy & hold {self.buy_and_hold_pct:+.2%}",
            f"baseline mécanique    {len(self.baseline_trades)} trades · "
            f"équité {self.baseline_equity_end:.2f} · "
            f"dd {self.baseline_max_drawdown_pct:.2%}",
        ]
        if self.counterfactuals:
            approved = [c for c in self.counterfactuals if c.approved]
            pnl = sum((c.pnl_quote for c in approved), Decimal(0))
            wins = len([c for c in approved if c.pnl_quote > 0])
            lines.append(
                f"HOLD contrefactuel    {len(approved)} achats possibles · "
                f"{wins} gagnants · {pnl:+.4f} EUR cumulés"
            )
        lines.append(
            f"latence LLM           médiane {self.median_inference_ms} ms · "
            f"moyenne {self.mean_inference_ms} ms"
        )
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        def trade(t: TradeRecord) -> dict[str, Any]:
            return {
                "entry_at": t.entry_at.isoformat(),
                "exit_at": t.exit_at.isoformat(),
                "entry_price": str(t.entry_price),
                "exit_price": str(t.exit_price),
                "amount": str(t.amount),
                "notional_quote": str(t.notional_quote),
                "fees_quote": str(t.fees_quote),
                "pnl_quote": str(t.pnl_quote),
                "reason": t.reason,
                "stop_loss_price": str(t.stop_loss_price),
                "take_profit_price": str(t.take_profit_price) if t.take_profit_price else None,
                "confidence": str(t.confidence),
                "thesis": t.thesis,
            }

        return {
            "symbol": self.symbol,
            "model": self.model,
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "candles": self.candles,
            "decisions": self.decisions,
            "buy": self.buy,
            "hold": self.hold,
            "downgraded": self.downgraded,
            "degraded": self.degraded,
            "approvals": self.approvals,
            "sell": self.sell,
            "halt": self.halt,
            "llm_exits": self.llm_exits,
            "rejections": dict(self.rejections),
            "trades": [trade(t) for t in self.trades],
            "counterfactuals": [
                {
                    "at": c.at.isoformat(),
                    "approved": c.approved,
                    "rejection": c.rejection,
                    "pnl_quote": str(c.pnl_quote),
                    "reason": c.reason,
                }
                for c in self.counterfactuals
            ],
            "equity_start": str(self.equity_start),
            "equity_end": str(self.equity_end),
            "net_pnl": str(self.net_pnl),
            "return_pct": str(self.return_pct),
            "win_rate": str(self.win_rate),
            "profit_factor": (
                str(self.profit_factor) if self.profit_factor is not None else None
            ),
            "expectancy_quote": str(self.expectancy_quote),
            "expectancy_pct": str(self.expectancy_pct),
            "gross_profit": str(self.gross_profit),
            "gross_loss": str(self.gross_loss),
            "max_drawdown_pct": str(self.max_drawdown_pct),
            "buy_and_hold_pct": str(self.buy_and_hold_pct),
            "baseline": {
                "trades": [trade(t) for t in self.baseline_trades],
                "equity_end": str(self.baseline_equity_end),
                "max_drawdown_pct": str(self.baseline_max_drawdown_pct),
            },
            "inference_ms": self.inference_ms,
        }


class ReplayExchange:
    """Exchange factice : ne sert que des bougies clôturées à l'instant courant."""

    def __init__(self, h4: list[list], h1: list[list]) -> None:
        self._h4 = sorted(h4, key=lambda c: c[0])
        self._h1 = sorted(h1, key=lambda c: c[0])
        self._now_ms = 0

    def set_now(self, milliseconds: int) -> None:
        self._now_ms = milliseconds

    def milliseconds(self) -> int:
        return self._now_ms

    def fetch_ohlcv(self, symbol: str, timeframe: str, since: int | None = None,
                    limit: int | None = None) -> list[list]:
        if timeframe == DECISION_TIMEFRAME:
            series, duration = self._h4, DECISION_MS
        elif timeframe == "1h":
            series, duration = self._h1, TIMEFRAME_MS["1h"]
        else:
            raise FeedError(f"timeframe non géré en rejeu : {timeframe}")
        closed = [c for c in series if c[0] + duration <= self._now_ms]
        if since is not None:
            closed = [c for c in closed if c[0] >= since]
        if limit is not None:
            closed = closed[-limit:]
        return closed


def _close_time(candle: list) -> datetime:
    return datetime.fromtimestamp((candle[0] + DECISION_MS) / 1000, tz=timezone.utc)


def _decimal(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _drawdowns(curve: list[tuple[datetime, Decimal]], at: datetime) -> tuple[Decimal, Decimal, Decimal]:
    equity = curve[-1][1]
    day_peak = max((e for t, e in curve if t >= at - timedelta(hours=24)), default=equity)
    week_peak = max((e for t, e in curve if t >= at - timedelta(days=7)), default=equity)
    hwm = max((e for _, e in curve), default=equity)
    return (
        max(Decimal(0), (day_peak - equity) / day_peak) if day_peak else Decimal(0),
        max(Decimal(0), (week_peak - equity) / week_peak) if week_peak else Decimal(0),
        max(Decimal(0), (hwm - equity) / hwm) if hwm else Decimal(0),
    )


def zone_is_open(snapshot: MarketSnapshot, context: MarketContext, config: RiskConfig) -> bool:
    """Les conditions où un BUY a seulement le droit d'exister (miroir du client LLM)."""
    if snapshot.price <= snapshot.ema_200_h4 * (1 + config.trend_buffer_pct):
        return False
    if context.rsi_14_h4 >= RSI_MAX_BUY:
        return False
    atr_pct = snapshot.atr_14_h4 / snapshot.price
    return config.min_atr_pct_of_price <= atr_pct <= config.max_atr_pct_of_price


def _simulate_exit(
    h4: list[list],
    entry_index: int,
    decision: RiskDecision,
    broker: PaperBroker,
    max_hold_candles: int = 0,
    stop_index: int | None = None,
    config: RiskConfig | None = None,
    price_tick: Decimal = Decimal("0.01"),
) -> tuple[int, Fill, str]:
    """Cherche la première sortie après l'entrée. Stop prioritaire sur le TP.

    `stop_index` borne la recherche à la fenêtre de backtest : une position encore
    ouverte à la fin est soldée au dernier cours connu, pas au-delà de la fenêtre.
    """
    amount = decision.amount
    stop = decision.stop_loss_price
    take = decision.take_profit_price
    if amount is None or stop is None:
        raise ValueError("sortie simulée sans quantité ou sans stop")

    ceiling = len(h4) - 1 if stop_index is None else min(stop_index, len(h4) - 1)
    last_index = ceiling if max_hold_candles <= 0 else min(
        ceiling, entry_index + max_hold_candles
    )
    for j in range(entry_index + 1, last_index + 1):
        trigger = PaperBroker.exit_trigger(stop, take, h4[j])
        if trigger is not None:
            price, reason = trigger
            return j, broker.exit(amount, price), reason
        if config is not None:
            stop = advance_profit_stop(
                entry_price=broker.entry(decision, _decimal(h4[entry_index][4])).price,
                initial_distance=decision.stop_distance_quote or Decimal(0),
                current_stop=stop, close=_decimal(h4[j][4]),
                price_tick=price_tick, config=config,
            )

    price = _decimal(h4[last_index][4])
    return last_index, broker.exit(amount, price), "END_OF_WINDOW"


async def _simulate_trade(
    h4: list[list],
    h1: list[list],
    symbol: str,
    info: Any,
    entry_index: int,
    decision: RiskDecision,
    broker: PaperBroker,
    propose: Callable[[MarketSnapshot, MarketContext, bool], Awaitable[Any]],
    config: RiskConfig,
    max_hold_candles: int = 0,
    stop_index: int | None = None,
) -> tuple[int, Fill, str, list[Any], bool]:
    """Simule une position bougie par bougie, avec sorties stop/TP et LLM.

    Le stop et le take-profit sont testés **avant** l'appel au modèle : ils vivent
    dans la bougie, le modèle ne voit que sa clôture. Un SELL du modèle est rempli
    à la clôture (plus slippage et frais) ; un HALT clôture et arrête le rejeu.
    """
    amount = decision.amount
    stop = decision.stop_loss_price
    take = decision.take_profit_price
    if amount is None or stop is None:
        raise ValueError("sortie simulée sans quantité ou sans stop")

    replay = ReplayExchange(h4, h1)
    ceiling = len(h4) - 1 if stop_index is None else min(stop_index, len(h4) - 1)
    last_index = ceiling if max_hold_candles <= 0 else min(
        ceiling, entry_index + max_hold_candles
    )
    exit_signals: list[Any] = []

    for j in range(entry_index + 1, last_index + 1):
        replay.set_now(h4[j][0] + DECISION_MS)
        trigger = PaperBroker.exit_trigger(stop, take, h4[j])
        if trigger is not None:
            price, reason = trigger
            return j, broker.exit(amount, price), reason, exit_signals, False

        stop = advance_profit_stop(
            entry_price=broker.entry(decision, _decimal(h4[entry_index][4])).price,
            initial_distance=decision.stop_distance_quote or Decimal(0),
            current_stop=stop, close=_decimal(h4[j][4]),
            price_tick=info.constraints.price_tick, config=config,
        )

        at = _close_time(h4[j])
        try:
            state = build_snapshot(
                replay, symbol, info=info, now=at, with_order_book=False
            )
        except FeedError:
            continue

        signal = await propose(state.snapshot, state.context, True)
        exit_signals.append(signal)
        action = str(getattr(signal, "action", "HOLD"))
        confidence = _decimal(getattr(signal, "confidence", 0))
        if action == "HALT" and confidence >= config.min_halt_confidence:
            return j, broker.exit(amount, state.snapshot.price), "LLM_HALT", exit_signals, True
        if action == "SELL" and confidence >= config.min_exit_confidence:
            return j, broker.exit(amount, state.snapshot.price), "LLM_SELL", exit_signals, False

    price = _decimal(h4[last_index][4])
    return last_index, broker.exit(amount, price), "END_OF_WINDOW", exit_signals, False


def _pnl(entry: Fill, exit_fill: Fill) -> Decimal:
    return (
        exit_fill.notional_quote
        - entry.notional_quote
        - entry.fee_quote
        - exit_fill.fee_quote
    )


def _max_drawdown(curve: list[tuple[datetime, Decimal]]) -> Decimal:
    peak = curve[0][1]
    worst = Decimal(0)
    for _, equity in curve:
        peak = max(peak, equity)
        if peak > 0:
            worst = max(worst, (peak - equity) / peak)
    return worst


def _record_trade_curve(curve, h4, entry_index, exit_index, equity_before,
                        entry, exit_fill, broker):
    """Valorisation à chaque clôture, frais de liquidation inclus, sans futur."""
    for j in range(entry_index, exit_index):
        value = broker.exit(entry.amount, _decimal(h4[j][4]))
        curve.append((_close_time(h4[j]), equity_before + _pnl(entry, value)))
    curve.append((_close_time(h4[exit_index]), equity_before + _pnl(entry, exit_fill)))


async def run_backtest(
    *,
    symbol: str,
    info: Any,
    h4: list[list],
    h1: list[list],
    settings: Any,
    propose: Callable[[MarketSnapshot, MarketContext, bool], Awaitable[Any]] | None,
    broker: PaperBroker | None = None,
    start_index: int | None = None,
    end_index: int | None = None,
    max_hold_candles: int = 0,
    mechanical: bool = False,
    counterfactuals: bool = True,
    progress: Callable[[int, int], None] | None = None,
    use_entry_rule: bool = True,
) -> BacktestResult:
    """Rejoue une fenêtre et rend les métriques. Ne touche ni au réseau ni à la base."""
    if len(h4) < EMA_PERIOD + 2:
        raise FeedError(f"historique H4 insuffisant : {len(h4)} bougies")

    config: RiskConfig = settings.risk
    paper_broker = broker or PaperBroker(info.taker_fee, settings.paper_slippage_bps)
    replay = ReplayExchange(h4, h1)

    first = start_index if start_index is not None else EMA_PERIOD + 1
    last = end_index if end_index is not None else len(h4) - 1
    first = max(first, EMA_PERIOD + 1)
    if last < first:
        raise ValueError("fenêtre de backtest vide")

    curve: list[tuple[datetime, Decimal]] = [(_close_time(h4[first - 1]), _decimal(settings.paper_start_equity))]
    equity = _decimal(settings.paper_start_equity)
    consecutive_losses = 0
    trades: list[TradeRecord] = []
    decisions = 0
    buy = hold = downgraded = degraded = approvals = 0
    sell = halt = llm_exits = 0
    halted = False
    rejections: Counter = Counter()
    counterfactual_rows: list[Counterfactual] = []
    inference: list[int] = []
    baseline_trades: list[TradeRecord] = []
    baseline_equity = equity
    baseline_curve: list[tuple[datetime, Decimal]] = [(_close_time(h4[first - 1]), equity)]
    baseline_consecutive = 0

    i = first
    while i <= last:
        replay.set_now(h4[i][0] + DECISION_MS)
        at = _close_time(h4[i])
        try:
            state = build_snapshot(
                replay,
                symbol,
                info=info,
                now=at,
                with_order_book=False,
            )
        except FeedError:
            i += 1
            continue

        use_rule = use_entry_rule and config.entry_rule_enabled
        open_zone = (
            evaluate_entry(state.snapshot, state.context, config).triggered
            if use_rule
            else zone_is_open(state.snapshot, state.context, config)
        )
        if not open_zone:
            i += 1
            continue

        if progress is not None:
            progress(i, last + 1)

        # -- baseline mécanique : acheter dès que la zone est ouverte -------
        if mechanical and propose is not None:
            raise ValueError("mechanical=True n'appelle pas le modèle")
        if mechanical:
            intent = TradeIntent(
                action=Action.BUY,
                confidence=1.0,
                market_regime=MarketRegime.TRENDING_UP,
            )
            account = AccountState(
                equity_quote=baseline_equity,
                free_quote=baseline_equity,
                consecutive_losses=baseline_consecutive,
                daily_drawdown_pct=_drawdowns(baseline_curve, at)[0],
                weekly_drawdown_pct=_drawdowns(baseline_curve, at)[1],
                drawdown_from_hwm_pct=_drawdowns(baseline_curve, at)[2],
            )
            decision = evaluate(intent, state.snapshot, account, info.constraints, config)
            if decision.approved:
                entry = paper_broker.entry(decision, state.snapshot.price)
                exit_index, exit_fill, reason = _simulate_exit(
                    h4, i, decision, paper_broker, max_hold_candles, stop_index=last,
                    config=config, price_tick=info.constraints.price_tick,
                )
                pnl = _pnl(entry, exit_fill)
                _record_trade_curve(baseline_curve, h4, i, exit_index,
                                    baseline_equity, entry, exit_fill, paper_broker)
                baseline_equity += pnl
                baseline_consecutive = 0 if pnl > 0 else baseline_consecutive + 1
                baseline_trades.append(
                    TradeRecord(
                        entry_at=at,
                        exit_at=_close_time(h4[exit_index]),
                        entry_price=entry.price,
                        exit_price=exit_fill.price,
                        amount=decision.amount or Decimal(0),
                        notional_quote=entry.notional_quote,
                        fees_quote=entry.fee_quote + exit_fill.fee_quote,
                        pnl_quote=pnl,
                        reason=reason,
                        stop_loss_price=decision.stop_loss_price or Decimal(0),
                        take_profit_price=decision.take_profit_price,
                        confidence=Decimal(1),
                        thesis="baseline mécanique",
                    )
                )
                i = exit_index + 1
            else:
                rejections[str(decision.rejection_code)] += 1
                i += 1
            continue

        # -- décision du modèle --------------------------------------------
        if propose is None:
            raise ValueError("propose est requis hors mode mécanique")
        signal = await propose(state.snapshot, state.context, False)
        decisions += 1
        inference.append(int(getattr(signal, "inference_ms", 0)))
        action = str(getattr(signal, "action", "HOLD"))
        confidence = _decimal(getattr(signal, "confidence", 0))
        if getattr(signal, "degraded", False):
            degraded += 1
        if getattr(signal, "degradation_reason", "") and "règle déterministe" in str(
            signal.degradation_reason
        ):
            downgraded += 1
        if action == "BUY":
            buy += 1
        elif action == "SELL":
            sell += 1
        elif action == "HALT":
            halt += 1
        else:
            hold += 1

        # Sans position, un SELL ne peut pas créer un short, et un HALT n'a rien à
        # liquider : il arrête simplement le rejeu.
        if action == "HALT" and confidence >= config.min_halt_confidence:
            halted = True
            break
        if action == "SELL":
            i += 1
            continue

        intent = TradeIntent(
            action=Action(action),
            confidence=float(confidence),
            market_regime=MarketRegime(str(getattr(signal, "market_regime", "RANGING"))),
            thesis=str(getattr(signal, "thesis", "")),
        )
        account = AccountState(
            equity_quote=equity,
            free_quote=equity,
            consecutive_losses=consecutive_losses,
            daily_drawdown_pct=_drawdowns(curve, at)[0],
            weekly_drawdown_pct=_drawdowns(curve, at)[1],
            drawdown_from_hwm_pct=_drawdowns(curve, at)[2],
        )
        decision = evaluate(intent, state.snapshot, account, info.constraints, config)

        if decision.approved:
            approvals += 1
            entry = paper_broker.entry(decision, state.snapshot.price)
            exit_index, exit_fill, reason, exit_signals, halted_trade = await _simulate_trade(
                h4, h1, symbol, info, i, decision, paper_broker, propose, config,
                max_hold_candles=max_hold_candles, stop_index=last,
            )
            for exit_signal in exit_signals:
                exit_action = str(getattr(exit_signal, "action", "HOLD"))
                if exit_action == "BUY":
                    buy += 1
                elif exit_action == "SELL":
                    sell += 1
                elif exit_action == "HALT":
                    halt += 1
                else:
                    hold += 1
                inference.append(int(getattr(exit_signal, "inference_ms", 0)))
                if getattr(exit_signal, "degraded", False):
                    degraded += 1
            decisions += len(exit_signals)
            if reason in ("LLM_SELL", "LLM_HALT"):
                llm_exits += 1
            pnl = _pnl(entry, exit_fill)
            _record_trade_curve(curve, h4, i, exit_index, equity, entry, exit_fill, paper_broker)
            equity += pnl
            consecutive_losses = 0 if pnl > 0 else consecutive_losses + 1
            trades.append(
                TradeRecord(
                    entry_at=at,
                    exit_at=_close_time(h4[exit_index]),
                    entry_price=entry.price,
                    exit_price=exit_fill.price,
                    amount=decision.amount or Decimal(0),
                    notional_quote=entry.notional_quote,
                    fees_quote=entry.fee_quote + exit_fill.fee_quote,
                    pnl_quote=pnl,
                    reason=reason,
                    stop_loss_price=decision.stop_loss_price or Decimal(0),
                    take_profit_price=decision.take_profit_price,
                    confidence=_decimal(getattr(signal, "confidence", 0)),
                    thesis=str(getattr(signal, "thesis", "")),
                )
            )
            i = exit_index + 1
            if halted_trade:
                halted = True
                break
            continue

        rejections[str(decision.rejection_code)] += 1
        if counterfactuals:
            forced = evaluate(
                TradeIntent(Action.BUY, 1.0, MarketRegime.TRENDING_UP),
                state.snapshot,
                AccountState(
                    equity_quote=equity,
                    free_quote=equity,
                    consecutive_losses=consecutive_losses,
                    daily_drawdown_pct=_drawdowns(curve, at)[0],
                    weekly_drawdown_pct=_drawdowns(curve, at)[1],
                    drawdown_from_hwm_pct=_drawdowns(curve, at)[2],
                ),
                info.constraints,
                config,
            )
            if forced.approved:
                entry = paper_broker.entry(forced, state.snapshot.price)
                exit_index, exit_fill, reason = _simulate_exit(
                    h4, i, forced, paper_broker, max_hold_candles, stop_index=last,
                    config=config, price_tick=info.constraints.price_tick,
                )
                counterfactual_rows.append(
                    Counterfactual(
                        at=at,
                        approved=True,
                        rejection="NONE",
                        pnl_quote=_pnl(entry, exit_fill),
                        reason=reason,
                        llm_action=action,
                    )
                )
            else:
                counterfactual_rows.append(
                    Counterfactual(
                        at=at,
                        approved=False,
                        rejection=str(forced.rejection_code),
                        pnl_quote=Decimal(0),
                        reason="",
                        llm_action=action,
                    )
                )
        i += 1

    curve.append((_close_time(h4[last]), equity))
    baseline_curve.append((_close_time(h4[last]), baseline_equity))

    first_close = _decimal(h4[first][4])
    last_close = _decimal(h4[last][4])
    buy_and_hold = (last_close - first_close) / first_close if first_close else Decimal(0)

    return BacktestResult(
        symbol=symbol,
        model=str(getattr(settings.llm, "model", "?")),
        period_start=_close_time(h4[first]),
        period_end=_close_time(h4[last]),
        candles=last - first + 1,
        decisions=decisions,
        buy=buy,
        hold=hold,
        downgraded=downgraded,
        degraded=degraded,
        inference_ms=inference,
        approvals=approvals,
        rejections=rejections,
        trades=trades,
        counterfactuals=counterfactual_rows,
        equity_start=_decimal(settings.paper_start_equity),
        equity_end=equity,
        max_drawdown_pct=_max_drawdown(curve),
        buy_and_hold_pct=buy_and_hold,
        baseline_trades=baseline_trades,
        baseline_equity_end=baseline_equity,
        baseline_max_drawdown_pct=_max_drawdown(baseline_curve),
        sell=sell,
        halt=halt,
        llm_exits=llm_exits,
    )
