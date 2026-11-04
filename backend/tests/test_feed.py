"""Tests du flux de marché et des indicateurs, sur bougies synthétiques."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.market.feed import (
    ATR_PERIOD, EMA_PERIOD, FeedError, atr, build_snapshot, closed_candles, ema,
    macd_histogram, rsi,
)

H4 = 14_400_000


def candles(n: int, start: float = 100.0, step: float = 0.1, spread: float = 0.5,
            t0: int = 1_700_000_000_000) -> list[list]:
    out = []
    for i in range(n):
        close = start + i * step
        out.append([t0 + i * H4, close - 0.05, close + spread, close - spread, close, 10.0])
    return out


class FakeExchange:
    def __init__(self, h4: list[list], h1: list[list], now_ms: int, book: dict | None = None):
        self._h4, self._h1, self._now = h4, h1, now_ms
        self._book = book or {"bids": [[100.0, 2.0]], "asks": [[100.1, 1.0]]}

    def fetch_ohlcv(self, symbol, timeframe, limit=None):
        return list(self._h4 if timeframe == "4h" else self._h1)

    def milliseconds(self):
        return self._now

    def fetch_order_book(self, symbol, limit=20):
        return self._book


def test_ema_rises_in_an_uptrend():
    values = [Decimal(str(100 + i)) for i in range(300)]
    value = ema(values, EMA_PERIOD)
    assert Decimal(100) < value < Decimal(399)


def test_ema_refuses_short_history():
    with pytest.raises(FeedError):
        ema([Decimal(1)] * 10, 200)


def test_atr_matches_a_constant_range():
    # Chaque bougie a high-low = 1, les clôtures montent de 0,1 : TR = 1.
    value = atr(candles(40), ATR_PERIOD)
    assert value == Decimal("1")


def test_rsi_is_maximal_on_a_pure_uptrend():
    assert rsi(candles(60)) == Decimal("100")


def test_rsi_is_minimal_on_a_pure_downtrend():
    falling = candles(60, step=-0.1)
    assert rsi(falling) == Decimal("0")


def test_macd_is_positive_in_an_uptrend():
    assert macd_histogram(candles(120)) > 0


def test_closed_candles_drop_the_unfinished_one():
    series = candles(10)
    last_ts = series[-1][0]
    exchange = FakeExchange(series, series, now_ms=last_ts + H4 + 1)
    assert len(closed_candles(exchange, "BTC/EUR", "4h", 10)) == 10

    exchange = FakeExchange(series, series, now_ms=last_ts + 1)
    assert len(closed_candles(exchange, "BTC/EUR", "4h", 10)) == 9


def test_build_snapshot_uses_only_closed_candles():
    h4 = candles(320)
    h1 = candles(120, t0=1_700_000_000_000, step=0.01, spread=0.2)
    now = h4[-1][0] + H4 + 1
    exchange = FakeExchange(h4, h1, now_ms=now)

    class Info:
        constraints = None
        quote = "EUR"

    state = build_snapshot(exchange, "BTC/EUR", info=Info(), now=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert state.snapshot.price == Decimal(str(h4[-1][4]))
    assert state.snapshot.ema_200_h4 > 0
    assert state.snapshot.atr_14_h4 > 0
    assert state.context.rsi_14_h4 == Decimal("100")
    assert state.context.order_book_imbalance.quantize(Decimal("0.0001")) == Decimal("0.3333")


def test_build_snapshot_refuses_insufficient_history():
    h4 = candles(50)
    h1 = candles(120)
    exchange = FakeExchange(h4, h1, now_ms=h4[-1][0] + H4 + 1)

    class Info:
        pass

    with pytest.raises(FeedError, match="EMA200"):
        build_snapshot(exchange, "BTC/EUR", info=Info())
