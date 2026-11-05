"""Tests du simulateur de shadow trades — purs, sans réseau ni base."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.execution.paper import PaperBroker
from app.runtime.shadow import simulate_shadow

ENTRY_AT = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
ENTRY_MS = int(ENTRY_AT.timestamp() * 1000)
H4_MS = 14_400_000
BROKER = PaperBroker(Decimal("0.0026"), slippage_bps=5)
AMOUNT = Decimal("0.05")
STOP = Decimal("95")
TAKE = Decimal("110")


def candle(offset: int, open_: str, high: str, low: str, close: str) -> list:
    return [ENTRY_MS + offset * H4_MS, float(open_), float(high), float(low), float(close), 0]


def simulate(candles: list[list]):
    return simulate_shadow(
        candles,
        entry_at=ENTRY_AT,
        entry_price=Decimal("100"),
        amount=AMOUNT,
        stop_loss_price=STOP,
        take_profit_price=TAKE,
        broker=BROKER,
    )


def test_stop_loss_is_detected_before_the_take_profit():
    outcome = simulate([
        candle(0, "100", "101", "99", "100"),
        candle(1, "100", "111", "94", "96"),  # touche les deux : le stop gagne
    ])
    assert outcome is not None
    assert outcome.status == "STOP_LOSS"
    assert outcome.exit_price < STOP
    assert outcome.pnl_quote < 0


def test_take_profit_is_detected():
    outcome = simulate([
        candle(0, "100", "101", "99", "100"),
        candle(1, "100", "111", "99", "108"),
    ])
    assert outcome is not None
    assert outcome.status == "TAKE_PROFIT"
    assert outcome.exit_price > Decimal("109")
    assert outcome.pnl_quote > 0


def test_end_of_data_closes_at_the_last_close():
    outcome = simulate([
        candle(0, "100", "101", "99", "100"),
        candle(1, "100", "101", "99", "100.5"),
    ])
    assert outcome is not None
    assert outcome.status == "END_OF_DATA"
    assert outcome.pnl_quote < 0  # frais + slippage, sans mouvement favorable


def test_no_forward_candle_returns_none():
    assert simulate([]) is None


def test_a_candle_closing_after_the_decision_is_included():
    # La bougie ouvre avant la décision mais clôt après : elle compte.
    outcome = simulate([candle(0, "100", "111", "99", "108")])
    assert outcome is not None
    assert outcome.status == "TAKE_PROFIT"
