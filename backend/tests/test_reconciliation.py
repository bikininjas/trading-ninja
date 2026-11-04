"""Tests de la réconciliation base ↔ exchange, sans réseau ni base réelle.

Le module testé est un planificateur pur ; les tests couvrent les trois verdicts
qui comptent : stop armé, stop absent, stop insuffisant. Le solde insuffisant a
son propre test parce qu'il interdit la liquidation (on ne vend pas ce qu'on n'a
pas) et impose l'arrêt.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.db.repository import PositionRow
from app.runtime.reconciliation import (
    CRITICAL,
    OK,
    WARN,
    RemoteOrder,
    inspect_exchange,
    plan_reconciliation,
    read_account,
    read_remote_orders,
)

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)


def position(amount: str = "0.010", stop: str = "96") -> PositionRow:
    return PositionRow(
        id=uuid.uuid4(), symbol="BTC/EUR", entry_price=Decimal("100"),
        amount=Decimal(amount), stop_loss_price=Decimal(stop),
        take_profit_price=Decimal("105"), opened_at=NOW,
        risk_amount_quote=Decimal("0.10"),
    )


def order(*, amount: str = "0.010", stop: str | None = "96",
          side: str = "sell", symbol: str = "BTC/EUR",
          order_id: str = "O-1", order_type: str = "stop-loss",
          client_order_id: str | None = None) -> RemoteOrder:
    return RemoteOrder(
        id=order_id, symbol=symbol, side=side, order_type=order_type,
        amount=Decimal(amount),
        stop_price=None if stop is None else Decimal(stop),
        status="open", client_order_id=client_order_id,
    )


def plan(positions, orders=(), balances=None, mode="LIVE"):
    if balances is None:
        balances = {"BTC/EUR": Decimal("0.010")}
    return plan_reconciliation(
        positions, orders, balances, mode=mode, quote_currency="EUR",
        quote_balance=Decimal("9.37"),
    )


def codes(result) -> list[str]:
    return [finding.code for finding in result.findings]


# -- mode papier -------------------------------------------------------------


def test_paper_mode_never_liquidates_an_internal_position():
    result = plan([position()], mode="PAPER")

    assert result.liquidate == ()
    assert not result.halt
    assert "NO_REMOTE_POSITION_EXPECTED" in codes(result)


def test_an_order_found_in_paper_mode_is_only_a_warning():
    result = plan([], [order()], mode="PAPER")

    assert result.liquidate == ()
    assert not result.halt
    finding = next(item for item in result.findings if item.code == "REMOTE_ORDER_OPEN")
    assert finding.severity == WARN
    assert "non géré par le bot" in finding.details


# -- mode live ---------------------------------------------------------------


def test_live_position_with_an_armed_stop_is_clean():
    result = plan([position()], [order()])

    assert result.clean
    assert result.liquidate == ()
    finding = next(item for item in result.findings if item.code == "STOP_ARMED")
    assert finding.severity == OK
    assert "stop validé 96" in finding.details


def test_live_position_without_a_stop_must_be_liquidated():
    result = plan([position()], [])

    assert not result.clean
    assert result.liquidate == ("BTC/EUR",)
    finding = next(item for item in result.findings if item.code == "STOP_MISSING")
    assert finding.severity == CRITICAL


def test_a_stop_covering_less_than_the_position_is_insufficient():
    result = plan([position(amount="0.010")], [order(amount="0.006")])

    assert result.liquidate == ("BTC/EUR",)
    finding = next(item for item in result.findings if item.code == "STOP_INSUFFICIENT")
    assert "0.006" in finding.details


def test_a_stop_below_the_validated_price_is_insufficient():
    result = plan([position(stop="96")], [order(stop="92")])

    assert result.liquidate == ("BTC/EUR",)
    finding = next(item for item in result.findings if item.code == "STOP_INSUFFICIENT")
    assert "sous le stop validé" in finding.details


def test_a_stop_above_the_validated_price_is_accepted():
    result = plan([position(stop="96")], [order(stop="97.5")])

    assert result.clean
    assert result.liquidate == ()


def test_a_buy_order_is_not_a_protective_stop():
    result = plan([position()], [order(side="buy")])

    assert result.liquidate == ("BTC/EUR",)
    assert "STOP_MISSING" in codes(result)


def test_missing_base_balance_halts_without_selling():
    result = plan([position(amount="0.010")], [], balances={"BTC/EUR": Decimal("0.004")})

    assert result.halt is True
    assert result.liquidate == ()
    finding = next(item for item in result.findings if item.code == "BALANCE_TOO_SMALL")
    assert finding.severity == CRITICAL
    assert "aucune vente ne doit partir" in finding.details


def test_unmatched_orders_are_reported_but_never_cancelled():
    result = plan([position()], [order(), order(order_id="TP-1", stop=None,
                                                order_type="take-profit")])

    assert result.liquidate == ()
    assert not result.halt
    finding = next(item for item in result.findings
                   if item.code == "UNMATCHED_REMOTE_ORDER")
    assert finding.severity == WARN
    assert "TP-1" in finding.details
    # Le plan ne contient aucune action d'annulation : la réconciliation constate,
    # l'exécuteur décide. Un ordre manuel ne doit jamais être annulé par erreur.
    assert not hasattr(result, "cancel")


def test_an_orphan_stop_without_a_position_halts_the_bot():
    result = plan([], [order(order_id="STOP-ORPHELIN")])

    assert result.halt is True
    assert not result.clean
    assert result.liquidate == ()
    finding = next(item for item in result.findings if item.code == "ORPHAN_STOP")
    assert finding.severity == CRITICAL
    assert "avoir" in finding.details


# -- lecture de l'exchange ---------------------------------------------------


class FakeExchange:
    def __init__(self, orders, balance):
        self._orders = orders
        self._balance = balance

    def fetch_open_orders(self):
        return self._orders

    def fetch_balance(self):
        return self._balance


def test_read_remote_orders_uses_stop_loss_price_and_ignores_foreign_symbols():
    exchange = FakeExchange(
        [
            {"id": "A", "symbol": "BTC/EUR", "side": "sell", "type": "market",
             "amount": "0.01", "stopLossPrice": "96000", "status": "open"},
            {"id": "B", "symbol": "ETH/EUR", "side": "sell", "type": "market",
             "amount": "0.2", "stopLossPrice": "3000", "status": "open"},
        ],
        {},
    )

    orders = read_remote_orders(exchange, ["BTC/EUR"])

    assert [item.id for item in orders] == ["A"]
    assert orders[0].stop_price == Decimal("96000")


def test_read_remote_orders_accepts_trigger_price_but_not_a_percentage():
    exchange = FakeExchange(
        [
            {"id": "A", "symbol": "BTC/EUR", "side": "sell", "type": "market",
             "amount": "0.01", "triggerPrice": "95000", "status": "open"},
            {"id": "B", "symbol": "BTC/EUR", "side": "sell", "type": "trailing-stop",
             "amount": "0.01", "triggerPrice": "+50%", "status": "open"},
            {"id": "C", "symbol": "BTC/EUR", "side": "sell", "type": "market",
             "amount": "0.01", "stopLossPrice": "90000", "status": "closed"},
        ],
        {},
    )

    orders = read_remote_orders(exchange, ["BTC/EUR"])

    assert [item.id for item in orders] == ["A", "B"]
    assert orders[0].stop_price == Decimal("95000")
    assert orders[1].stop_price is None  # un pourcentage n'est pas un prix


def test_read_account_prefers_total_and_falls_back_to_free():
    exchange = FakeExchange(
        [],
        {
            "total": {"BTC": "0.02", "EUR": "9.37"},
            "free": {"BTC": "0.01", "EUR": "5.00", "ETH": "0.1"},
        },
    )

    balances, quote = read_account(exchange, ["BTC/EUR", "ETH/EUR"], "EUR")

    assert balances == {"BTC/EUR": Decimal("0.02"), "ETH/EUR": Decimal("0.1")}
    assert quote == Decimal("9.37")


class FakeRepo:
    def __init__(self, positions):
        self._positions = positions
        self.calls: list[str] = []

    async def open_positions(self, mode):
        self.calls.append(mode)
        return list(self._positions)


@pytest.mark.asyncio
async def test_inspect_exchange_reads_positions_orders_and_balance():
    exchange = FakeExchange(
        [{"id": "A", "symbol": "BTC/EUR", "side": "sell", "type": "market",
          "amount": "0.010", "stopLossPrice": "96", "status": "open"}],
        {"total": {"BTC": "0.010", "EUR": "9.37"}},
    )
    repo = FakeRepo([position()])

    result = await inspect_exchange(
        repo, exchange, "LIVE", ["BTC/EUR"], quote_currency="EUR"
    )

    assert result.clean
    assert result.positions == 1
    assert result.remote_orders == 1
    assert repo.calls == ["LIVE"]
