"""Tests de l'exécution papier : frais, slippage, gaps, priorité du stop."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.engine.risk_gate import RejectionCode, RiskDecision
from app.execution.paper import PaperBroker


def decision(amount: str = "1", **kwargs) -> RiskDecision:
    base = {
        "approved": True,
        "amount": Decimal(amount),
        "stop_loss_price": Decimal("96"),
        "take_profit_price": Decimal("108"),
        "rejection_code": RejectionCode.NONE,
    }
    base.update(kwargs)
    return RiskDecision(**base)


def broker() -> PaperBroker:
    return PaperBroker(taker_fee=Decimal("0.0026"), slippage_bps=5)


def test_entry_pays_the_market_plus_slippage_and_fees():
    fill = broker().entry(decision("1"), Decimal("100"))
    assert fill.price == Decimal("100.05")
    assert fill.notional_quote == Decimal("100.05")
    assert fill.fee_quote == Decimal("0.2601300")
    assert fill.slippage_quote == Decimal("0.050")


def test_exit_receives_the_market_minus_slippage_and_fees():
    fill = broker().exit(Decimal("1"), Decimal("90"))
    assert fill.price == Decimal("89.955")
    assert fill.fee_quote == Decimal("0.2338830")
    assert fill.slippage_quote == Decimal("0.045")


def test_entry_refuses_an_unapproved_decision():
    with pytest.raises(ValueError):
        broker().entry(decision(approved=False), Decimal("100"))


def test_stop_triggers_at_the_stop_price():
    trigger = PaperBroker.exit_trigger(Decimal("96"), Decimal("108"), [0, "100", "101", "95"])
    assert trigger == (Decimal("96"), "STOP_LOSS")


def test_gap_below_the_stop_fills_at_the_open():
    # Ouverture sous le stop : on ne peut pas sortir à 96, on sort à 93.
    trigger = PaperBroker.exit_trigger(Decimal("96"), Decimal("108"), [0, "93", "94", "92"])
    assert trigger == (Decimal("93"), "STOP_LOSS")


def test_take_profit_triggers_at_the_level():
    trigger = PaperBroker.exit_trigger(Decimal("96"), Decimal("108"), [0, "100", "109", "99"])
    assert trigger == (Decimal("108"), "TAKE_PROFIT")


def test_gap_above_the_take_profit_fills_at_the_open():
    trigger = PaperBroker.exit_trigger(Decimal("96"), Decimal("108"), [0, "110", "111", "109"])
    assert trigger == (Decimal("110"), "TAKE_PROFIT")


def test_when_both_levels_are_touched_the_stop_wins():
    # Sans données tick, on ne peut pas savoir l'ordre. On retient la perte :
    # se tromper dans l'autre sens embellirait le backtest.
    trigger = PaperBroker.exit_trigger(Decimal("96"), Decimal("108"), [0, "100", "109", "95"])
    assert trigger == (Decimal("96"), "STOP_LOSS")


def test_no_trigger_inside_the_range():
    assert PaperBroker.exit_trigger(Decimal("96"), Decimal("108"), [0, "100", "102", "98"]) is None
