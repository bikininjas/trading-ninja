"""Tests du Chandelier Exit : niveau, cliquet et sortie à la clôture.

Le Chandelier est une règle pure : aucun ordre, aucune horloge. Ces tests
vérifient que le stop ne descend jamais, qu'une mèche ne sort pas la position et
qu'un ATR absurde est refusé plutôt que transformé en stop nul.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.engine.exit_rule import (
    chandelier_stop,
    should_exit_chandelier,
    update_chandelier_stop,
)


def test_chandelier_stop_is_high_minus_multiple_atr():
    assert chandelier_stop(Decimal("120"), Decimal("5"), Decimal("3")) == Decimal("105")


def test_chandelier_stop_accepts_a_custom_multiple():
    assert chandelier_stop(Decimal("120"), Decimal("5"), Decimal("2")) == Decimal("110")


def test_chandelier_stop_refuses_a_non_positive_level():
    with pytest.raises(ValueError, match="non positif"):
        chandelier_stop(Decimal("10"), Decimal("5"), Decimal("3"))


@pytest.mark.parametrize("high,atr,multiple", [
    (Decimal("0"), Decimal("5"), Decimal("3")),
    (Decimal("120"), Decimal("-1"), Decimal("3")),
    (Decimal("120"), Decimal("5"), Decimal("0")),
    (Decimal("NaN"), Decimal("5"), Decimal("3")),
])
def test_chandelier_stop_refuses_invalid_inputs(high, atr, multiple):
    with pytest.raises(ValueError):
        chandelier_stop(high, atr, multiple)


def test_the_trailing_stop_only_ratchets_up():
    first = update_chandelier_stop(None, Decimal("120"), Decimal("5"), Decimal("3"))
    assert first == Decimal("105")
    # Plus haut identique, volatilité plus forte : le niveau brut baisse, le
    # stop conservé ne doit pas suivre.
    second = update_chandelier_stop(first, Decimal("120"), Decimal("8"), Decimal("3"))
    assert second == first
    # Nouveau plus haut : le stop monte.
    third = update_chandelier_stop(second, Decimal("140"), Decimal("5"), Decimal("3"))
    assert third == Decimal("125")


def test_the_initial_structural_stop_is_never_loosened():
    # Le stop structurel (110) est plus haut que le Chandelier brut (105).
    assert update_chandelier_stop(
        Decimal("110"), Decimal("120"), Decimal("5"), Decimal("3")
    ) == Decimal("110")


def test_exit_only_on_a_close_strictly_below_the_level():
    assert should_exit_chandelier(Decimal("104.99"), Decimal("105")) is True
    assert should_exit_chandelier(Decimal("105"), Decimal("105")) is False
    assert should_exit_chandelier(Decimal("105.01"), Decimal("105")) is False
