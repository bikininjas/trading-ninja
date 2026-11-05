"""Laboratoire du mandat short — mesure hors production.

Ce module ne passe aucun ordre et n'écrit pas en base. Il répond à une seule
question : **si le mandat autorisait la vente à découvert, la même fenêtre
historique aurait-elle été positive ?**

Il réutilise les bougies et les indicateurs du backtest, mais il ne touche pas au
Risk Gateway : le short réel demande un autre lieu d'exécution (marge spot ou
futures) et des garde-fous de liquidation qui n'existent pas encore. Un résultat
positif ici est une hypothèse à valider, pas une autorisation de trader.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from app.backtest.golden import GoldenBundle
from app.market.feed import atr, ema, rsi

__all__ = ["ShortResult", "ShortTrade", "simulate_short"]

ZERO = Decimal("0")
ONE = Decimal("1")

# Frais taker Kraken du compte (D-048), slippage pessimiste du broker papier.
DEFAULT_FEE = Decimal("0.008")
DEFAULT_SLIPPAGE = Decimal("0.0005")
DEFAULT_START_EQUITY = Decimal("9.37")
DEFAULT_RISK_PCT = Decimal("0.0175")
DEFAULT_MAX_NOTIONAL_PCT = Decimal("0.50")
DEFAULT_MIN_NOTIONAL = Decimal("4.16")  # BTC/EUR, minimum Kraken observé


@dataclass(frozen=True, slots=True)
class ShortTrade:
    entry_at: datetime
    exit_at: datetime
    entry_price: Decimal
    exit_price: Decimal
    amount: Decimal
    notional_quote: Decimal
    fees_quote: Decimal
    pnl_quote: Decimal
    reason: str
    stop_price: Decimal
    take_profit_price: Decimal
    rsi: Decimal
    slope_atr: Decimal
    distance_to_ema50_atr: Decimal


@dataclass(frozen=True, slots=True)
class ShortResult:
    symbol: str
    trades: list[ShortTrade] = field(default_factory=list)
    equity_start: Decimal = DEFAULT_START_EQUITY
    equity_end: Decimal = DEFAULT_START_EQUITY
    buy_and_hold_pct: Decimal = ZERO
    skipped_below_minimum: int = 0

    @property
    def pnl_quote(self) -> Decimal:
        return self.equity_end - self.equity_start

    @property
    def return_pct(self) -> Decimal:
        return self.pnl_quote / self.equity_start if self.equity_start else ZERO

    @property
    def wins(self) -> list[ShortTrade]:
        return [t for t in self.trades if t.pnl_quote > 0]

    @property
    def win_rate(self) -> Decimal:
        if not self.trades:
            return ZERO
        return Decimal(len(self.wins)) / Decimal(len(self.trades))

    def summary(self) -> str:
        lines = [
            f"{self.symbol} · mandat short · équité {self.equity_start:.2f} → "
            f"{self.equity_end:.2f}",
            f"trades                {len(self.trades)} · gagnants {len(self.wins)} · "
            f"réussite {self.win_rate:.1%}",
            f"PnL net               {self.pnl_quote:+.4f} EUR "
            f"({self.return_pct:+.2%})",
            f"buy & hold            {self.buy_and_hold_pct:+.2%}",
            f"sous le minimum       {self.skipped_below_minimum}",
        ]
        for t in self.trades:
            lines.append(
                f"  {t.entry_at:%Y-%m-%d %H:%M} → {t.exit_at:%Y-%m-%d %H:%M} "
                f"{t.reason:9s} {t.pnl_quote:+.4f} EUR"
            )
        return "\n".join(lines)


def _dt(timestamp_ms: int) -> datetime:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)


def _decimal(value: object) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _h1_return(h1: list[list], close_ms: int) -> Decimal:
    """Rendement de la dernière H1 clôturée avant `close_ms`."""
    closed = [c for c in h1 if int(c[0]) + 3_600_000 <= close_ms]
    if len(closed) < 2:
        return ZERO
    last = _decimal(closed[-1][4])
    previous = _decimal(closed[-2][4])
    return (last - previous) / last if last else ZERO


def _short_pnl(entry: Decimal, exit_price: Decimal, amount: Decimal,
               fee_rate: Decimal) -> tuple[Decimal, Decimal]:
    """PnL net d'un short : on vend à l'entrée, on rachète à la sortie."""
    entry_notional = entry * amount
    exit_notional = exit_price * amount
    fees = (entry_notional + exit_notional) * fee_rate
    return (entry - exit_price) * amount - fees, fees


def simulate_short(
    bundle: GoldenBundle,
    *,
    slope_max: Decimal | None = Decimal("0"),
    rsi_min: Decimal = Decimal("30"),
    rsi_max: Decimal = Decimal("65"),
    require_negative_h1: bool = False,
    stop_atr: Decimal = Decimal("2.5"),
    take_profit_r: Decimal = Decimal("1.5"),
    trend_buffer_pct: Decimal = Decimal("0.005"),
    pullback_atr: Decimal = Decimal("0.75"),
    fee_rate: Decimal = DEFAULT_FEE,
    slippage: Decimal = DEFAULT_SLIPPAGE,
    start_equity: Decimal = DEFAULT_START_EQUITY,
    risk_pct: Decimal = DEFAULT_RISK_PCT,
    max_notional_pct: Decimal = DEFAULT_MAX_NOTIONAL_PCT,
    min_notional: Decimal = DEFAULT_MIN_NOTIONAL,
) -> ShortResult:
    """Rejoue une fenêtre avec un mandat short, sans lookahead ni ordre réel."""
    h4 = bundle.h4
    if len(h4) < 261:
        raise ValueError("historique H4 insuffisant pour la pente EMA200")

    closes = [_decimal(c[4]) for c in h4]
    opens = [_decimal(c[1]) for c in h4]
    highs = [_decimal(c[2]) for c in h4]
    lows = [_decimal(c[3]) for c in h4]

    equity = _decimal(start_equity)
    trades: list[ShortTrade] = []
    skipped = 0
    start = max(260, bundle.start_index)
    end = min(bundle.end_index, len(h4) - 1)

    i = start
    while i < end:
        price = closes[i]
        ema200 = ema(closes[: i + 1], 200)
        ema50 = ema(closes[: i + 1], 50)
        atr_h4 = atr(h4[: i + 1], 14)
        rsi_h4 = rsi(h4[: i + 1], 14)
        if atr_h4 <= 0 or price <= 0:
            i += 1
            continue

        ema200_prev = ema(closes[: i - 59], 200)
        slope = (ema200 - ema200_prev) / atr_h4
        distance = (price - ema50) / atr_h4
        close_ms = int(h4[i][0]) + 4 * 3_600_000
        h1_return = _h1_return(bundle.h1, close_ms)

        setup = (
            price < ema200 * (ONE - trend_buffer_pct)
            and ema50 < ema200
            and rsi_min <= rsi_h4 <= rsi_max
            and -pullback_atr <= distance <= pullback_atr
            and (slope_max is None or slope <= slope_max)
            and (not require_negative_h1 or h1_return < 0)
        )
        if not setup:
            i += 1
            continue

        stop_distance = stop_atr * atr_h4
        risk_quote = equity * risk_pct
        amount = risk_quote / stop_distance
        notional = amount * price
        if notional > equity * max_notional_pct:
            notional = equity * max_notional_pct
            amount = notional / price
        if notional < min_notional:
            skipped += 1
            i += 1
            continue

        entry_price = price * (ONE - slippage)
        stop_price = entry_price + stop_distance
        take_profit_price = entry_price - take_profit_r * stop_distance

        exit_index = i + 1
        exit_price: Decimal | None = None
        reason = "OPEN"
        while exit_index <= end:
            candle_open = opens[exit_index]
            high = highs[exit_index]
            low = lows[exit_index]
            if high >= stop_price:
                raw = max(candle_open, stop_price)
                exit_price = raw * (ONE + slippage)
                reason = "STOP"
                break
            if low <= take_profit_price:
                raw = min(candle_open, take_profit_price)
                exit_price = raw * (ONE + slippage)
                reason = "TP"
                break
            exit_index += 1
        if exit_price is None:
            exit_price = closes[end] * (ONE + slippage)
            exit_index = end
            reason = "END_OF_WINDOW"

        pnl, fees = _short_pnl(entry_price, exit_price, amount, fee_rate)
        equity += pnl
        trades.append(
            ShortTrade(
                entry_at=_dt(int(h4[i][0])),
                exit_at=_dt(int(h4[exit_index][0])),
                entry_price=entry_price,
                exit_price=exit_price,
                amount=amount,
                notional_quote=notional,
                fees_quote=fees,
                pnl_quote=pnl,
                reason=reason,
                stop_price=stop_price,
                take_profit_price=take_profit_price,
                rsi=rsi_h4,
                slope_atr=slope,
                distance_to_ema50_atr=distance,
            )
        )
        i = exit_index + 1

    first_close = closes[start]
    buy_hold = (closes[end] - first_close) / first_close if first_close else ZERO
    return ShortResult(
        symbol=bundle.symbol,
        trades=trades,
        equity_start=_decimal(start_equity),
        equity_end=equity,
        buy_and_hold_pct=buy_hold,
        skipped_below_minimum=skipped,
    )
