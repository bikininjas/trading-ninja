"""Rejeu du moteur swing Daily — le vrai pare-feu, le vrai broker, aucune I/O.

Le Daily ne partage pas la cadence H4 : une seule décision par bougie D1 clôturée.
Les indicateurs sont précalculés en O(n) (EMA, RSI, ATR, Donchian, Bollinger) pour
que 10 ans de données restent instantanés.

Deux règles de sortie, dans cet ordre :

1. **stop structurel** (intrabar, prioritaire sur tout le reste) ;
2. **sortie de tendance** à la clôture, selon ``SWING_EXIT_MODE`` :
   - ``EMA20`` : sous l'EMA20 D1, ou au-dessus de la bande de Bollinger
     (20, 2,5) avec divergence baissière du RSI ;
   - ``CHANDELIER`` : sous le plus haut des N D1 moins N × ATR, stop à cliquet
     qui ne redescend jamais.

Le passage à seuil de rentabilité (+1 R) est appliqué **après** la bougie qui a
touché le seuil, jamais dans la même bougie : sans ticks, supposer que le stop a
bougé avant le plus bas serait optimiste.

Aucun ordre, aucune écriture en base, aucun LLM.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Sequence, cast

from app.backtest.engine import BacktestResult, TradeRecord
from app.engine.entry_rule import evaluate_entry
from app.engine.exit_rule import should_exit_chandelier, update_chandelier_stop
from app.engine.risk_gate import (
    AccountState,
    Action,
    MarketRegime,
    MarketSnapshot,
    RiskConfig,
    SwingExitMode,
    TradeIntent,
    evaluate,
)
from app.execution.paper import PaperBroker
from app.market.feed import (
    BOLLINGER_PERIOD,
    BOLLINGER_STD,
    D1_DONCHIAN,
    D1_SWING_LOW,
    D1_WARMUP,
    D1Context,
    MarketContext,
)

__all__ = ["SwingSeries", "build_series", "run_swing_backtest", "snapshot_at"]

D1_MS = 86_400_000
RSI_PERIOD = 14
ATR_PERIOD = 14
DIVERGENCE_LOOKBACK = 10


def _d(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _ema_series(values: Sequence[Decimal], period: int) -> list[Decimal]:
    alpha = Decimal(2) / Decimal(period + 1)
    out = [values[0]]
    for value in values[1:]:
        out.append(value * alpha + out[-1] * (1 - alpha))
    return out


def _rsi_series(closes: Sequence[Decimal], period: int = RSI_PERIOD) -> list[Decimal | None]:
    out: list[Decimal | None] = [None] * len(closes)
    if len(closes) <= period:
        return out
    gains: list[Decimal] = []
    losses: list[Decimal] = []
    for previous, current in zip(closes, closes[1:]):
        delta = current - previous
        gains.append(max(delta, Decimal(0)))
        losses.append(max(-delta, Decimal(0)))
    avg_gain = sum(gains[:period]) / Decimal(period)
    avg_loss = sum(losses[:period]) / Decimal(period)

    def value(gain: Decimal, loss: Decimal) -> Decimal:
        if loss == 0:
            return Decimal("100")
        rs = gain / loss
        return Decimal("100") - (Decimal("100") / (Decimal(1) + rs))

    out[period] = value(avg_gain, avg_loss)
    for i in range(period + 1, len(closes)):
        avg_gain = (avg_gain * (period - 1) + gains[i - 1]) / Decimal(period)
        avg_loss = (avg_loss * (period - 1) + losses[i - 1]) / Decimal(period)
        out[i] = value(avg_gain, avg_loss)
    return out


def _atr_series(candles: Sequence[Sequence[Any]], period: int = ATR_PERIOD) -> list[Decimal | None]:
    out: list[Decimal | None] = [None] * len(candles)
    true_ranges: list[Decimal] = []
    for i in range(1, len(candles)):
        high = _d(candles[i][2])
        low = _d(candles[i][3])
        prev_close = _d(candles[i - 1][4])
        true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
        if i >= period:
            window = true_ranges[-period:]
            out[i] = sum(window) / Decimal(period)
    return out


def _bollinger(closes: Sequence[Decimal], period: int, k: Decimal) -> tuple[list[Decimal | None], list[Decimal | None]]:
    upper: list[Decimal | None] = [None] * len(closes)
    mid: list[Decimal | None] = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        window = closes[i - period + 1:i + 1]
        mean = sum(window) / Decimal(period)
        variance = sum(((value - mean) ** 2 for value in window), Decimal(0)) / Decimal(period)
        mid[i] = mean
        upper[i] = mean + k * variance.sqrt()
    return upper, mid


def _rolling_previous_max(values: Sequence[Decimal], window: int) -> list[Decimal | None]:
    out: list[Decimal | None] = [None] * len(values)
    for i in range(window, len(values)):
        out[i] = max(values[i - window:i])
    return out


def _rolling_min_inclusive(values: Sequence[Decimal], window: int) -> list[Decimal | None]:
    out: list[Decimal | None] = [None] * len(values)
    for i in range(window - 1, len(values)):
        out[i] = min(values[i - window + 1:i + 1])
    return out


class SwingSeries:
    """Séries d'indicateurs D1, calculées une fois pour tout le rejeu."""

    def __init__(self, candles: list[list]) -> None:
        if len(candles) < D1_WARMUP:
            raise ValueError(f"historique D1 insuffisant : {len(candles)} bougies")
        self.candles = candles
        self.closes = [_d(c[4]) for c in candles]
        self.highs = [_d(c[2]) for c in candles]
        self.lows = [_d(c[3]) for c in candles]
        self.ema20 = _ema_series(self.closes, 20)
        self.ema50 = _ema_series(self.closes, 50)
        self.ema200 = _ema_series(self.closes, 200)
        self.rsi = _rsi_series(self.closes)
        self.atr = _atr_series(candles)
        self.donchian = _rolling_previous_max(self.highs, D1_DONCHIAN)
        self.swing_low = _rolling_min_inclusive(self.lows, D1_SWING_LOW)
        self.boll_upper, self.boll_mid = _bollinger(
            self.closes, BOLLINGER_PERIOD, BOLLINGER_STD
        )

    def divergence(self, index: int) -> bool:
        """Divergence baissière : nouveau sommet de clôture, RSI plus bas qu'au sommet."""
        upper = self.boll_upper[index]
        rsi_now = self.rsi[index]
        if upper is None or rsi_now is None:
            return False
        if self.closes[index] <= upper:
            return False
        start = max(0, index - DIVERGENCE_LOOKBACK)
        prior = range(start, index)
        if not prior:
            return False
        peak = max(prior, key=lambda k: self.closes[k])
        rsi_peak = self.rsi[peak]
        return rsi_peak is not None and rsi_now < rsi_peak - Decimal(2)

    def highest_high_at(self, index: int, period: int) -> Decimal | None:
        """Plus haut des ``period`` bougies jusqu'à ``index`` inclus."""
        if period < 2 or index + 1 < period:
            return None
        return max(self.highs[index - period + 1:index + 1])

    def context(self, index: int, *, chandelier_period: int = D1_DONCHIAN) -> D1Context | None:
        highest_high = self.highest_high_at(index, chandelier_period)
        values = (
            self.ema20[index], self.ema50[index], self.ema200[index],
            self.ema50[index - 5], self.rsi[index], self.atr[index],
            self.donchian[index], self.boll_upper[index], self.boll_mid[index],
            self.swing_low[index], highest_high,
        )
        if any(value is None for value in values):
            return None
        if index < 1:
            return None
        ema20 = cast(Decimal, self.ema20[index])
        ema50 = cast(Decimal, self.ema50[index])
        ema200 = cast(Decimal, self.ema200[index])
        ema20_prev = cast(Decimal, self.ema20[index - 1])
        ema50_prev = cast(Decimal, self.ema50[index - 5])
        rsi_14 = cast(Decimal, self.rsi[index])
        atr_14 = cast(Decimal, self.atr[index])
        donchian = cast(Decimal, self.donchian[index])
        upper = cast(Decimal, self.boll_upper[index])
        mid = cast(Decimal, self.boll_mid[index])
        low10 = cast(Decimal, self.swing_low[index])
        chandelier_high = cast(Decimal, highest_high)
        last, previous = self.candles[index], self.candles[index - 1]
        return D1Context(
            ema_20=ema20,
            ema_50=ema50,
            ema_200=ema200,
            ema_20_prev=ema20_prev,
            ema_50_slope_5d_pct=(
                (ema50 - ema50_prev) / ema50_prev
                if ema50_prev > 0 else Decimal(0)
            ),
            rsi_14=rsi_14,
            atr_14=atr_14,
            donchian_high_20=donchian,
            bollinger_upper=upper,
            bollinger_mid=mid,
            swing_low_10=low10,
            last_open=_d(last[1]),
            last_close=self.closes[index],
            last_low=self.lows[index],
            last_high=self.highs[index],
            prev_close=self.closes[index - 1],
            prev_low=self.lows[index - 1],
            bearish_divergence=self.divergence(index),
            highest_high_chandelier=chandelier_high,
        )


def build_series(candles: list[list]) -> SwingSeries:
    return SwingSeries(candles)


def snapshot_at(
    series: SwingSeries,
    index: int,
    symbol: str,
    *,
    chandelier_period: int = D1_DONCHIAN,
) -> tuple[MarketSnapshot, MarketContext]:
    """Construit l'état D1 exact que voit le pare-feu et le LLM à ``index``."""
    context_d1 = series.context(index, chandelier_period=chandelier_period)
    if context_d1 is None:
        raise ValueError(f"contexte D1 incomplet à l'indice {index}")
    at = _close_time(series.candles[index])
    atr_now = cast(Decimal, series.atr[index])
    snapshot = MarketSnapshot(
        symbol=symbol,
        price=series.closes[index],
        ema_200_h4=series.ema200[index],
        atr_14_h4=atr_now,
        last_closed_h1_return_atr=(
            (series.closes[index] - _d(series.candles[index][1])) / atr_now
        ),
        tick_at=at,
        now=at,
        ema_200_slope_atr=(
            (series.ema200[index] - series.ema200[index - 60]) / atr_now
            if index >= 60 else Decimal(0)
        ),
        swing_low_10d=cast(Decimal, series.swing_low[index]),
    )
    context = MarketContext(
        ema_50_h4=series.ema50[index],
        rsi_14_h4=context_d1.rsi_14,
        macd_histogram=Decimal(0),
        order_book_imbalance=Decimal(0),
        candles_closed=index + 1,
        d1=context_d1,
    )
    return snapshot, context


def _close_time(candle: list) -> datetime:
    return datetime.fromtimestamp((int(candle[0]) + D1_MS) / 1000, tz=timezone.utc)


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


def _max_drawdown(curve: list[tuple[datetime, Decimal]]) -> Decimal:
    peak = curve[0][1]
    worst = Decimal(0)
    for _, equity in curve:
        peak = max(peak, equity)
        if peak > 0:
            worst = max(worst, (peak - equity) / peak)
    return worst


def _pnl(entry: Any, exit_fill: Any) -> Decimal:
    return (
        exit_fill.notional_quote
        - entry.notional_quote
        - entry.fee_quote
        - exit_fill.fee_quote
    )


def run_swing_backtest(
    *,
    symbol: str,
    info: Any,
    d1: list[list],
    settings: Any,
    broker: PaperBroker | None = None,
    start_index: int | None = None,
    end_index: int | None = None,
) -> BacktestResult:
    """Rejoue la politique SWING_MACRO_D1 sur des bougies Daily clôturées."""
    config: RiskConfig = settings.risk
    series = SwingSeries(d1)
    paper = broker or PaperBroker(info.taker_fee, settings.paper_slippage_bps)
    fee_rate = paper.fee(Decimal(1))

    first = max(start_index or 0, D1_WARMUP)
    last = min(end_index if end_index is not None else len(d1) - 1, len(d1) - 1)
    if last <= first:
        raise ValueError("fenêtre swing vide")

    equity = _d(settings.paper_start_equity)
    curve: list[tuple[datetime, Decimal]] = [
        (_close_time(d1[first - 1]), equity)
    ]
    trades: list[TradeRecord] = []
    rejections: Counter = Counter()
    decisions = approvals = 0
    consecutive_losses = 0

    i = first
    while i <= last:
        try:
            snapshot, context = snapshot_at(
                series, i, symbol,
                chandelier_period=config.swing_chandelier_period,
            )
        except ValueError:
            i += 1
            continue

        at = _close_time(d1[i])
        price = series.closes[i]

        setup = evaluate_entry(snapshot, context, config)
        if not setup.triggered:
            i += 1
            continue

        decisions += 1
        daily, weekly, hwm = _drawdowns(curve, at)
        account = AccountState(
            equity_quote=equity,
            free_quote=equity,
            consecutive_losses=consecutive_losses,
            daily_drawdown_pct=daily,
            weekly_drawdown_pct=weekly,
            drawdown_from_hwm_pct=hwm,
        )
        decision = evaluate(
            TradeIntent(Action.BUY, 1.0, MarketRegime.TRENDING_UP, setup.reason),
            snapshot,
            account,
            info.constraints,
            config,
        )
        if not decision.approved:
            rejections[str(decision.rejection_code)] += 1
            i += 1
            continue

        approvals += 1
        entry = paper.entry(decision, price)
        amount = decision.amount or Decimal(0)
        initial_stop = decision.stop_loss_price or Decimal(0)
        stop = initial_stop
        trailing_stop = initial_stop
        entry_price = entry.price
        r_distance = entry_price - initial_stop
        breakeven = entry_price * (Decimal(1) + fee_rate * Decimal(2))

        exit_index = last
        exit_fill = None
        reason = "END_OF_WINDOW"
        for j in range(i + 1, last + 1):
            trigger = PaperBroker.exit_trigger(stop, None, d1[j])
            if trigger is not None:
                exit_price, reason = trigger
                exit_index, exit_fill = j, paper.exit(amount, exit_price)
                break

            if (config.swing_breakeven_r > 0 and stop < entry_price
                    and series.highs[j] >= entry_price + config.swing_breakeven_r * r_distance):
                stop = max(stop, breakeven)

            if config.swing_exit_mode is SwingExitMode.CHANDELIER:
                atr_j = series.atr[j]
                highest = series.highest_high_at(j, config.swing_chandelier_period)
                if atr_j is not None and highest is not None:
                    trailing_stop = update_chandelier_stop(
                        trailing_stop,
                        highest,
                        atr_j,
                        config.swing_chandelier_atr_multiple,
                    )
                    if should_exit_chandelier(series.closes[j], trailing_stop):
                        exit_index, exit_fill = j, paper.exit(amount, series.closes[j])
                        reason = "TREND_EXIT_CHANDELIER"
                        break
            else:
                if config.swing_exit_below_ema20 and series.closes[j] < series.ema20[j]:
                    exit_index, exit_fill = j, paper.exit(amount, series.closes[j])
                    reason = "TREND_EXIT_EMA20"
                    break
                if series.divergence(j):
                    exit_index, exit_fill = j, paper.exit(amount, series.closes[j])
                    reason = "TREND_EXIT_BOLLINGER"
                    break

        if exit_fill is None:
            exit_fill = paper.exit(amount, series.closes[exit_index])

        pnl = _pnl(entry, exit_fill)
        equity += pnl
        consecutive_losses = 0 if pnl > 0 else consecutive_losses + 1
        curve.append((_close_time(d1[exit_index]), equity))
        trades.append(
            TradeRecord(
                entry_at=at,
                exit_at=_close_time(d1[exit_index]),
                entry_price=entry.price,
                exit_price=exit_fill.price,
                amount=amount,
                notional_quote=entry.notional_quote,
                fees_quote=entry.fee_quote + exit_fill.fee_quote,
                pnl_quote=pnl,
                reason=reason,
                stop_loss_price=initial_stop,
                take_profit_price=None,
                confidence=Decimal(1),
                thesis=setup.reason,
            )
        )
        i = exit_index + 1

    curve.append((_close_time(d1[last]), equity))
    first_close, last_close = series.closes[first], series.closes[last]
    buy_and_hold = (
        (last_close - first_close) / first_close if first_close else Decimal(0)
    )

    return BacktestResult(
        symbol=symbol,
        model="SWING_MACRO_D1",
        period_start=_close_time(d1[first]),
        period_end=_close_time(d1[last]),
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
