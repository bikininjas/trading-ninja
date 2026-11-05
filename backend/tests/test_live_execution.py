"""Tests de l'exécuteur réel — aucun réseau, aucune clé, aucun ordre réel."""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from app.engine.risk_gate import RiskDecision
from app.execution.live import LiveBroker, LiveExecutionError

SYMBOL = "BTC/EUR"


def decision(**overrides) -> RiskDecision:
    values = {
        "approved": True,
        "amount": Decimal("0.05"),
        "notional_quote": Decimal("5"),
        "stop_loss_price": Decimal("95"),
        "take_profit_price": Decimal("110"),
        "stop_distance_quote": Decimal("5"),
        "intended_risk_quote": Decimal("0.25"),
        "effective_risk_quote": Decimal("0.25"),
        "effective_risk_pct": Decimal("0.02"),
    }
    values.update(overrides)
    return RiskDecision(**values)


class FakeExchange:
    def __init__(self, *, stop_missing: bool = False, order_status: str = "closed",
                 create_raises: bool = False, sell_raises: bool = False,
                 base_free: str = "0", support_edit: bool = False,
                 stop_create_raises: bool = False,
                 fail_cancel_call: int | None = None):
        self.stop_missing = stop_missing
        self.order_status = order_status
        self.create_raises = create_raises
        self.sell_raises = sell_raises
        self.base_free = base_free
        self.stop_create_raises = stop_create_raises
        self.fail_cancel_call = fail_cancel_call
        self.has = {"editOrder": True} if support_edit else {}
        self.orders: dict[str, dict] = {}
        self.open_orders: list[dict] = []
        self.trades: list[dict] = []
        self.create_calls: list[dict] = []
        self.cancel_calls: list[str] = []
        self.events: list[tuple] = []
        self._next = 0

    def amount_to_precision(self, symbol, amount):
        return str(amount)

    def price_to_precision(self, symbol, price):
        return str(price)

    def _new_id(self, prefix: str) -> str:
        self._next += 1
        return f"{prefix}-{self._next}"

    def create_order(self, symbol, type, side, amount, price=None, params=None):
        self.create_calls.append({
            "symbol": symbol, "type": type, "side": side, "amount": amount,
            "price": price, "params": params or {},
        })
        self.events.append(("create", type, side))
        if self.create_raises:
            raise RuntimeError("Insufficient funds")
        if type == "stop-loss" and self.stop_create_raises:
            raise RuntimeError("Stop rejected")
        if side == "sell" and self.sell_raises and type == "market":
            raise RuntimeError("Market closed")
        order_id = self._new_id("order")
        order = {
            "id": order_id, "symbol": symbol, "side": side, "type": type,
            "amount": amount, "filled": amount, "status": self.order_status,
            "average": "100", "price": "100",
            "fee": {"cost": "0.08", "currency": "EUR"},
        }
        self.orders[order_id] = order
        if side == "sell" and type == "stop-loss":
            stop = {
                "id": order_id, "symbol": symbol, "side": "sell",
                "type": "stop-loss", "amount": amount, "filled": "0",
                "status": "open",
                "stopLossPrice": (params or {}).get("stopLossPrice"),
            }
            self.orders[order_id] = stop
            self.open_orders.append(stop)
            return stop
        if side == "buy" and params and params.get("close") and not self.stop_missing:
            stop_price = params["close"]["price"]
            stop = {
                "id": self._new_id("stop"), "symbol": symbol, "side": "sell",
                "type": "stop-loss", "amount": amount, "filled": "0",
                "status": "open", "stopLossPrice": stop_price,
            }
            self.orders[stop["id"]] = stop
            self.open_orders.append(stop)
        if side == "sell" and type == "take-profit":
            tp = {
                "id": self._new_id("tp"), "symbol": symbol, "side": "sell",
                "type": "take-profit", "amount": amount, "filled": "0",
                "status": "open", "takeProfitPrice": (params or {}).get("takeProfitPrice"),
            }
            self.orders[tp["id"]] = tp
            self.open_orders.append(tp)
        if side == "sell" and type == "market":
            # Une vente réelle consomme la position ; les protections restantes
            # sont annulées par l'appelant, pas par l'exchange.
            for existing in list(self.open_orders):
                if existing["side"] == "sell":
                    self.open_orders.remove(existing)
        return order

    def edit_order(self, order_id, symbol, type, side, amount, price=None, params=None):
        self.events.append(("edit", type, order_id))
        new_price = (params or {}).get("stopLossPrice")
        for order in self.open_orders:
            if order["id"] == order_id:
                order["stopLossPrice"] = new_price
                order["amount"] = amount
        self.orders[order_id]["stopLossPrice"] = new_price
        self.orders[order_id]["amount"] = amount
        return self.orders[order_id]

    def fetch_order(self, order_id, symbol=None):
        return self.orders[order_id]

    def fetch_open_orders(self, symbol=None):
        return list(self.open_orders)

    def fetch_my_trades(self, symbol=None, since=None, limit=None):
        return list(self.trades)

    def cancel_order(self, order_id, symbol=None):
        self.events.append(("cancel", order_id))
        self.cancel_calls.append(order_id)
        if (self.fail_cancel_call is not None
                and len(self.cancel_calls) == self.fail_cancel_call):
            raise RuntimeError("Cancel rejected")
        for order in list(self.open_orders):
            if order["id"] == order_id:
                self.open_orders.remove(order)
                order["status"] = "canceled"
                return {"id": order_id, "status": "canceled"}
        raise RuntimeError("Order not found")

    def fetch_balance(self):
        return {
            "free": {"EUR": "9.37", "BTC": self.base_free},
            "total": {"EUR": "9.37", "BTC": self.base_free},
        }


def broker(exchange: FakeExchange, **overrides) -> LiveBroker:
    values = {
        "quote_currency": "EUR",
        "taker_fee": Decimal("0.008"),
        "poll_interval": 0,
        "timeout_seconds": 0.05,
        "sleep": _noop_sleep,
    }
    values.update(overrides)
    return LiveBroker(exchange, **values)


async def _noop_sleep(_seconds: float) -> None:
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_entry_places_a_bracket_and_reads_the_actual_fee():
    exchange = FakeExchange()
    fill = await broker(exchange).entry(
        decision(), Decimal("100"), symbol=SYMBOL
    )

    assert fill.price == Decimal("100")
    assert fill.amount == Decimal("0.05")
    assert fill.fee_quote == Decimal("0.08")  # frais réels, pas 0,05 × 0,008
    assert fill.order_id is not None
    assert fill.stop_order_id is not None
    assert exchange.create_calls[0]["params"]["close"]["ordertype"] == "stop-loss"
    assert exchange.open_orders[0]["stopLossPrice"] == "95"


@pytest.mark.asyncio
async def test_entry_without_a_stop_unwinds_the_position():
    exchange = FakeExchange(stop_missing=True)

    with pytest.raises(LiveExecutionError, match="stop absent"):
        await broker(exchange).entry(decision(), Decimal("100"), symbol=SYMBOL)

    sides = [call["side"] for call in exchange.create_calls]
    assert sides == ["buy", "sell"]


@pytest.mark.asyncio
async def test_entry_is_refused_when_the_exchange_rejects_the_buy():
    exchange = FakeExchange(create_raises=True)

    with pytest.raises(LiveExecutionError, match="achat refusé"):
        await broker(exchange).entry(decision(), Decimal("100"), symbol=SYMBOL)

    assert exchange.create_calls == [{
        "symbol": SYMBOL, "type": "market", "side": "buy",
        "amount": "0.05", "price": None,
        "params": {"close": {"ordertype": "stop-loss", "price": "95"}},
    }]


@pytest.mark.asyncio
async def test_an_unfilled_order_is_canceled_and_raises():
    exchange = FakeExchange(order_status="open")

    with pytest.raises(LiveExecutionError, match="non rempli"):
        await broker(exchange).entry(decision(), Decimal("100"), symbol=SYMBOL)

    assert exchange.cancel_calls == ["order-1"]


@pytest.mark.asyncio
async def test_exit_cancels_protections_before_selling():
    exchange = FakeExchange()
    exchange.open_orders = [
        {"id": "stop-1", "symbol": SYMBOL, "side": "sell", "type": "stop-loss",
         "amount": "0.05", "stopLossPrice": "95"},
        {"id": "tp-1", "symbol": SYMBOL, "side": "sell", "type": "take-profit",
         "amount": "0.05", "takeProfitPrice": "110"},
    ]

    fill = await broker(exchange).exit(
        Decimal("0.05"), Decimal("100"), symbol=SYMBOL, stop_price=Decimal("95")
    )

    assert exchange.cancel_calls == ["stop-1", "tp-1"]
    assert all(call["side"] == "sell" for call in exchange.create_calls)
    assert fill.amount == Decimal("0.05")
    assert fill.fee_quote == Decimal("0.08")


@pytest.mark.asyncio
async def test_exit_rearms_the_stop_when_the_sale_fails():
    exchange = FakeExchange(sell_raises=True)
    exchange.open_orders = [
        {"id": "stop-1", "symbol": SYMBOL, "side": "sell", "type": "stop-loss",
         "amount": "0.05", "stopLossPrice": "95"},
    ]

    with pytest.raises(LiveExecutionError, match="vente refusée"):
        await broker(exchange).exit(
            Decimal("0.05"), Decimal("100"), symbol=SYMBOL, stop_price=Decimal("95")
        )

    assert exchange.cancel_calls == ["stop-1"]
    assert exchange.create_calls[-1]["type"] == "stop-loss"
    assert exchange.create_calls[-1]["params"]["stopLossPrice"] == "95"


@pytest.mark.asyncio
async def test_balance_exposes_free_quote_and_base():
    exchange = FakeExchange(base_free="0.05")
    b = broker(exchange)
    assert await b.free_quote() == Decimal("9.37")
    assert await b.free_base(SYMBOL) == Decimal("0.05")


@pytest.mark.asyncio
async def test_position_state_reads_the_base_and_the_stop():
    exchange = FakeExchange(base_free="0.05")
    exchange.open_orders = [{
        "id": "stop-9", "symbol": SYMBOL, "side": "sell", "type": "stop-loss",
        "amount": "0.05", "stopLossPrice": "95", "status": "open",
    }]

    state = await broker(exchange).position_state(
        SYMBOL, Decimal("0.05"), Decimal("95")
    )

    assert state.base_total == Decimal("0.05")
    assert state.stop_ok is True
    assert state.stop_order_id == "stop-9"


@pytest.mark.asyncio
async def test_last_sell_fill_reads_the_real_trade_and_its_fee():
    exchange = FakeExchange()
    exchange.trades = [{
        "id": "trade-1", "symbol": SYMBOL, "side": "sell", "amount": "0.05",
        "price": "90", "fee": {"cost": "0.036", "currency": "EUR"},
        "timestamp": 1_700_000_000_000,
    }]

    fill = await broker(exchange).last_sell_fill(
        SYMBOL, since=1_600_000_000_000
    )

    assert fill is not None
    assert fill.price == Decimal("90")
    assert fill.fee_quote == Decimal("0.036")
    assert fill.amount == Decimal("0.05")


@pytest.mark.asyncio
async def test_entry_refuses_a_shared_base_balance():
    exchange = FakeExchange(base_free="0.05")

    with pytest.raises(LiveExecutionError, match="sous-compte dédié"):
        await broker(exchange).entry(decision(), Decimal("100"), symbol=SYMBOL)


# ---------------------------------------------------------------------------
# Remplacement du stop (breakeven / trailing) — D-061
# ---------------------------------------------------------------------------


async def _entered(exchange: FakeExchange):
    live = broker(exchange)
    fill = await live.entry(decision(), Decimal("100"), symbol=SYMBOL)
    return live, fill


@pytest.mark.asyncio
async def test_stop_replacement_creates_the_new_stop_before_cancelling_the_old():
    exchange = FakeExchange()
    live, fill = await _entered(exchange)
    old = fill.stop_order_id

    new = await live.update_stop_loss_order(
        SYMBOL, fill.amount, old, Decimal("99")
    )

    assert new != old
    create_at = exchange.events.index(("create", "stop-loss", "sell"))
    cancel_at = exchange.events.index(("cancel", old))
    assert create_at < cancel_at, "l'ancien stop a été annulé avant le remplacement"
    assert old not in [order["id"] for order in exchange.open_orders]
    remaining = [order for order in exchange.open_orders if order["id"] == new]
    assert len(remaining) == 1
    assert Decimal(remaining[0]["stopLossPrice"]) == Decimal("99")


@pytest.mark.asyncio
async def test_supported_native_edit_keeps_the_same_stop():
    exchange = FakeExchange(support_edit=True)
    live, fill = await _entered(exchange)

    new = await live.update_stop_loss_order(
        SYMBOL, fill.amount, fill.stop_order_id, Decimal("99")
    )

    assert new == fill.stop_order_id
    assert ("create", "stop-loss", "sell") not in exchange.events
    assert exchange.cancel_calls == []
    assert Decimal(exchange.orders[new]["stopLossPrice"]) == Decimal("99")


@pytest.mark.asyncio
async def test_an_unchanged_stop_price_is_a_noop():
    exchange = FakeExchange()
    live, fill = await _entered(exchange)
    old_price = Decimal(exchange.orders[fill.stop_order_id]["stopLossPrice"])

    new = await live.update_stop_loss_order(
        SYMBOL, fill.amount, fill.stop_order_id, old_price
    )

    assert new == fill.stop_order_id
    assert exchange.cancel_calls == []
    assert ("create", "stop-loss", "sell") not in exchange.events


@pytest.mark.asyncio
async def test_a_lower_stop_is_refused():
    exchange = FakeExchange()
    live, fill = await _entered(exchange)

    with pytest.raises(LiveExecutionError, match="ne desserre jamais"):
        await live.update_stop_loss_order(
            SYMBOL, fill.amount, fill.stop_order_id, Decimal("90")
        )

    assert exchange.cancel_calls == []
    assert fill.stop_order_id in [order["id"] for order in exchange.open_orders]


@pytest.mark.asyncio
async def test_a_failed_replacement_keeps_the_old_stop():
    exchange = FakeExchange()
    live, fill = await _entered(exchange)
    exchange.stop_create_raises = True

    with pytest.raises(LiveExecutionError, match="réarmement"):
        await live.update_stop_loss_order(
            SYMBOL, fill.amount, fill.stop_order_id, Decimal("99")
        )

    assert exchange.cancel_calls == []
    assert fill.stop_order_id in [order["id"] for order in exchange.open_orders]


@pytest.mark.asyncio
async def test_a_failed_cancel_of_the_old_stop_removes_the_new_one():
    exchange = FakeExchange(fail_cancel_call=1)
    live, fill = await _entered(exchange)

    with pytest.raises(LiveExecutionError, match="non annulé"):
        await live.update_stop_loss_order(
            SYMBOL, fill.amount, fill.stop_order_id, Decimal("99")
        )

    stops = [order for order in exchange.open_orders if order["type"] == "stop-loss"]
    assert len(stops) == 1
    assert stops[0]["id"] == fill.stop_order_id


@pytest.mark.asyncio
async def test_a_terminated_old_stop_is_not_replaced():
    exchange = FakeExchange()
    live, fill = await _entered(exchange)
    exchange.orders[fill.stop_order_id]["status"] = "closed"

    with pytest.raises(LiveExecutionError, match="déjà terminé"):
        await live.update_stop_loss_order(
            SYMBOL, fill.amount, fill.stop_order_id, Decimal("99")
        )

    assert exchange.cancel_calls == []


class PanicExchange(FakeExchange):
    """Exchange avec soldes mutables : une vente au marché les consomme."""

    def __init__(self, balances: dict[str, str], markets: dict | None = None,
                 sell_raises: bool = False) -> None:
        super().__init__(sell_raises=sell_raises)
        self.balances = {key: Decimal(value) for key, value in balances.items()}
        self.markets = markets or {}

    def fetch_balance(self):
        raw = {key: str(value) for key, value in self.balances.items()}
        return {"free": dict(raw), "total": dict(raw)}

    def create_order(self, symbol, type, side, amount, price=None, params=None):
        order = super().create_order(symbol, type, side, amount, price, params)
        if side == "sell" and type == "market":
            base = symbol.split("/")[0]
            self.balances[base] = max(
                Decimal(0), self.balances.get(base, Decimal(0)) - Decimal(str(amount))
            )
        return order


@pytest.mark.asyncio
async def test_panic_sell_cancels_every_order_and_flattens_every_balance():
    exchange = PanicExchange(
        {"EUR": "1.00", "BTC": "0.05", "ETH": "0.02"},
        markets={
            "BTC/EUR": {"base": "BTC", "quote": "EUR", "spot": True},
            "ETH/EUR": {"base": "ETH", "quote": "EUR", "spot": True},
        },
    )
    exchange.open_orders = [
        {"id": "stop-1", "symbol": "BTC/EUR", "side": "sell", "type": "stop-loss",
         "amount": "0.05", "status": "open", "stopLossPrice": "95"},
        {"id": "limit-1", "symbol": "ETH/EUR", "side": "sell", "type": "limit",
         "amount": "0.02", "status": "open"},
    ]
    report = await broker(exchange).panic_sell(["BTC/EUR"])

    assert report.complete
    assert report.canceled_orders == 2
    assert {item.symbol for item in report.exits} == {"BTC/EUR", "ETH/EUR"}
    assert all(item.fill is not None for item in report.exits)
    assert exchange.balances["BTC"] == 0
    assert exchange.balances["ETH"] == 0
    assert exchange.open_orders == []


@pytest.mark.asyncio
async def test_panic_sell_reports_a_failed_sale_without_raising():
    exchange = PanicExchange({"EUR": "1.00", "BTC": "0.05"},
                             sell_raises=True)
    report = await broker(exchange).panic_sell(["BTC/EUR"])

    assert not report.complete
    assert report.exits[0].fill is None
    assert report.exits[0].error is not None
    assert any("BTC/EUR" in error for error in report.errors)


@pytest.mark.asyncio
async def test_panic_sell_without_balance_is_a_clean_noop():
    exchange = PanicExchange({"EUR": "9.37"})
    report = await broker(exchange).panic_sell(["BTC/EUR"])

    assert report.complete
    assert report.exits == ()
    assert exchange.create_calls == []
