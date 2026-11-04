"""Flux de marché Kraken et indicateurs — bougies **clôturées** uniquement.

Une bougie en cours change jusqu'à sa clôture : s'en servir rend le comportement
irreproductible entre backtest, papier et réel. Toutes les fonctions de ce module
jettent la dernière bougie si elle n'est pas fermée, et `build_snapshot` refuse de
produire un instantané si l'historique est insuffisant.

Le module fournit deux objets distincts :

- `MarketSnapshot` : ce que consomme le pare-feu déterministe (prix, EMA200, ATR,
  retour de la dernière H1) ;
- `MarketContext` : ce que consomme le LLM (EMA50, RSI14, histogramme MACD,
  déséquilibre du carnet) — des indicateurs **qualitatifs** qui n'entrent jamais
  dans le dimensionnement.

Aucune I/O à l'import : l'exchange est injecté.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from app.engine.risk_gate import MarketSnapshot
from app.exchange.kraken import KrakenMarketInfo, market_info

__all__ = [
    "FeedError",
    "MarketContext",
    "SymbolState",
    "atr",
    "build_snapshot",
    "closed_candles",
    "ema",
    "last_price",
    "macd_histogram",
    "order_book_imbalance",
    "rsi",
]

TIMEFRAME_MS = {"1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}
EMA_PERIOD = 200
EMA_FAST = 50
ATR_PERIOD = 14
RSI_PERIOD = 14


class FeedError(RuntimeError):
    """Historique insuffisant ou données inexploitables."""


@dataclass(frozen=True, slots=True)
class MarketContext:
    """Contexte qualitatif transmis au modèle. Aucun de ces champs ne dimensionne."""

    ema_50_h4: Decimal
    rsi_14_h4: Decimal
    macd_histogram: Decimal
    order_book_imbalance: Decimal  # [-1, 1], positif = pression acheteuse
    candles_closed: int


@dataclass(frozen=True, slots=True)
class SymbolState:
    """Instantané complet d'une paire au moment T."""

    symbol: str
    info: KrakenMarketInfo
    snapshot: MarketSnapshot
    context: MarketContext
    last_close_at: datetime


def _decimal(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def closed_candles(exchange: Any, symbol: str, timeframe: str, limit: int,
                   now_ms: int | None = None) -> list[list]:
    """Bougies dont la fenêtre est terminée, la plus ancienne d'abord."""
    if timeframe not in TIMEFRAME_MS:
        raise FeedError(f"timeframe non géré : {timeframe}")
    candles = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
    now = now_ms if now_ms is not None else exchange.milliseconds()
    duration = TIMEFRAME_MS[timeframe]
    return [c for c in candles if c[0] + duration <= now]


def ema(values: list, period: int = EMA_PERIOD) -> Decimal:
    """EMA classique, alpha = 2/(n+1). `values` du plus ancien au plus récent."""
    if period <= 0:
        raise ValueError("period doit être positif")
    if len(values) < period:
        raise FeedError(f"EMA{period} : {len(values)} valeurs, {period} requises")
    alpha = Decimal(2) / Decimal(period + 1)
    current = _decimal(values[0])
    for value in values[1:]:
        current = _decimal(value) * alpha + current * (1 - alpha)
    return current


def atr(candles: list[list], period: int = ATR_PERIOD) -> Decimal:
    """ATR simple sur les `period` dernières variations vraies."""
    if len(candles) < period + 1:
        raise FeedError(f"ATR{period} : {len(candles)} bougies, {period + 1} requises")
    true_ranges: list[Decimal] = []
    for previous, current in zip(candles, candles[1:]):
        high, low = _decimal(current[2]), _decimal(current[3])
        prev_close = _decimal(previous[4])
        true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    window = true_ranges[-period:]
    return sum(window) / Decimal(period)


def rsi(candles: list[list], period: int = RSI_PERIOD) -> Decimal:
    """RSI de Wilder sur les clôtures. 100 si aucune perte sur la fenêtre."""
    if len(candles) < period + 1:
        raise FeedError(f"RSI{period} : {len(candles)} bougies, {period + 1} requises")
    closes = [_decimal(c[4]) for c in candles]
    deltas = [abs(closes[i] - closes[i - 1]) for i in range(1, len(closes))]
    gains = [max(closes[i] - closes[i - 1], Decimal(0)) for i in range(1, len(closes))]
    losses = [d - g for d, g in zip(deltas, gains)]
    avg_gain = sum(gains[:period]) / Decimal(period)
    avg_loss = sum(losses[:period]) / Decimal(period)
    for gain, loss in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + gain) / Decimal(period)
        avg_loss = (avg_loss * (period - 1) + loss) / Decimal(period)
    if avg_loss == 0:
        return Decimal("100")
    rs = avg_gain / avg_loss
    return Decimal("100") - (Decimal("100") / (Decimal(1) + rs))


def _ema_series(values: list[Decimal], period: int) -> list[Decimal]:
    alpha = Decimal(2) / Decimal(period + 1)
    out = [values[0]]
    for value in values[1:]:
        out.append(value * alpha + out[-1] * (1 - alpha))
    return out


def macd_histogram(candles: list[list], fast: int = 12, slow: int = 26,
                   signal: int = 9) -> Decimal:
    """Histogramme MACD = MACD - signal(EMA9 du MACD)."""
    if len(candles) < slow + signal + 1:
        raise FeedError(
            f"MACD : {len(candles)} bougies, {slow + signal + 1} requises"
        )
    closes = [_decimal(c[4]) for c in candles]
    start = slow - 1
    fast_series = _ema_series(closes, fast)
    slow_series = _ema_series(closes[start:], slow)
    # Aligne les deux séries sur les bougies où l'EMA lente existe.
    macd_line = [f - s for f, s in zip(fast_series[start:], slow_series)]
    signal_line = _ema_series(macd_line, signal)[-1]
    return macd_line[-1] - signal_line


def order_book_imbalance(exchange: Any, symbol: str, depth: int = 20) -> Decimal:
    """(volume acheteur - volume vendeur) / total, sur les `depth` premiers niveaux.

    Mesure publique, sans clé. Un carnet vide vaut 0 (neutre), jamais une division
    par zéro.
    """
    book = exchange.fetch_order_book(symbol, limit=depth)
    bid = sum((_decimal(level[1]) for level in book.get("bids", [])[:depth]), Decimal(0))
    ask = sum((_decimal(level[1]) for level in book.get("asks", [])[:depth]), Decimal(0))
    total = bid + ask
    if total <= 0:
        return Decimal(0)
    return (bid - ask) / total


def _close_time(candle: list, timeframe: str) -> datetime:
    return datetime.fromtimestamp((candle[0] + TIMEFRAME_MS[timeframe]) / 1000, tz=timezone.utc)


def build_snapshot(
    exchange: Any,
    symbol: str,
    *,
    decision_timeframe: str = "4h",
    entry_timeframe: str = "1h",
    now: datetime | None = None,
    info: KrakenMarketInfo | None = None,
    with_order_book: bool = True,
) -> SymbolState:
    """Construit l'instantané que consomment le pare-feu et le modèle.

    `price` est la clôture de la dernière bougie de décision ; `tick_at` est l'heure
    de clôture de cette bougie. Le runtime rafraîchit le prix juste avant
    `evaluate()` — voir `last_price` — pour que la latence du LLM ne transforme pas
    une décision en donnée périmée.
    """
    moment = now or datetime.now(timezone.utc)
    market = info or market_info(exchange, symbol)

    decision_candles = closed_candles(exchange, symbol, decision_timeframe, 320)
    entry_candles = closed_candles(exchange, symbol, entry_timeframe, 120)
    if len(decision_candles) < EMA_PERIOD + 1:
        raise FeedError(
            f"{symbol} : {len(decision_candles)} bougies {decision_timeframe} clôturées, "
            f"{EMA_PERIOD + 1} requises pour l'EMA200"
        )
    if len(entry_candles) < ATR_PERIOD + 1:
        raise FeedError(f"{symbol} : historique {entry_timeframe} insuffisant")

    closes = [c[4] for c in decision_candles]
    price = _decimal(closes[-1])
    ema_200 = ema(closes, EMA_PERIOD)
    atr_h4 = atr(decision_candles, ATR_PERIOD)

    last_entry = entry_candles[-1]
    entry_atr = atr(entry_candles, ATR_PERIOD)
    if entry_atr <= 0:
        raise FeedError(f"{symbol} : ATR {entry_timeframe} nul")
    h1_return_atr = (_decimal(last_entry[4]) - _decimal(last_entry[1])) / entry_atr

    context = MarketContext(
        ema_50_h4=ema(closes, EMA_FAST),
        rsi_14_h4=rsi(decision_candles, RSI_PERIOD),
        macd_histogram=macd_histogram(decision_candles),
        order_book_imbalance=(
            order_book_imbalance(exchange, symbol) if with_order_book else Decimal(0)
        ),
        candles_closed=len(decision_candles),
    )

    snapshot = MarketSnapshot(
        symbol=symbol,
        price=price,
        ema_200_h4=ema_200,
        atr_14_h4=atr_h4,
        last_closed_h1_return_atr=h1_return_atr,
        tick_at=_close_time(decision_candles[-1], decision_timeframe),
        now=moment,
    )
    return SymbolState(
        symbol=symbol,
        info=market,
        snapshot=snapshot,
        context=context,
        last_close_at=_close_time(decision_candles[-1], decision_timeframe),
    )


def last_price(exchange: Any, symbol: str) -> Decimal:
    """Prix courant, pour la vérification de fraîcheur juste avant `evaluate()`."""
    ticker = exchange.fetch_ticker(symbol)
    price = ticker.get("last")
    if price is None:
        raise FeedError(f"{symbol} : prix indisponible")
    return _decimal(price)
