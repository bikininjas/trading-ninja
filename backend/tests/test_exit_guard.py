"""Tests du garde-fou de sortie — le LLM solde, il ne shorte jamais."""

from __future__ import annotations

from decimal import Decimal

from app.engine.exit_guard import ExitRejectionCode, OpenPosition, evaluate_exit
from app.engine.risk_gate import Action, RiskConfig, TradeIntent

CONFIG = RiskConfig()
POSITION = OpenPosition(symbol="BTC/EUR", amount=Decimal("0.046"), entry_price=Decimal("100"))


def intent(action: str = "SELL", confidence: str = "0.9") -> TradeIntent:
    return TradeIntent(Action(action), float(confidence), market_regime="TRENDING_UP")


def test_sell_closes_the_whole_position():
    decision = evaluate_exit(intent(), "BTC/EUR", POSITION, CONFIG)
    assert decision.approved is True
    assert decision.amount == POSITION.amount
    assert decision.reason == "LLM_SELL"


def test_sell_without_position_is_refused():
    decision = evaluate_exit(intent(), "BTC/EUR", None, CONFIG)
    assert decision.approved is False
    assert decision.rejection_code is ExitRejectionCode.NO_OPEN_POSITION


def test_symbol_mismatch_is_refused():
    decision = evaluate_exit(intent(), "ETH/EUR", POSITION, CONFIG)
    assert decision.approved is False
    assert decision.rejection_code is ExitRejectionCode.SYMBOL_MISMATCH


def test_buy_is_not_an_exit():
    decision = evaluate_exit(intent("BUY"), "BTC/EUR", POSITION, CONFIG)
    assert decision.approved is False
    assert decision.rejection_code is ExitRejectionCode.ACTION_IS_NOT_SELL


def test_low_confidence_sell_is_refused():
    decision = evaluate_exit(intent(confidence="0.50"), "BTC/EUR", POSITION, CONFIG)
    assert decision.approved is False
    assert decision.rejection_code is ExitRejectionCode.EXIT_CONFIDENCE_TOO_LOW


def test_halt_requires_a_higher_confidence_than_sell():
    low = evaluate_exit(intent("HALT", "0.70"), "BTC/EUR", POSITION, CONFIG)
    assert low.approved is False
    assert low.rejection_code is ExitRejectionCode.EXIT_CONFIDENCE_TOO_LOW
    high = evaluate_exit(intent("HALT", "0.85"), "BTC/EUR", POSITION, CONFIG)
    assert high.approved is True
    assert high.reason == "LLM_HALT"


def test_zero_amount_is_refused():
    broken = OpenPosition(symbol="BTC/EUR", amount=Decimal(0), entry_price=Decimal("100"))
    decision = evaluate_exit(intent(), "BTC/EUR", broken, CONFIG)
    assert decision.approved is False
    assert decision.rejection_code is ExitRejectionCode.MALFORMED_EXIT


def test_nan_confidence_is_refused():
    decision = evaluate_exit(
        TradeIntent(Action.SELL, float("nan"), market_regime="TRENDING_UP"),
        "BTC/EUR", POSITION, CONFIG,
    )
    assert decision.approved is False
    assert decision.rejection_code is ExitRejectionCode.MALFORMED_EXIT
