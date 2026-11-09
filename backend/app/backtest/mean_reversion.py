"""Laboratoire Mean Reversion — hypothèse mesurée, jamais activée.

Règle testée :

- **entrée** : clôture sous la bande de Bollinger basse (20 ; 2), RSI(14) < 32,
  volume de la bougie > moyenne des 20 volumes précédents ;
- **sortie** : contact avec la bande médiane (SMA 20), ou stop structurel
  d'urgence sous le creux des 10 dernières bougies.

Le pare-feu reste l'autorité : chaque intention passe par ``evaluate()``. Deux
variantes sont mesurées séparément :

- ``gated`` : la configuration exécutable, filtre « prix au-dessus de l'EMA200 »
  compris ;
- ``raw`` : ce même filtre neutralisé (``allow_counter_trend``) pour mesurer
  l'hypothèse elle-même. **Jamais activable en production.**

Le module ne fait aucune I/O : le chargement des bougies et l'agrégation D1
restent dans ``scripts/backtest.py``.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Sequence, cast

from app.backtest.engine import (
    BacktestResult,
    TradeRecord,
    _drawdowns,
    _max_drawdown,
    _pnl,
)
from app.backtest.swing import (
    _atr_series,
    _bollinger,
    _ema_series,
    _rolling_min_inclusive,
    _rsi_series,
)
from app.engine.risk_gate import (
    AccountState,
    Action,
    MarketRegime,
    MarketSnapshot,
    RiskConfig,
    StopMode,
    TradeIntent,
    evaluate,
)
from app.execution.paper import PaperBroker
from app.market.feed import MarketContext

__all__ = [
    "MeanReversionSeries",
    "build_series",
    "evaluate_mean_reversion",
    "run_mean_reversion_backtest",
    "snapshot_at",
]

BB_PERIOD = 20
BB_STD = Decimal("2")
RSI_PERIOD = 14
VOLUME_PERIOD = 20
SWING_LOW = 10
ATR_PERIOD = 14
WARMUP = 260  # EMA200 + 60 bougies pour la pente

RSI_MAX = Decimal("32")


def _d(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _close_time(candle: list, step_ms: int) -> datetime:
    """Heure de clôture réelle : 4 h en H4, 24 h en D1."""
    return datetime.fromtimestamp((int(candle[0]) + step_ms) / 1000, tz=timezone.utc)


def _step_ms(candles: Sequence[Sequence[Any]]) -> int:
    deltas = [
        int(current[0]) - int(previous[0])
        for previous, current in zip(candles, candles[1:])
        if int(current[0]) > int(previous[0])
    ]
    return min(deltas) if deltas else 4 * 3_600_000


def _volume_sma(volumes: Sequence[Decimal], period: int) -> list[Decimal | None]:
    out: list[Decimal | None] = [None] * len(volumes)
    total = Decimal(0)
    for index, volume in enumerate(volumes):
        total += volume
        if index >= period:
            total -= volumes[index - period]
        if index >= period - 1:
            out[index] = total / Decimal(period)
    return out


class MeanReversionSeries:
    """Indicateurs précalculés une fois pour tout le rejeu."""

    def __init__(self, candles: list[list]) -> None:
        if len(candles) < WARMUP:
            raise ValueError(f"historique insuffisant : {len(candles)} bougies")
        self.candles = candles
        self.step_ms = _step_ms(candles)
        self.closes = [_d(candle[4]) for candle in candles]
        self.highs = [_d(candle[2]) for candle in candles]
        self.lows = [_d(candle[3]) for candle in candles]
        self.volumes = [_d(candle[5]) if len(candle) > 5 else Decimal(0)
                        for candle in candles]
        self.ema50 = _ema_series(self.closes, 50)
        self.ema200 = _ema_series(self.closes, 200)
        self.rsi = _rsi_series(self.closes, RSI_PERIOD)
        self.atr = _atr_series(candles, ATR_PERIOD)
        self.swing_low = _rolling_min_inclusive(self.lows, SWING_LOW)
        upper, mid = _bollinger(self.closes, BB_PERIOD, BB_STD)
        self.boll_upper = upper
        self.boll_mid = mid
        # La bande basse est le symétrique de la haute autour de la moyenne.
        self.boll_lower = [
            (2 * middle - high) if middle is not None and high is not None else None
            for high, middle in zip(upper, mid)
        ]
        self.volume_sma = _volume_sma(self.volumes, VOLUME_PERIOD)


def build_series(candles: list[list]) -> MeanReversionSeries:
    return MeanReversionSeries(candles)


def evaluate_mean_reversion(series: MeanReversionSeries, index: int,
                            *, rsi_max: Decimal = RSI_MAX) -> str | None:
    """Rend le motif d'entrée, ou ``None``. Aucun accès au futur."""
    close = series.closes[index]
    lower = series.boll_lower[index]
    rsi = series.rsi[index]
    volume = series.volumes[index]
    volume_sma = series.volume_sma[index]
    if lower is None or rsi is None or volume_sma is None or volume_sma <= 0:
        return None
    if close >= lower:
        return None
    if rsi >= rsi_max:
        return None
    if volume <= volume_sma:
        return None
    return (
        f"clôture {close} sous BB basse {lower:.2f} · RSI {rsi:.2f} < {rsi_max} · "
        f"volume {volume:.4f} > SMA20 {volume_sma:.4f}"
    )


def snapshot_at(series: MeanReversionSeries, index: int,
                symbol: str) -> tuple[MarketSnapshot, MarketContext]:
    close = series.closes[index]
    atr = cast(Decimal, series.atr[index])
    at = _close_time(series.candles[index], series.step_ms)
    snapshot = MarketSnapshot(
        symbol=symbol,
        price=close,
        ema_200_h4=series.ema200[index],
        atr_14_h4=atr,
        last_closed_h1_return_atr=(
            (close - _d(series.candles[index][1])) / atr
        ),
        tick_at=at,
        now=at,
        ema_200_slope_atr=(
            (series.ema200[index] - series.ema200[index - 60]) / atr
            if index >= 60 else Decimal(0)
        ),
        swing_low_10d=cast(Decimal, series.swing_low[index]),
    )
    context = MarketContext(
        ema_50_h4=series.ema50[index],
        rsi_14_h4=cast(Decimal, series.rsi[index]),
        macd_histogram=Decimal(0),
        order_book_imbalance=Decimal(0),
        candles_closed=index + 1,
    )
    return snapshot, context


def run_mean_reversion_backtest(
    *,
    symbol: str,
    info: Any,
    candles: list[list],
    settings: Any,
    broker: PaperBroker | None = None,
    start_index: int | None = None,
    end_index: int | None = None,
    rsi_max: Decimal = RSI_MAX,
    exit_mode: str = "touch",
    allow_counter_trend: bool = False,
) -> BacktestResult:
    """Rejoue l'hypothèse moyenne-réversion sur des bougies clôturées."""
    if exit_mode not in {"touch", "close"}:
        raise ValueError(f"exit_mode inconnu : {exit_mode!r}")
    # Le stop de ce laboratoire est structurel par construction : le pare-feu
    # reçoit le creux des 10 bougies, jamais un multiple ATR du timeframe.
    config: RiskConfig = replace(
        settings.risk,
        stop_mode=StopMode.STRUCTURAL_D1,
        take_profit_r_multiple=Decimal(0),
        allow_counter_trend=allow_counter_trend,
    )
    settings = replace(settings, risk=config)
    series = MeanReversionSeries(candles)
    paper = broker or PaperBroker(info.taker_fee, settings.paper_slippage_bps)

    first = max(start_index or 0, WARMUP)
    last = min(end_index if end_index is not None else len(candles) - 1,
               len(candles) - 1)
    if last <= first:
        raise ValueError("fenêtre mean-reversion vide")

    equity = _d(settings.paper_start_equity)
    curve: list[tuple[datetime, Decimal]] = [(_close_time(candles[first - 1], series.step_ms), equity)]
    trades: list[TradeRecord] = []
    rejections: Counter = Counter()
    decisions = approvals = 0
    consecutive_losses = 0

    index = first
    while index <= last:
        reason = evaluate_mean_reversion(series, index, rsi_max=rsi_max)
        if reason is None:
            index += 1
            continue
        if series.atr[index] is None or series.swing_low[index] is None:
            index += 1
            continue

        snapshot, context = snapshot_at(series, index, symbol)
        at = _close_time(candles[index], series.step_ms)
        price = series.closes[index]
        daily, weekly, hwm = _drawdowns(curve, at)
        account = AccountState(
            equity_quote=equity,
            free_quote=equity,
            consecutive_losses=consecutive_losses,
            daily_drawdown_pct=daily,
            weekly_drawdown_pct=weekly,
            drawdown_from_hwm_pct=hwm,
        )
        decisions += 1
        decision = evaluate(
            TradeIntent(Action.BUY, 1.0, MarketRegime.RANGING, reason),
            snapshot,
            account,
            info.constraints,
            config,
        )
        if not decision.approved:
            rejections[str(decision.rejection_code)] += 1
            index += 1
            continue

        approvals += 1
        entry = paper.entry(decision, price)
        amount = decision.amount or Decimal(0)
        initial_stop = decision.stop_loss_price or Decimal(0)
        exit_index = last
        exit_fill = None
        exit_reason = "END_OF_WINDOW"

        for j in range(index + 1, last + 1):
            # Le stop structurel est prioritaire : sans ticks, supposer que la
            # bande médiane a été touchée avant le plus bas serait optimiste.
            trigger = PaperBroker.exit_trigger(initial_stop, None, candles[j])
            if trigger is not None:
                exit_price, exit_reason = trigger
                exit_index, exit_fill = j, paper.exit(amount, exit_price)
                break
            middle = series.boll_mid[j]
            if middle is None:
                continue
            if exit_mode == "touch" and series.highs[j] >= middle:
                exit_index, exit_fill = j, paper.exit(amount, middle)
                exit_reason = "MID_BAND_TOUCH"
                break
            if exit_mode == "close" and series.closes[j] >= middle:
                exit_index, exit_fill = j, paper.exit(amount, series.closes[j])
                exit_reason = "MID_BAND_CLOSE"
                break

        if exit_fill is None:
            exit_fill = paper.exit(amount, series.closes[exit_index])

        pnl = _pnl(entry, exit_fill)
        equity += pnl
        consecutive_losses = 0 if pnl > 0 else consecutive_losses + 1
        curve.append((_close_time(candles[exit_index], series.step_ms), equity))
        trades.append(TradeRecord(
            entry_at=at,
            exit_at=_close_time(candles[exit_index], series.step_ms),
            entry_price=entry.price,
            exit_price=exit_fill.price,
            amount=amount,
            notional_quote=entry.notional_quote,
            fees_quote=entry.fee_quote + exit_fill.fee_quote,
            pnl_quote=pnl,
            reason=exit_reason,
            stop_loss_price=initial_stop,
            take_profit_price=None,
            confidence=Decimal(1),
            thesis=reason,
        ))
        index = exit_index + 1

    curve.append((_close_time(candles[last], series.step_ms), equity))
    first_close, last_close = series.closes[first], series.closes[last]
    buy_and_hold = (
        (last_close - first_close) / first_close if first_close else Decimal(0)
    )

    from app.insights.diagnostics import backtest_trade_diagnostics
    trades=[replace(t,diagnostics=backtest_trade_diagnostics(t,candles,series.step_ms)) for t in trades]
    return BacktestResult(
        symbol=symbol,
        model="MEAN_REVERSION_BB",
        period_start=_close_time(candles[first], series.step_ms),
        period_end=_close_time(candles[last], series.step_ms),
        candles=last - first + 1,
        decisions=decisions,
        buy=approvals,
        hold=decisions - approvals,
        downgraded=0,
        degraded=0,
        inference_ms=[],
        approvals=approvals,
        rejections=rejections,
        trades=trades,
        counterfactuals=[],
        equity_start=_d(settings.paper_start_equity),
        equity_end=equity,
        max_drawdown_pct=_max_drawdown(curve),
        buy_and_hold_pct=buy_and_hold,
        baseline_trades=[],
        baseline_equity_end=_d(settings.paper_start_equity),
        baseline_max_drawdown_pct=Decimal(0),
    )
