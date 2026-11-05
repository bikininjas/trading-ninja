"""Tests de la passerelle Kraken, hors réseau.

Le faux exchange reproduit le comportement **mesuré** de CCXT 4.5.91 sur Kraken :
`amount_to_precision` tronque, `price_to_precision` arrondit au plus proche. Les
valeurs du marché BTC/EUR (tick 0,1 · pas 1e-08 · min 0,00006 BTC · min 0,45 EUR)
viennent de `exchange.load_markets()` réel, pas d'une supposition.
"""

from __future__ import annotations

from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

import pytest

from app.engine.risk_gate import RejectionCode, RiskDecision
from app.exchange.kraken import (
    KeyPermissionError,
    OrderPlanError,
    bracket_params,
    build_bracket_entry,
    build_exchange,
    market_info,
    resolve_taker_fee,
    to_exchange_amount,
    to_exchange_stop_price,
    verify_key_permissions,
)

BTC_EUR = {
    "id": "XXBTZEUR",
    "symbol": "BTC/EUR",
    "base": "BTC",
    "quote": "EUR",
    "spot": True,
    "active": True,
    "type": "spot",
    "precision": {"amount": 1e-08, "price": 0.1},
    "limits": {
        "amount": {"min": 6e-05, "max": None},
        "cost": {"min": 0.45, "max": None},
        "price": {"min": None, "max": None},
    },
    "taker": 0.0026,
    "maker": 0.0016,
}


class FakeKraken:
    """Surface minimale de CCXT utilisée par l'adaptateur, sans réseau."""

    def __init__(self, market: dict | None = None, permissions: list[str] | None = None,
                 boom: Exception | None = None, extra: dict | None = None):
        self._market = market or BTC_EUR
        self._permissions = permissions
        self._boom = boom
        self._extra = extra or {}
        self.sent_orders: list[dict] = []

    # -- marchés ----------------------------------------------------------
    def market(self, symbol: str) -> dict:
        if symbol != self._market["symbol"]:
            raise KeyError(symbol)
        return self._market

    def amount_to_precision(self, symbol: str, amount) -> str:
        step = Decimal(str(self._market["precision"]["amount"]))
        floored = (Decimal(amount) / step).to_integral_value(rounding=ROUND_DOWN) * step
        return format(floored, "f")

    def price_to_precision(self, symbol: str, price) -> str:
        tick = Decimal(str(self._market["precision"]["price"]))
        rounded = (Decimal(price) / tick).to_integral_value(rounding=ROUND_HALF_UP) * tick
        return format(rounded, "f")

    # -- clé --------------------------------------------------------------
    def privatePostGetApiKeyInfo(self) -> dict:
        if self._boom is not None:
            raise self._boom
        return {"error": [], "result": {"permissions": self._permissions, **self._extra}}

    # -- ordres -----------------------------------------------------------
    def create_order(self, **kwargs):
        self.sent_orders.append(kwargs)
        return {"id": "FAKE-1", **kwargs}


HEALTHY_PERMISSIONS = [
    "query-funds",
    "query-open-trades",
    "query-closed-trades",
    "modify-trades",
    "close-trades",
]


def decision(**kwargs) -> RiskDecision:
    base = {
        "approved": True,
        "amount": Decimal("0.00123456789"),
        "stop_loss_price": Decimal("70000.0"),
    }
    base.update(kwargs)
    return RiskDecision(**base)


# ---------------------------------------------------------------------------
# Marché → contraintes du pare-feu
# ---------------------------------------------------------------------------


def test_market_info_maps_limits_to_decimal_constraints():
    info = market_info(FakeKraken(), "BTC/EUR")
    assert info.exchange_symbol == "XXBTZEUR"
    assert info.quote == "EUR"
    assert info.constraints.amount_step == Decimal("1E-8")
    assert info.constraints.price_tick == Decimal("0.1")
    assert info.constraints.min_amount == Decimal("0.00006")
    assert info.constraints.min_notional == Decimal("0.45")
    assert info.taker_fee == Decimal("0.0026")


class FeeExchange:
    def __init__(self, taker: str | None = None, *, fail: bool = False):
        self._taker = taker
        self._fail = fail

    def fetch_trading_fee(self, symbol):
        if self._fail:
            raise RuntimeError("clé absente")
        return {"taker": self._taker}


def test_resolve_taker_fee_reads_the_account_tier():
    fee = resolve_taker_fee(FeeExchange("0.008"), "BTC/EUR", Decimal("0.0026"))
    assert fee == Decimal("0.008")


def test_resolve_taker_fee_falls_back_when_the_private_call_fails():
    fee = resolve_taker_fee(FeeExchange(fail=True), "BTC/EUR", Decimal("0.008"))
    assert fee == Decimal("0.008")


def test_market_info_accepts_the_real_fee_as_an_override():
    info = market_info(FakeKraken(), "BTC/EUR", taker_fee=Decimal("0.008"))
    assert info.taker_fee == Decimal("0.008")


def test_eur_pair_is_spot_and_active():
    info = market_info(FakeKraken(), "BTC/EUR")
    assert info.active is True
    assert info.base == "BTC"


def test_non_spot_market_is_refused():
    market = {**BTC_EUR, "spot": False}
    with pytest.raises(Exception, match="spot"):
        market_info(FakeKraken(market=market), "BTC/EUR")


# ---------------------------------------------------------------------------
# Précision : jamais au-delà du budget
# ---------------------------------------------------------------------------


def test_amount_is_never_rounded_above_the_validated_quantity():
    exchange = FakeKraken()
    amount = Decimal("0.000123456789")
    assert to_exchange_amount(exchange, "BTC/EUR", amount) == "0.00012345"
    assert Decimal("0.00012345") <= amount


def test_float_amount_is_refused():
    with pytest.raises(OrderPlanError, match="Decimal"):
        to_exchange_amount(FakeKraken(), "BTC/EUR", 0.001)


def test_stop_rounded_below_the_validated_price_is_refused():
    # 70000,04 arrondi au tick 0,1 donne 70000,0 : plus bas, donc plus de perte.
    with pytest.raises(OrderPlanError, match="plus bas"):
        to_exchange_stop_price(FakeKraken(), "BTC/EUR", Decimal("70000.04"))


def test_stop_rounded_up_is_accepted():
    assert to_exchange_stop_price(FakeKraken(), "BTC/EUR", Decimal("70000.06")) == "70000.1"


# ---------------------------------------------------------------------------
# Bracket
# ---------------------------------------------------------------------------


def test_bracket_uses_the_close_stop_loss_parameter():
    params = bracket_params("70000.0")
    assert params == {"close": {"ordertype": "stop-loss", "price": "70000.0"}}


def test_bracket_entry_shape_is_kraken_compatible():
    args = build_bracket_entry(FakeKraken(), decision(), "BTC/EUR")
    assert args["symbol"] == "BTC/EUR"
    assert args["type"] == "market"
    assert args["side"] == "buy"
    assert args["amount"] == "0.00123456"   # tronqué, jamais 0.00123457
    assert args["price"] is None
    assert args["params"]["close"] == {"ordertype": "stop-loss", "price": "70000.0"}
    # Tout ce que CCXT reçoit est une chaîne : aucun flottant ne traverse.
    assert isinstance(args["amount"], str)
    assert isinstance(args["params"]["close"]["price"], str)


def test_unapproved_decision_cannot_produce_an_order():
    refused = RiskDecision(approved=False, rejection_code=RejectionCode.CONFIDENCE_TOO_LOW)
    with pytest.raises(OrderPlanError, match="décision refusée"):
        build_bracket_entry(FakeKraken(), refused, "BTC/EUR")


def test_approved_decision_without_stop_is_refused():
    with pytest.raises(OrderPlanError, match="sans quantité ou sans stop"):
        build_bracket_entry(FakeKraken(), decision(stop_loss_price=None), "BTC/EUR")


# ---------------------------------------------------------------------------
# Permissions de la clé
# ---------------------------------------------------------------------------


def test_healthy_key_passes():
    audit = verify_key_permissions(FakeKraken(permissions=HEALTHY_PERMISSIONS))
    assert "query-funds" in audit.permissions


def test_ui_style_labels_are_normalised():
    audit = verify_key_permissions(
        FakeKraken(
            permissions=[
                "Query Funds",
                "Query Open Orders & Trades",
                "Query Closed Orders & Trades",
                "Create & Modify Orders",
                "Cancel/Close Orders",
            ]
        )
    )
    assert "Query Funds" in audit.permissions


def test_ip_allowlist_is_read_in_snake_case():
    # Kraken renvoie `ip_allowlist` ; la doc montre `ipAllowlist`. Lire le mauvais
    # nom fait croire que la clé n'est pas restreinte alors qu'elle l'est.
    audit = verify_key_permissions(
        FakeKraken(permissions=HEALTHY_PERMISSIONS, extra={"ip_allowlist": ["82.65.136.32/32"]})
    )
    assert audit.ip_allowlist == ("82.65.136.32/32",)


def test_ip_allowlist_is_read_in_camel_case_too():
    audit = verify_key_permissions(
        FakeKraken(permissions=HEALTHY_PERMISSIONS, extra={"ipAllowlist": ["10.0.0.1/32"]})
    )
    assert audit.ip_allowlist == ("10.0.0.1/32",)


def test_withdraw_permission_is_fatal():
    with pytest.raises(KeyPermissionError, match="retraits"):
        verify_key_permissions(FakeKraken(permissions=[*HEALTHY_PERMISSIONS, "withdraw-funds"]))


def test_withdraw_label_in_ui_style_is_also_caught():
    with pytest.raises(KeyPermissionError, match="retraits"):
        verify_key_permissions(
            FakeKraken(permissions=[*HEALTHY_PERMISSIONS, "Withdraw Funds"])
        )


def test_missing_cancel_permission_is_fatal():
    permissions = [p for p in HEALTHY_PERMISSIONS if p != "close-trades"]
    with pytest.raises(KeyPermissionError, match="annuler"):
        verify_key_permissions(FakeKraken(permissions=permissions))


def test_old_cancel_label_is_still_accepted():
    # Les clés créées avant le nouveau modèle renvoient `cancel-orders`.
    permissions = [p for p in HEALTHY_PERMISSIONS if p != "close-trades"] + ["cancel-orders"]
    audit = verify_key_permissions(FakeKraken(permissions=permissions))
    assert "cancel-orders" in audit.permissions


def test_unreadable_permissions_fail_closed():
    with pytest.raises(KeyPermissionError, match="illisibles"):
        verify_key_permissions(FakeKraken(boom=RuntimeError("EAPI:Invalid key")))


def test_empty_permissions_fail_closed():
    with pytest.raises(KeyPermissionError, match="aucune permission"):
        verify_key_permissions(FakeKraken(permissions=[]))


# ---------------------------------------------------------------------------
# Instanciation
# ---------------------------------------------------------------------------


def test_build_exchange_sets_the_two_non_negotiable_options():
    exchange = build_exchange(api_key="pub", secret="c2VjcmV0")
    assert exchange.id == "kraken"
    assert exchange.enableRateLimit is True
    assert exchange.options["adjustForTimeDifference"] is True
    assert exchange.apiKey == "pub"
