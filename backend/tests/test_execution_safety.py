"""Pannes après acceptation, pagination et protections : aucun renvoi aveugle."""

from decimal import Decimal as D

import ccxt
import pytest

from app.execution.live import LiveBroker, LiveExecutionError, UncertainExecutionError
from tests.test_live_execution import (
    FakeExchange,
    MemoryJournal,
    PanicExchange,
    SYMBOL,
    broker,
    decision,
)

pytestmark = pytest.mark.asyncio


class LostReply(FakeExchange):
    def create_order(self, *args, **kwargs):
        order = super().create_order(*args, **kwargs)
        order["clientOrderId"] = self.create_calls[-1]["params"]["clientOrderId"]
        raise ccxt.RequestTimeout("réponse perdue après acceptation")

    def fetch_closed_orders(self, symbol, since, limit, params):
        return [o for o in self.orders.values() if o.get("status") == "closed"]


async def test_lost_reply_is_recovered_by_client_id_after_restart_without_resending():
    exchange, journal = LostReply(), MemoryJournal()
    with pytest.raises(UncertainExecutionError):
        await broker(exchange, journal=journal).entry(
            decision(), D("100"), symbol=SYMBOL
        )
    restarted = broker(exchange, journal=journal)
    with pytest.raises(UncertainExecutionError, match="non rapprochée"):
        await restarted.entry(decision(), D("100"), symbol=SYMBOL)
    receipts = await restarted.recover_requests()
    assert len(receipts) == 1
    assert receipts[0][1].amount == D("0.05")
    assert len(exchange.create_calls) == 1
    assert journal.rows["test-0"]["state"] == "FILLED"


async def test_missing_response_id_remains_unknown_and_blocks_emergency_resend():
    exchange, journal = FakeExchange(), MemoryJournal()
    exchange.create_order = lambda *a, **kw: {}
    live = broker(exchange, journal=journal)
    with pytest.raises(UncertainExecutionError, match="sans identifiant"):
        await live.entry(decision(), D("100"), symbol=SYMBOL)
    with pytest.raises(UncertainExecutionError, match="non rapprochée"):
        await live.emergency_exit(D("0.05"), symbol=SYMBOL)
    assert journal.rows["test-0"]["state"] == "UNKNOWN"


async def test_no_journal_means_no_exchange_mutation():
    exchange = FakeExchange()
    live = LiveBroker(exchange, quote_currency="EUR", taker_fee=D(".008"))
    with pytest.raises(LiveExecutionError, match="durable manquant"):
        await live.entry(decision(), D("100"), symbol=SYMBOL)
    assert exchange.create_calls == []


async def test_poll_failure_keeps_the_acknowledged_order_for_reconciliation():
    exchange, journal = FakeExchange(order_status="open"), MemoryJournal()

    def unavailable(*a):
        raise ccxt.NetworkError("coupure pendant le polling")

    exchange.fetch_order = unavailable
    with pytest.raises(UncertainExecutionError, match="illisible après envoi"):
        await broker(exchange, journal=journal).entry(
            decision(), D("100"), symbol=SYMBOL
        )
    assert journal.rows["test-0"]["state"] == "UNKNOWN"
    assert journal.rows["test-0"]["exchange_order_id"] == "order-1"
    assert len(exchange.create_calls) == 1


async def test_lost_cancel_reply_is_recovered_without_repeating_the_cancel():
    exchange, journal = FakeExchange(), MemoryJournal()
    live = broker(exchange, journal=journal)
    fill = await live.entry(decision(), D("100"), symbol=SYMBOL)
    journal.rows["test-0"]["state"] = "BOOKED"
    original = exchange.cancel_order

    def lost_cancel(*a):
        original(*a)
        raise ccxt.RequestTimeout("réponse annulation perdue")

    exchange.cancel_order = lost_cancel
    with pytest.raises(LiveExecutionError, match="annulation"):
        await live.exit(fill.amount, D("100"), symbol=SYMBOL)
    assert journal.rows["test-1"]["state"] == "UNKNOWN"
    assert await live.recover_requests() == []
    assert journal.rows["test-1"]["state"] == "CONFIRMED"
    assert exchange.cancel_calls == [fill.stop_order_id]
    assert len(exchange.create_calls) == 1


async def test_partial_sale_preserves_the_fill_and_rearms_the_actual_residual():
    class Partial(PanicExchange):
        def create_order(self, symbol, type, side, amount, price=None, params=None):
            if type == "market" and side == "sell":
                amount = str(D(amount) / 2)
            return super().create_order(symbol, type, side, amount, price, params)

    exchange = Partial({"BTC": ".05", "EUR": "1"})
    fill = await broker(exchange).emergency_exit(
        D(".05"), symbol=SYMBOL, stop_price=D("95")
    )
    assert fill.amount == D(".025")
    assert fill.stop_order_id
    assert exchange.balances["BTC"] == D(".025")
    stop = exchange.orders[fill.stop_order_id]
    assert D(stop["amount"]) == D(".025")


async def test_stop_filled_during_cancellation_prevents_a_second_sale():
    exchange = FakeExchange(base_free=".05")
    exchange.open_orders = [
        {
            "id": "stop",
            "symbol": SYMBOL,
            "side": "sell",
            "type": "stop-loss",
            "stopLossPrice": "95",
            "amount": ".05",
        }
    ]
    original = exchange.cancel_order

    def stop_won(*a):
        result = original(*a)
        exchange.base_free = "0"
        return result

    exchange.cancel_order = stop_won
    with pytest.raises(UncertainExecutionError, match="aucune base disponible"):
        await broker(exchange).exit(D(".05"), D("100"), symbol=SYMBOL)
    assert exchange.create_calls == []


async def test_more_than_one_page_of_fills_is_fully_aggregated():
    exchange = FakeExchange()
    exchange.trades = [
        dict(
            id=str(i),
            order="stop",
            side="sell",
            amount=".001",
            price="100",
            timestamp=i + 1,
            fee={"cost": ".001", "currency": "EUR"},
        )
        for i in range(121)
    ]
    fills = await broker(exchange).sell_fills_since(SYMBOL)
    assert len(fills) == 1
    assert fills[0].amount == D(".121")
    assert fills[0].notional_quote == D("12.1")
    assert fills[0].fee_quote == D(".121")


async def test_repeated_page_is_an_error_instead_of_a_truncated_fill():
    exchange = FakeExchange()
    exchange.fetch_my_trades = lambda *a: [
        dict(
            id=str(i),
            order="stop",
            side="sell",
            amount=".001",
            price="100",
            timestamp=i + 1,
            symbol=SYMBOL,
        )
        for i in range(50)
    ]
    with pytest.raises(UncertainExecutionError, match="non progressive"):
        await broker(exchange).sell_fills_since(SYMBOL)


async def test_unknown_order_absent_from_history_stays_blocking():
    exchange, journal = FakeExchange(), MemoryJournal()
    await journal.prepare(SYMBOL, dict(type="market", side="buy", amount=".05"))
    exchange.fetch_closed_orders = lambda *a: []
    with pytest.raises(UncertainExecutionError, match="non retrouvé"):
        await broker(exchange, journal=journal).recover_requests()
    assert journal.rows["test-0"]["state"] == "UNKNOWN"
    assert exchange.create_calls == []


async def test_history_pages_for_other_symbols_do_not_truncate_the_target_symbol():
    exchange = FakeExchange()
    exchange.trades = [
        dict(
            id=str(i),
            order="eth-order",
            symbol="ETH/EUR",
            side="sell",
            amount="1",
            price="100",
            timestamp=i + 1,
        )
        for i in range(50)
    ]
    exchange.trades += [
        dict(
            id="btc-last",
            order="btc-order",
            symbol=SYMBOL,
            side="sell",
            amount=".05",
            price="100",
            timestamp=100,
        )
    ]
    calls = []
    original = exchange.fetch_my_trades

    def pages(symbol, since, limit, params):
        calls.append((symbol, params))
        return original(symbol, since, limit, params)

    exchange.fetch_my_trades = pages
    fills = await broker(exchange).sell_fills_since(SYMBOL)
    assert len(calls) == 2 and all(symbol is None for symbol, _ in calls)
    assert calls[0][1]["end"] == calls[1][1]["end"]
    assert len(fills) == 1 and fills[0].order_id == "btc-order"


async def test_lost_old_stop_cancel_reply_keeps_the_verified_new_protection():
    exchange, journal = FakeExchange(), MemoryJournal()
    live = broker(exchange, journal=journal)
    fill = await live.entry(decision(), D(100), symbol=SYMBOL)
    journal.rows["test-0"]["state"] = "BOOKED"
    original = exchange.cancel_order

    def lost(*args):
        original(*args)
        raise ccxt.RequestTimeout("réponse perdue")

    exchange.cancel_order = lost
    new_id = await live.update_stop_loss_order(
        SYMBOL, fill.amount, fill.stop_order_id, D(99)
    )
    assert new_id != fill.stop_order_id
    assert len(exchange.open_orders) == 1
    assert exchange.open_orders[0]["id"] == new_id
    assert D(exchange.open_orders[0]["stopLossPrice"]) == D(99)


async def test_unreadable_old_stop_after_cancel_keeps_new_protection_and_blocks_further_mutations():
    exchange, journal = FakeExchange(), MemoryJournal()
    live = broker(exchange, journal=journal)
    fill = await live.entry(decision(), D(100), symbol=SYMBOL)
    journal.rows["test-0"]["state"] = "BOOKED"
    original_read = exchange.fetch_order

    def lost_cancel(*args):
        raise ccxt.RequestTimeout("annulation peut encore arriver")

    def unreadable(order_id, symbol):
        if order_id == fill.stop_order_id and exchange.cancel_calls:
            raise ccxt.NetworkError("lecture indisponible")
        return original_read(order_id, symbol)

    def cancel(order_id, symbol):
        exchange.cancel_calls.append(order_id)
        return lost_cancel(order_id, symbol)

    exchange.cancel_order, exchange.fetch_order = cancel, unreadable
    with pytest.raises(UncertainExecutionError, match="non annulé"):
        await live.update_stop_loss_order(
            SYMBOL, fill.amount, fill.stop_order_id, D(99)
        )
    assert len(exchange.open_orders) == 2
    assert any(D(o["stopLossPrice"]) == D(99) for o in exchange.open_orders)
    count = len(exchange.create_calls)
    with pytest.raises(UncertainExecutionError, match="non rapprochée"):
        await live.update_stop_loss_order(
            SYMBOL, fill.amount, fill.stop_order_id, D(100)
        )
    assert len(exchange.create_calls) == count
