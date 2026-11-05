"""Tests de la règle d'entrée déterministe — pure, sans réseau ni base."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.engine.entry_rule import evaluate_entry
from app.engine.risk_gate import EntryMode, MarketSnapshot, RiskConfig
from app.market.feed import D1Context, MarketContext

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
CONFIG = RiskConfig()
H1_CONFIG = RiskConfig(entry_mode=EntryMode.PULLBACK_H1)


def snapshot(price: str = "100", ema200: str = "90", atr: str = "1",
             h1_return: str = "0", slope: str = "10") -> MarketSnapshot:
    return MarketSnapshot(
        symbol="BTC/EUR", price=Decimal(price), ema_200_h4=Decimal(ema200),
        atr_14_h4=Decimal(atr), last_closed_h1_return_atr=Decimal(h1_return),
        tick_at=NOW, now=NOW, ema_200_slope_atr=Decimal(slope),
    )


def context(ema50: str = "99.5", rsi: str = "55", *,
            ema50_h1: str = "0", rsi_h1: str = "0", atr_h1: str = "0",
            low: str = "0") -> MarketContext:
    return MarketContext(
        ema_50_h4=Decimal(ema50), rsi_14_h4=Decimal(rsi),
        macd_histogram=Decimal("0.5"), order_book_imbalance=Decimal("0"),
        candles_closed=320,
        ema_50_h1=Decimal(ema50_h1), rsi_14_h1=Decimal(rsi_h1),
        atr_14_h1=Decimal(atr_h1), last_closed_h1_low=Decimal(low),
    )


def h1_context(ema50_h1: str = "99.6", rsi_h1: str = "42",
               atr_h1: str = "0.5", low: str = "99.0",
               ema50_h4: str = "99.5", rsi_h4: str = "55") -> MarketContext:
    """Contexte d'un repli H1 valide sur l'EMA50 H1 (100 touche 99,6 et referme)."""
    return context(ema50=ema50_h4, rsi=rsi_h4, ema50_h1=ema50_h1,
                   rsi_h1=rsi_h1, atr_h1=atr_h1, low=low)


def test_pullback_in_an_uptrend_triggers():
    setup = evaluate_entry(snapshot(), context(), CONFIG)
    assert setup.triggered is True
    assert setup.setup == "PULLBACK"
    assert "pullback EMA50" in setup.reason


def test_price_below_ema200_is_refused():
    setup = evaluate_entry(snapshot(price="80", ema200="90"), context(ema50="79.5"), CONFIG)
    assert setup.triggered is False
    assert "EMA200" in setup.reason


def test_ema50_below_ema200_is_refused():
    setup = evaluate_entry(snapshot(), context(ema50="89"), CONFIG)
    assert setup.triggered is False
    assert "structure baissière" in setup.reason


def test_overbought_rsi_is_refused():
    setup = evaluate_entry(snapshot(), context(rsi="75"), CONFIG)
    assert setup.triggered is False
    assert "surchauffe" in setup.reason


def test_weak_momentum_is_refused():
    setup = evaluate_entry(snapshot(), context(rsi="30"), CONFIG)
    assert setup.triggered is False
    assert "momentum" in setup.reason


def test_dead_market_is_refused():
    setup = evaluate_entry(snapshot(atr="0.01"), context(), CONFIG)
    assert setup.triggered is False
    assert "atone" in setup.reason


def test_abnormal_volatility_is_refused():
    setup = evaluate_entry(snapshot(atr="9"), context(), CONFIG)
    assert setup.triggered is False
    assert "volatilité anormale" in setup.reason


def test_falling_knife_is_refused():
    setup = evaluate_entry(snapshot(h1_return="-3"), context(), CONFIG)
    assert setup.triggered is False
    assert "couteau" in setup.reason


def test_price_too_far_above_ema50_is_refused():
    setup = evaluate_entry(snapshot(), context(ema50="95"), CONFIG)
    assert setup.triggered is False
    assert "au-dessus de l'EMA50" in setup.reason


def test_price_too_far_below_ema50_is_refused():
    setup = evaluate_entry(snapshot(), context(ema50="104"), CONFIG)
    assert setup.triggered is False
    assert "sous l'EMA50" in setup.reason


def test_non_finite_price_is_refused():
    from dataclasses import replace

    setup = evaluate_entry(replace(snapshot(), price=Decimal("NaN")), context(), CONFIG)
    assert setup.triggered is False
    assert "non finies" in setup.reason


def test_ema50_requirement_can_be_disabled():
    config = RiskConfig(entry_require_ema50_above_ema200=False)
    setup = evaluate_entry(snapshot(ema200="99.5"), context(ema50="99.4"), config)
    assert setup.triggered is True


# ---------------------------------------------------------------------------
# SWING_MACRO_D1 (D-060)
# ---------------------------------------------------------------------------

SWING_CONFIG = RiskConfig(entry_mode=EntryMode.SWING_MACRO_D1)


def d1(**kw) -> D1Context:
    base = dict(
        ema_20=Decimal("98"), ema_50=Decimal("95"), ema_200=Decimal("90"),
        ema_20_prev=Decimal("97"), ema_50_slope_5d_pct=Decimal("0.02"),
        rsi_14=Decimal("55"), atr_14=Decimal("2"),
        donchian_high_20=Decimal("99"), bollinger_upper=Decimal("105"),
        bollinger_mid=Decimal("98"), swing_low_10=Decimal("92"),
        last_open=Decimal("99"), last_close=Decimal("100"),
        last_low=Decimal("98"), last_high=Decimal("101"),
        prev_close=Decimal("98"), prev_low=Decimal("97"),
        bearish_divergence=False,
    )
    base.update(kw)
    return D1Context(**base)


def swing_context(**kw) -> MarketContext:
    return MarketContext(
        ema_50_h4=Decimal("95"), rsi_14_h4=Decimal("55"),
        macd_histogram=Decimal("0"), order_book_imbalance=Decimal("0"),
        candles_closed=320, d1=d1(**kw),
    )


def test_swing_breakout_triggers():
    setup = evaluate_entry(snapshot(), swing_context(), SWING_CONFIG)
    assert setup.triggered is True
    assert setup.setup == "SWING_MACRO_D1"
    assert "Donchian" in setup.reason


def test_swing_value_rebound_triggers_when_no_breakout():
    setup = evaluate_entry(
        snapshot(), swing_context(donchian_high_20=Decimal("110")), SWING_CONFIG
    )
    assert setup.triggered is True
    assert "rebond EMA20" in setup.reason


def test_swing_requires_close_above_ema200():
    setup = evaluate_entry(snapshot(price="89", ema200="90"), swing_context(), SWING_CONFIG)
    assert setup.triggered is False
    assert "EMA200" in setup.reason


def test_swing_requires_positive_ema50_slope():
    setup = evaluate_entry(
        snapshot(), swing_context(ema_50_slope_5d_pct=Decimal("-0.01")), SWING_CONFIG
    )
    assert setup.triggered is False
    assert "pente EMA50 D1" in setup.reason


def test_swing_refuses_when_no_trigger():
    setup = evaluate_entry(
        snapshot(), swing_context(
            donchian_high_20=Decimal("110"),
            prev_low=Decimal("99"),  # n'a pas testé l'EMA20
        ), SWING_CONFIG,
    )
    assert setup.triggered is False
    assert "aucun déclencheur" in setup.reason


def test_swing_refuses_without_d1_context():
    setup = evaluate_entry(snapshot(), context(), SWING_CONFIG)
    assert setup.triggered is False
    assert "contexte D1 indisponible" in setup.reason


def test_swing_trigger_flags_can_isolate_donchian():
    config = RiskConfig(
        entry_mode=EntryMode.SWING_MACRO_D1,
        swing_require_value_rebound=False,
    )
    setup = evaluate_entry(snapshot(), swing_context(), config)
    assert setup.triggered is True
    assert "Donchian" in setup.reason

    no_breakout = evaluate_entry(
        snapshot(), swing_context(donchian_high_20=Decimal("110")), config
    )
    assert no_breakout.triggered is False


def test_negative_last_h1_is_refused_when_the_filter_is_enabled():
    config = RiskConfig(entry_require_positive_h1=True)
    setup = evaluate_entry(snapshot(h1_return="-0.2"), context(), config)
    assert setup.triggered is False
    assert "rebond confirmé" in setup.reason


def test_negative_last_h1_is_allowed_by_default():
    setup = evaluate_entry(snapshot(h1_return="-0.2"), context(), CONFIG)
    assert setup.triggered is True


def test_a_flat_ema200_is_not_an_uptrend():
    config = RiskConfig(entry_ema200_slope_min=Decimal("2"))
    setup = evaluate_entry(snapshot(slope="0.5"), context(), config)
    assert setup.triggered is False
    assert "tendance pas assez haussière" in setup.reason


def test_a_rising_ema200_passes_the_trend_gate():
    config = RiskConfig(entry_ema200_slope_min=Decimal("2"))
    setup = evaluate_entry(snapshot(slope="3.2"), context(), config)
    assert setup.triggered is True


# ---------------------------------------------------------------------------
# Mode PULLBACK_H1 — repli fin sur l'EMA50 H1 (D-058)
# ---------------------------------------------------------------------------


def test_h1_pullback_that_touches_and_reclaims_triggers():
    setup = evaluate_entry(snapshot(), h1_context(), H1_CONFIG)
    assert setup.triggered is True
    assert setup.setup == "PULLBACK_H1"
    assert "pullback H1" in setup.reason
    assert setup.rsi == Decimal("42")


def test_h1_mode_still_requires_price_above_ema200():
    setup = evaluate_entry(snapshot(price="80", ema200="90"), h1_context(), H1_CONFIG)
    assert setup.triggered is False
    assert "EMA200" in setup.reason


def test_h1_mode_still_refuses_a_falling_knife():
    setup = evaluate_entry(snapshot(h1_return="-3"), h1_context(), H1_CONFIG)
    assert setup.triggered is False
    assert "couteau" in setup.reason


def test_h1_mode_does_not_require_ema50_h4_above_ema200():
    """Le régime est porté par le prix au-dessus de l'EMA200 et la pente."""
    setup = evaluate_entry(snapshot(), h1_context(ema50_h4="89"), H1_CONFIG)
    assert setup.triggered is True


def test_h1_that_never_touched_the_ema50_is_refused():
    setup = evaluate_entry(snapshot(), h1_context(low="99.9"), H1_CONFIG)
    assert setup.triggered is False
    assert "n'a pas testé" in setup.reason


def test_h1_that_closed_below_the_ema50_is_refused():
    setup = evaluate_entry(snapshot(price="99"), h1_context(), H1_CONFIG)
    assert setup.triggered is False
    assert "repli non refermé" in setup.reason


def test_h1_reclaim_too_far_above_the_ema50_is_refused():
    setup = evaluate_entry(snapshot(), h1_context(ema50_h1="99", atr_h1="0.5"), H1_CONFIG)
    assert setup.triggered is False
    assert "après le repli" in setup.reason


def test_h1_rsi_below_the_band_is_refused():
    setup = evaluate_entry(snapshot(), h1_context(rsi_h1="30"), H1_CONFIG)
    assert setup.triggered is False
    assert "momentum trop faible" in setup.reason


def test_h1_rsi_above_the_band_is_not_a_pullback():
    setup = evaluate_entry(snapshot(), h1_context(rsi_h1="60"), H1_CONFIG)
    assert setup.triggered is False
    assert "remontée déjà étendue" in setup.reason


def test_h1_mode_refuses_when_the_h1_context_is_missing():
    setup = evaluate_entry(snapshot(), context(), H1_CONFIG)
    assert setup.triggered is False
    assert "indisponibles" in setup.reason


def test_unknown_entry_mode_is_refused_at_construction():
    with pytest.raises(ValueError, match="entry_mode"):
        RiskConfig(entry_mode="MOMENTUM")


def test_incoherent_h1_rsi_bounds_are_refused_at_construction():
    with pytest.raises(ValueError, match="RSI H1"):
        RiskConfig(entry_h1_rsi_min=Decimal("60"), entry_h1_rsi_max=Decimal("40"))
