"""Tests du Profit Sweeper : calcul pur, seuil, absence de ligne sur perte."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.engine.sweep import plan_sweep


def test_a_loss_produces_no_sweep_row():
    plan = plan_sweep(Decimal("-0.42"), Decimal("0.30"), Decimal("1"))
    assert plan.applies is False
    assert plan.status is None


def test_a_break_even_trade_produces_no_sweep_row():
    assert plan_sweep(Decimal("0"), Decimal("0.30"), Decimal("1")).applies is False


def test_a_gain_above_the_minimum_is_swept():
    plan = plan_sweep(Decimal("10"), Decimal("0.30"), Decimal("1"))
    assert plan.applies is True
    assert plan.swept_quote == Decimal("3.00000000")
    assert plan.status == "SIMULATED"


def test_a_gain_below_the_minimum_is_recorded_as_skipped():
    plan = plan_sweep(Decimal("0.10"), Decimal("0.30"), Decimal("1"))
    assert plan.applies is True
    assert plan.computed_quote == Decimal("0.03000000")
    assert plan.swept_quote == 0
    assert plan.status == "SKIPPED_BELOW_MINIMUM"
    assert "minimum" in plan.reason


def test_a_sweep_exactly_at_the_minimum_is_executed():
    plan = plan_sweep(Decimal("1"), Decimal("0.30"), Decimal("0.30"))
    assert plan.status == "SIMULATED"
    assert plan.swept_quote == Decimal("0.30000000")


def test_live_plan_is_pending_not_simulated():
    plan = plan_sweep(Decimal("10"), Decimal("0.30"), Decimal("1"), simulated=False)
    assert plan.status == "PENDING"


@pytest.mark.parametrize("ratio", ["0", "1", "-0.2", "1.5"])
def test_an_invalid_ratio_is_refused(ratio):
    with pytest.raises(ValueError):
        plan_sweep(Decimal("10"), Decimal(ratio), Decimal("1"))


def test_deferred_amounts_accumulate_until_the_threshold():
    plan = plan_sweep(Decimal("0.20"), Decimal("0.30"), Decimal("0.10"),
                      deferred_quote=Decimal("0.06"))
    assert plan.status == "SIMULATED"
    assert plan.swept_quote == Decimal("0.12000000")


def test_a_loss_ignores_deferred_amounts():
    plan = plan_sweep(Decimal("-0.10"), Decimal("0.30"), Decimal("0.10"),
                      deferred_quote=Decimal("5"))
    assert plan.applies is False


def test_a_negative_deferred_amount_is_refused():
    with pytest.raises(ValueError):
        plan_sweep(Decimal("1"), Decimal("0.30"), Decimal("0.10"),
                   deferred_quote=Decimal("-1"))
