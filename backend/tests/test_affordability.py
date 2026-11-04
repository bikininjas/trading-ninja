"""Tests de la faisabilité d'un ordre avec un petit capital.

Les chiffres viennent du marché réel (Kraken, 2026-11-04) : `SHIB/EUR` est la paire
EUR dont le minimum d'ordre est le plus bas (~3,20 EUR), avec un ATR H4 d'environ
1,5 % — soit un stop à 3,75 %. C'est le meilleur cas possible, et il reste
inatteignable avec 9,37 EUR au profil prudent.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.engine.affordability import assess
from app.engine.risk_gate import (
    AccountState,
    Action,
    ExchangeConstraints,
    MarketRegime,
    MarketSnapshot,
    RejectionCode,
    RiskConfig,
    TradeIntent,
    evaluate,
)

EQUITY = Decimal("9.37")
PRICE = Decimal("0.00000415")
ATR = PRICE * Decimal("0.015")  # 1,5 % du prix

CONSTRAINTS = ExchangeConstraints(
    amount_step=Decimal("1"),
    min_amount=Decimal("770000"),
    min_notional=Decimal("0.45"),
    price_tick=Decimal("0.00000001"),
)


def config(risk: str = "0.005", notional: str = "0.20") -> RiskConfig:
    return RiskConfig(risk_per_trade_pct=Decimal(risk), max_notional_pct=Decimal(notional))


def test_prudent_profile_cannot_buy_the_cheapest_pair():
    verdict = assess(
        "SHIB/EUR",
        price=PRICE,
        atr_14=ATR,
        constraints=CONSTRAINTS,
        config=config(),
        equity=EQUITY,
    )
    # 770 000 × 0,00000415 = 3,20 EUR ; à 0,5 % de risque il faudrait ~24 EUR.
    assert verdict.min_order_quote == Decimal("3.19550000")
    assert verdict.stop_distance_pct == Decimal("0.0375")
    assert verdict.required_equity == Decimal("23.966250000")
    assert verdict.binding == "risk"
    assert verdict.affordable is False
    assert verdict.shortfall == Decimal("14.596250000")
    # Le plus petit ordre risquerait ~1,28 % de l'équité, pas 0,5 %.
    assert verdict.effective_risk_pct_at_minimum.quantize(Decimal("0.000001")) == Decimal("0.012789")


def test_raising_risk_alone_is_not_enough_when_the_exposure_cap_binds():
    # 1,75 % de risque : le budget suffirait, mais 20 % de 9,37 EUR = 1,87 EUR,
    # toujours sous le minimum d'ordre de 3,20 EUR.
    verdict = assess(
        "SHIB/EUR",
        price=PRICE,
        atr_14=ATR,
        constraints=CONSTRAINTS,
        config=config("0.0175", "0.20"),
        equity=EQUITY,
    )
    assert verdict.binding == "exposure"
    assert verdict.required_equity == Decimal("15.9775")
    assert verdict.affordable is False


def test_micro_profile_both_knobs_together():
    # 1,75 % de risque ET 50 % d'exposition : le minimum d'ordre tient enfin.
    verdict = assess(
        "SHIB/EUR",
        price=PRICE,
        atr_14=ATR,
        constraints=CONSTRAINTS,
        config=config("0.0175", "0.50"),
        equity=EQUITY,
    )
    assert verdict.binding == "risk"
    assert verdict.required_equity == Decimal("6.847500000")
    assert verdict.affordable is True
    assert verdict.shortfall == Decimal("0")


def test_absolute_cap_blocks_regardless_of_equity():
    verdict = assess(
        "SHIB/EUR",
        price=PRICE,
        atr_14=ATR,
        constraints=CONSTRAINTS,
        config=RiskConfig(max_position_notional_quote=Decimal("2")),
        equity=Decimal("100000"),
    )
    assert verdict.binding == "absolute_cap"
    assert verdict.required_equity is None
    assert verdict.affordable is False


def test_non_decimal_and_non_finite_inputs_are_refused():
    with pytest.raises(TypeError):
        assess("SHIB/EUR", price=0.00000415, atr_14=ATR,
               constraints=CONSTRAINTS, config=config(), equity=EQUITY)
    with pytest.raises(ValueError):
        assess("SHIB/EUR", price=Decimal("NaN"), atr_14=ATR,
               constraints=CONSTRAINTS, config=config(), equity=EQUITY)
    with pytest.raises(ValueError):
        assess("SHIB/EUR", price=PRICE, atr_14=Decimal("0"),
               constraints=CONSTRAINTS, config=config(), equity=EQUITY)


def test_the_prediction_matches_the_gate_actual_decision():
    """Le préflight doit prédire le refus réel du pare-feu, pas un refus théorique."""
    now = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
    market = MarketSnapshot(
        symbol="SHIB/EUR",
        price=PRICE,
        ema_200_h4=PRICE * Decimal("0.90"),
        atr_14_h4=ATR,
        last_closed_h1_return_atr=Decimal("0"),
        tick_at=now,
        now=now,
    )
    account = AccountState(equity_quote=EQUITY, free_quote=EQUITY)
    decision = evaluate(
        TradeIntent(Action.BUY, 0.95, MarketRegime.TRENDING_UP),
        market,
        account,
        CONSTRAINTS,
        config(),
    )
    verdict = assess(
        "SHIB/EUR", price=PRICE, atr_14=ATR,
        constraints=CONSTRAINTS, config=config(), equity=EQUITY,
    )
    assert decision.approved is False
    assert decision.rejection_code is RejectionCode.SIZING_BELOW_EXCHANGE_MINIMUM
    assert verdict.affordable is False
