"""Tests du pare-feu transactionnel.

Le Risk Gateway est le seul composant autorisé à dire « oui ». On teste donc
prioritairement qu'il dit « non » : chaque garde-fou a au moins un test dédié,
et un test de balayage vérifie qu'aucune combinaison d'entrées ne peut produire
un ordre qui dépasse le budget de risque.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.engine.risk_gate import (
    AccountState,
    Action,
    ExchangeConstraints,
    MarketRegime,
    MarketSnapshot,
    RejectionCode,
    RiskConfig,
    StopMode,
    SwingExitMode,
    TradeIntent,
    evaluate,
)

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)

CONSTRAINTS = ExchangeConstraints(
    amount_step=Decimal("0.001"),
    min_amount=Decimal("0.001"),
    min_notional=Decimal("10"),
    price_tick=Decimal("0.01"),
)


def market(price="100", ema="90", atr="2", h1="-0.2", age_s=0.0) -> MarketSnapshot:
    return MarketSnapshot(
        symbol="BTC/USDT",
        price=Decimal(price),
        ema_200_h4=Decimal(ema),
        atr_14_h4=Decimal(atr),
        last_closed_h1_return_atr=Decimal(h1),
        tick_at=NOW - timedelta(seconds=age_s),
        now=NOW,
    )


def account(**kw) -> AccountState:
    base = dict(equity_quote=Decimal("10000"), free_quote=Decimal("10000"))
    base.update(kw)
    return AccountState(**base)


def intent(action=Action.BUY, confidence=0.9, regime=MarketRegime.TRENDING_UP) -> TradeIntent:
    return TradeIntent(action=action, confidence=confidence, market_regime=regime, thesis="t")


def run(m=None, a=None, i=None, c=CONSTRAINTS, cfg=RiskConfig()):
    return evaluate(i or intent(), m or market(), a or account(), c, cfg)


# ---------------------------------------------------------------------------
# Chemin nominal
# ---------------------------------------------------------------------------


def test_happy_path_sizing_is_exact():
    d = run()
    assert d.approved, d.rejection_details
    # stop = 100 - 2.5*2 = 95 ; risque = 0.5% * 10000 = 50 ; qty = 50/5 = 10
    assert d.stop_loss_price == Decimal("95.00")
    assert d.stop_distance_quote == Decimal("5.00")
    assert d.amount == Decimal("10.000")
    assert d.notional_quote == Decimal("1000.0000")
    assert d.intended_risk_quote == Decimal("50.0000")
    assert d.effective_risk_quote == Decimal("50.00000")
    assert d.effective_risk_pct == Decimal("0.005")
    assert d.take_profit_price == Decimal("110.00")  # 2R
    assert d.rejection_code is RejectionCode.NONE


def test_determinism():
    assert run() == run()


def test_full_check_chain_reported():
    d = run()
    assert d.checks_passed[:3] == ("input_integrity", "data_freshness", "action_is_buy")
    assert d.checks_passed[-1] == "risk_budget_enforced"


# ---------------------------------------------------------------------------
# Protocole : le LLM ne peut rien forcer
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("action", [Action.SELL, Action.HOLD])
def test_only_buy_is_actionable(action):
    d = run(i=intent(action=action, confidence=1.0))
    assert not d.approved
    assert d.rejection_code is RejectionCode.ACTION_IS_NOT_BUY


def test_garbage_action_rejected():
    d = run(i=TradeIntent(action="MOON", confidence=0.99))  # type: ignore[arg-type]
    assert not d.approved
    assert d.rejection_code is RejectionCode.MALFORMED_INTENT


def test_confidence_out_of_bounds_rejected():
    d = run(i=TradeIntent(action=Action.BUY, confidence=1.5))
    assert not d.approved
    assert d.rejection_code is RejectionCode.MALFORMED_INTENT


def test_low_confidence_rejected():
    d = run(i=intent(confidence=0.79))
    assert not d.approved
    assert d.rejection_code is RejectionCode.CONFIDENCE_TOO_LOW


def test_llm_bearish_regime_blocks_entry():
    d = run(i=intent(regime=MarketRegime.TRENDING_DOWN))
    assert not d.approved
    assert d.rejection_code is RejectionCode.REJECTED_REGIME_CONFLICT


def test_string_enums_accepted_from_json():
    d = run(i=TradeIntent(action="BUY", confidence=0.9, market_regime="TRENDING_UP"))  # type: ignore[arg-type]
    assert d.approved


# ---------------------------------------------------------------------------
# Données de marché
# ---------------------------------------------------------------------------


def test_stale_data_rejected():
    d = run(m=market(age_s=6))
    assert d.rejection_code is RejectionCode.STALE_MARKET_DATA


def test_future_timestamp_rejected():
    d = run(m=market(age_s=-30))
    assert d.rejection_code is RejectionCode.STALE_MARKET_DATA


@pytest.mark.parametrize("price,ema,atr", [("0", "90", "2"), ("100", "0", "2"), ("100", "90", "0")])
def test_invalid_market_data_rejected(price, ema, atr):
    d = run(m=market(price=price, ema=ema, atr=atr))
    assert d.rejection_code is RejectionCode.INVALID_MARKET_DATA


def test_zero_equity_rejected():
    d = run(a=AccountState(equity_quote=Decimal("0"), free_quote=Decimal("0")))
    assert d.rejection_code is RejectionCode.INVALID_MARKET_DATA


# ---------------------------------------------------------------------------
# Coupe-circuits
# ---------------------------------------------------------------------------


def test_locked_bot_rejected():
    d = run(a=account(locked=True, lock_reason=RejectionCode.CIRCUIT_BREAKER_DAILY_DRAWDOWN))
    assert d.rejection_code is RejectionCode.CIRCUIT_BREAKER_DAILY_DRAWDOWN


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("daily_drawdown_pct", Decimal("0.02"), RejectionCode.CIRCUIT_BREAKER_DAILY_DRAWDOWN),
        ("weekly_drawdown_pct", Decimal("0.05"), RejectionCode.CIRCUIT_BREAKER_WEEKLY_DRAWDOWN),
        ("drawdown_from_hwm_pct", Decimal("0.10"), RejectionCode.CIRCUIT_BREAKER_MAX_DRAWDOWN),
        ("consecutive_losses", 4, RejectionCode.CIRCUIT_BREAKER_CONSECUTIVE_LOSSES),
    ],
)
def test_circuit_breakers(field, value, code):
    d = run(a=account(**{field: value}))
    assert not d.approved
    assert d.rejection_code is code


def test_circuit_breakers_below_threshold_pass():
    assert run(a=account(daily_drawdown_pct=Decimal("0.019"), weekly_drawdown_pct=Decimal("0.049"))).approved


def test_order_rate_limit():
    d = run(a=account(orders_last_hour=6))
    assert d.rejection_code is RejectionCode.MAX_ORDERS_PER_HOUR_EXCEEDED


# ---------------------------------------------------------------------------
# Contexte de marché
# ---------------------------------------------------------------------------


def test_counter_trend_rejected_at_ema():
    # prix exactement sur l'EMA200 => refus (il faut être au-dessus avec marge)
    d = run(m=market(price="95", ema="95"))
    assert d.rejection_code is RejectionCode.REJECTED_COUNTER_TREND_EMA200


def test_trend_buffer_matters():
    # 90 * 1.005 = 90.45 ; 90.44 est refusé, 90.46 passe
    assert run(m=market(price="90.44", ema="90", atr="0.5")).rejection_code is RejectionCode.REJECTED_COUNTER_TREND_EMA200
    assert run(m=market(price="90.46", ema="90", atr="0.5")).approved


def test_falling_knife_rejected():
    d = run(m=market(h1="-2.6"))
    assert d.rejection_code is RejectionCode.REJECTED_FALLING_KNIFE


def test_falling_knife_threshold_is_inclusive():
    # Choix assumé : au seuil exact on s'abstient (fail-closed), on n'arrondit
    # jamais en faveur du trade.
    assert run(m=market(h1="-2.5")).rejection_code is RejectionCode.REJECTED_FALLING_KNIFE
    assert run(m=market(h1="-2.49")).approved


def test_dead_market_rejected():
    d = run(m=market(atr="0.1"))  # ATR = 0.1 % du prix
    assert d.rejection_code is RejectionCode.REJECTED_LOW_VOLATILITY


def test_abnormal_volatility_rejected():
    d = run(m=market(atr="9"))  # ATR = 9 % du prix
    assert d.rejection_code is RejectionCode.REJECTED_EXTREME_VOLATILITY


# ---------------------------------------------------------------------------
# Monoposition
# ---------------------------------------------------------------------------


def test_position_already_open():
    d = run(a=account(open_position_symbols=("BTC/USDT",)))
    assert d.rejection_code is RejectionCode.POSITION_ALREADY_OPEN


def test_other_symbol_fills_max_positions():
    d = run(a=account(open_position_symbols=("ETH/USDT",)))
    assert d.rejection_code is RejectionCode.POSITION_ALREADY_OPEN


# ---------------------------------------------------------------------------
# Dimensionnement : les plafonds mordent dans le bon ordre
# ---------------------------------------------------------------------------


def test_notional_cap_clamps_size_and_lowers_effective_risk():
    # ATR faible => stop serré => la formule risque voudrait 40 unités (4000 $),
    # le plafond d'exposition (20 % de 10000 = 2000 $) ramène à 20 unités.
    d = run(m=market(atr="0.5"))
    assert d.approved
    assert d.amount == Decimal("20.000")
    assert d.notional_quote == Decimal("2000.0000")
    assert d.effective_risk_pct == Decimal("0.0025")
    assert d.effective_risk_pct < d.intended_risk_quote / Decimal("10000")


def test_cash_cap_clamps_size():
    d = run(a=account(free_quote=Decimal("300")))
    assert d.approved
    assert d.amount == Decimal("2.994")  # 300 * 0.998 / 100, tronqué au pas
    assert d.notional_quote == Decimal("299.4000")


def test_absolute_notional_cap():
    cfg = RiskConfig(max_position_notional_quote=Decimal("50"))
    d = run(a=account(equity_quote=Decimal("100000"), free_quote=Decimal("100000")), cfg=cfg)
    assert d.approved
    assert d.notional_quote <= Decimal("50")


def test_min_notional_rejected():
    d = run(a=account(free_quote=Decimal("5")))
    assert d.rejection_code is RejectionCode.EXCHANGE_MIN_NOTIONAL_FAILED


def test_min_amount_rejected():
    c = ExchangeConstraints(
        amount_step=Decimal("0.001"), min_amount=Decimal("5"),
        min_notional=Decimal("10"), price_tick=Decimal("0.01"),
    )
    d = run(a=account(free_quote=Decimal("300")), c=c)
    assert d.rejection_code is RejectionCode.SIZING_BELOW_EXCHANGE_MINIMUM


def test_zero_free_balance_rejected():
    d = run(a=account(free_quote=Decimal("0")))
    assert d.rejection_code is RejectionCode.INSUFFICIENT_FREE_BALANCE


def test_loss_streak_halves_risk():
    d = run(a=account(consecutive_losses=2))
    assert d.approved
    assert d.intended_risk_quote == Decimal("25.0000")  # 0.25 %
    assert d.amount == Decimal("5.000")


def test_stop_is_truncated_down_never_up():
    c = ExchangeConstraints(
        amount_step=Decimal("0.001"), min_amount=Decimal("0.001"),
        min_notional=Decimal("1"), price_tick=Decimal("1"),
    )
    d = run(m=market(atr="2.333"), c=c)
    assert d.stop_loss_price == Decimal("94")  # 100 - 5.8325 = 94.1675 -> tronqué à 94


# ---------------------------------------------------------------------------
# Invariant global : impossible de dépasser le budget, quel que soit l'état
# ---------------------------------------------------------------------------


def test_a_single_buy_cannot_engage_more_than_half_the_capital():
    with pytest.raises(ValueError, match="pas de all-in"):
        RiskConfig(max_notional_pct=Decimal("0.51"))


@pytest.mark.parametrize("atr", ["0.2", "0.5", "1", "2", "4", "6"])
@pytest.mark.parametrize("equity", ["500", "5000", "10000", "250000"])
@pytest.mark.parametrize("cash_ratio", ["0.05", "0.5", "1.0"])
def test_risk_budget_never_exceeded(atr, equity, cash_ratio):
    eq = Decimal(equity)
    cfg = RiskConfig()
    a = AccountState(equity_quote=eq, free_quote=eq * Decimal(cash_ratio))
    d = evaluate(intent(), market(atr=atr), a, CONSTRAINTS, cfg)

    if not d.approved:
        assert d.rejection_code is not RejectionCode.NONE
        assert d.amount is None
        return

    budget = eq * cfg.risk_per_trade_pct
    assert d.effective_risk_quote is not None and d.effective_risk_quote <= budget
    assert d.effective_risk_pct is not None and d.effective_risk_pct <= cfg.risk_per_trade_pct
    assert d.stop_loss_price is not None and d.stop_loss_price < Decimal("100")
    assert d.notional_quote is not None and d.notional_quote <= eq * cfg.max_notional_pct
    assert d.notional_quote <= a.free_quote
    assert d.notional_quote >= CONSTRAINTS.min_notional


# ---------------------------------------------------------------------------
# La configuration elle-même refuse les réglages dangereux
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"risk_per_trade_pct": Decimal("0")},
        {"risk_per_trade_pct": Decimal("1.5")},
        {"risk_per_trade_pct": Decimal("0.9")},
        {"risk_per_trade_pct": Decimal("0.051")},
        {"max_notional_pct": Decimal("0")},
        {"max_notional_pct": Decimal("2")},
        {"stop_atr_multiple": Decimal("0")},
        {"take_profit_r_multiple": Decimal("-1")},
        {"max_daily_drawdown_pct": Decimal("0.5")},
        {"risk_throttle_factor": Decimal("1")},
        {"min_atr_pct_of_price": Decimal("0.2"), "max_atr_pct_of_price": Decimal("0.1")},
    ],
)
def test_dangerous_config_rejected(kwargs):
    with pytest.raises(ValueError):
        RiskConfig(**kwargs)


# ---------------------------------------------------------------------------
# Le pare-feu ne lève jamais : toute anomalie devient un refus explicite
# (régression trouvée par un audit à froid : deux entrées faisaient lever)
# ---------------------------------------------------------------------------


def test_naive_datetime_is_rejected_not_raised():
    m = MarketSnapshot(
        symbol="BTC/USDT",
        price=Decimal("100"),
        ema_200_h4=Decimal("90"),
        atr_14_h4=Decimal("2"),
        last_closed_h1_return_atr=Decimal("0"),
        tick_at=datetime(2026, 11, 4, 12, 0),  # naïf, sans fuseau
        now=datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc),
    )
    d = evaluate(intent(), m, account(), CONSTRAINTS, RiskConfig())
    assert not d.approved
    assert d.rejection_code is RejectionCode.MALFORMED_INTENT
    assert "fuseau" in (d.rejection_details or "")


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_price_is_rejected(bad):
    d = run(m=market(price=bad))
    assert not d.approved
    assert d.rejection_code is RejectionCode.INVALID_MARKET_DATA


def test_non_finite_atr_is_rejected():
    d = run(m=market(atr="NaN"))
    assert d.rejection_code is RejectionCode.INVALID_MARKET_DATA


def test_unexpected_type_becomes_internal_error_not_exception():
    # equity_quote non numérique : l'accès à .is_finite() lève AttributeError,
    # qui doit être absorbée en refus explicite.
    d = run(a=AccountState(equity_quote="pas un nombre", free_quote=Decimal("10")))  # type: ignore[arg-type]
    assert not d.approved
    assert d.rejection_code is RejectionCode.INTERNAL_ERROR


def test_evaluate_never_raises_on_hostile_input():
    hostile = [
        (MarketSnapshot("X", Decimal("100"), Decimal("90"), Decimal("2"),
                        Decimal("0"), datetime.min.replace(tzinfo=timezone.utc),
                        datetime.max.replace(tzinfo=timezone.utc)), account()),
        (market(), AccountState(equity_quote=Decimal("1e400"), free_quote=Decimal("0"))),
    ]
    for m, a in hostile:
        d = evaluate(intent(), m, a, CONSTRAINTS, RiskConfig())
        assert isinstance(d.approved, bool)
        assert d.rejection_code is not RejectionCode.NONE


# ---------------------------------------------------------------------------
# Stop structurel D1 et petit portefeuille (D-060)
# ---------------------------------------------------------------------------


def swing_market(price="100", swing_low="96", **kw) -> MarketSnapshot:
    return MarketSnapshot(
        symbol="BTC/USDT",
        price=Decimal(price),
        ema_200_h4=Decimal("90"),
        atr_14_h4=Decimal("2"),
        last_closed_h1_return_atr=Decimal("-0.2"),
        tick_at=NOW,
        now=NOW,
        swing_low_10d=Decimal(swing_low),
        **kw,
    )


SWING_CONFIG = RiskConfig(stop_mode=StopMode.STRUCTURAL_D1, take_profit_r_multiple=Decimal("0"))


def test_structural_stop_uses_the_d1_swing_low():
    d = run(m=swing_market(), cfg=SWING_CONFIG)
    assert d.approved, d.rejection_details
    assert d.stop_loss_price == Decimal("96.00")
    assert d.stop_distance_quote == Decimal("4.00")


def test_structural_stop_is_capped_at_five_percent():
    d = run(m=swing_market(swing_low="80"), cfg=SWING_CONFIG)
    assert d.approved, d.rejection_details
    assert d.stop_loss_price == Decimal("95.00")
    assert d.stop_distance_quote == Decimal("5.00")


def test_structural_stop_without_swing_low_is_refused():
    d = run(m=swing_market(swing_low="0"), cfg=SWING_CONFIG)
    assert not d.approved
    assert d.rejection_code is RejectionCode.INVALID_STOP_LOSS_CALCULATION


def test_small_account_full_allocation_requires_the_flag():
    with pytest.raises(ValueError, match="max_notional_pct"):
        RiskConfig(max_notional_pct=Decimal("1.0"))

    small = account(equity_quote=Decimal("20"), free_quote=Decimal("20"))
    tight = ExchangeConstraints(
        amount_step=Decimal("0.001"), min_amount=Decimal("0.001"),
        min_notional=Decimal("15"), price_tick=Decimal("0.01"),
    )
    blocked = run(m=swing_market(), a=small, c=tight, cfg=SWING_CONFIG)
    assert not blocked.approved
    assert blocked.rejection_code is RejectionCode.EXCHANGE_MIN_NOTIONAL_FAILED

    full = run(
        m=swing_market(), a=small, c=tight,
        cfg=RiskConfig(
            risk_per_trade_pct=Decimal("0.05"),
            stop_mode=StopMode.STRUCTURAL_D1,
            take_profit_r_multiple=Decimal("0"),
            max_notional_pct=Decimal("1.0"),
            small_account_full_allocation=True,
        ),
    )
    assert full.approved, full.rejection_details
    assert full.notional_quote >= Decimal("19.8")
    assert full.notional_quote <= Decimal("20.0")
    assert full.effective_risk_quote <= Decimal("1.00")


def test_small_account_floor_reaches_the_exchange_minimum():
    small = account(equity_quote=Decimal("20"), free_quote=Decimal("20"))
    tight = ExchangeConstraints(
        amount_step=Decimal("0.001"), min_amount=Decimal("0.001"),
        min_notional=Decimal("15"), price_tick=Decimal("0.01"),
    )
    cfg = RiskConfig(
        risk_per_trade_pct=Decimal("0.0175"),
        stop_mode=StopMode.STRUCTURAL_D1,
        take_profit_r_multiple=Decimal("0"),
        max_notional_pct=Decimal("1.0"),
        small_account_full_allocation=True,
    )
    d = run(m=swing_market(), a=small, c=tight, cfg=cfg)
    assert d.approved, d.rejection_details
    # 15 EUR de notionnel, stop à 4 % → 0,60 EUR risqués, soit 3 % de 20 EUR.
    assert d.notional_quote >= Decimal("14.9")
    assert d.effective_risk_pct == Decimal("0.03")


def test_small_account_floor_refuses_when_above_the_cap():
    tiny = account(equity_quote=Decimal("12"), free_quote=Decimal("12"))
    cfg = RiskConfig(
        risk_per_trade_pct=Decimal("0.0175"),
        stop_mode=StopMode.STRUCTURAL_D1,
        max_notional_pct=Decimal("1.0"),
        small_account_full_allocation=True,
    )
    # 10 EUR de notionnel, stop à 4 % → 0,40 EUR = 3,3 % de 12 EUR : passe.
    d = run(m=swing_market(), a=tiny, cfg=cfg)
    assert d.approved, d.rejection_details
    # Même compte, plafond petit compte ramené à 4 % : un stop à 5 % exigerait
    # 5 % de risque, donc le refus est explicite.
    capped = RiskConfig(
        risk_per_trade_pct=Decimal("0.0175"),
        stop_mode=StopMode.STRUCTURAL_D1,
        max_notional_pct=Decimal("1.0"),
        small_account_full_allocation=True,
        small_account_max_risk_pct=Decimal("0.04"),
    )
    d2 = run(m=swing_market(swing_low="80"), a=tiny, cfg=capped)
    assert not d2.approved
    assert d2.rejection_code is RejectionCode.EXCHANGE_MIN_NOTIONAL_FAILED
    assert "plafond petit compte" in (d2.rejection_details or "")


def test_free_balance_below_min_notional_blocks_new_entry():
    d = run(a=account(free_quote=Decimal("9"), equity_quote=Decimal("10000")))
    assert not d.approved
    assert d.rejection_code is RejectionCode.EXCHANGE_MIN_NOTIONAL_FAILED
    assert "minimum notionnel" in (d.rejection_details or "")


# ---------------------------------------------------------------------------
# Sortie swing Chandelier (D-061)
# ---------------------------------------------------------------------------


def test_swing_exit_mode_defaults_to_ema20():
    assert RiskConfig().swing_exit_mode is SwingExitMode.EMA20


def test_swing_exit_mode_can_be_chandelier():
    cfg = RiskConfig(swing_exit_mode=SwingExitMode.CHANDELIER)
    assert cfg.swing_exit_mode is SwingExitMode.CHANDELIER


def test_unknown_swing_exit_mode_is_refused():
    with pytest.raises(ValueError, match="swing_exit_mode"):
        RiskConfig(swing_exit_mode="TRAILING")  # type: ignore[arg-type]


@pytest.mark.parametrize("period", [1, 0, -5, 201])
def test_chandelier_period_must_stay_in_bounds(period):
    with pytest.raises(ValueError, match="swing_chandelier_period"):
        RiskConfig(swing_chandelier_period=period)


@pytest.mark.parametrize("multiple", [Decimal("0"), Decimal("-1"), Decimal("NaN")])
def test_chandelier_multiple_must_be_positive_and_finite(multiple):
    with pytest.raises(ValueError, match="swing_chandelier_atr_multiple"):
        RiskConfig(swing_chandelier_atr_multiple=multiple)
