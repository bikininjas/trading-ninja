"""Tests du kill-switch : liquidation, annulation des ordres, halte forcée."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.db.repository import AccountSnapshot, PositionRow
from app.execution.paper import PaperBroker
from app.runtime import recovery
from app.runtime.reconciliation import ReconciliationPlan, apply_reconciliation
from app.runtime.recovery import liquidate_open_positions

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)


def position(symbol: str = "BTC/EUR") -> PositionRow:
    return PositionRow(
        id=uuid.uuid4(), symbol=symbol, entry_price=Decimal("100"),
        amount=Decimal("0.046"), stop_loss_price=Decimal("96"),
        take_profit_price=Decimal("105"), opened_at=NOW, risk_amount_quote=Decimal("0.115"),
    )


class FakeRepo:
    def __init__(self, pos: PositionRow | None):
        self.pos = pos
        self.canceled: list[tuple] = []
        self.orders: list[dict] = []
        self.inserted: list[dict] = []
        self.closed: dict | None = None
        self.after: dict | None = None
        self.snapshots: list[dict] = []
        self.halted: str | None = None
        if pos is not None:
            self.orders.append({"id": uuid.uuid4(), "position_id": pos.id,
                                "symbol": pos.symbol, "purpose": "STOP_LOSS",
                                "status": "SUBMITTED"})

    async def account(self, mode, starting):
        return AccountSnapshot(
            equity=Decimal("5.32"), free_quote=Decimal("5.32"), hwm=Decimal("9.37"),
            daily_drawdown_pct=Decimal(0), weekly_drawdown_pct=Decimal(0),
            from_hwm_drawdown_pct=Decimal(0), consecutive_losses=0, orders_last_hour=0,
            has_open_position=self.pos is not None, reserve_quote=Decimal("0.50"),
        )

    async def open_position(self, mode):
        pos, self.pos = self.pos, None
        return pos

    async def open_positions(self, mode):
        pos, self.pos = self.pos, None
        return [pos] if pos is not None else []

    async def open_orders(self, mode): return list(self.orders)

    async def update_order_status(self, order_id, status, error_message=None):
        self.canceled.append((order_id, status, error_message))

    async def insert_order(self, mode, **kwargs):
        self.inserted.append(kwargs)
        return uuid.uuid4()

    async def close_position(self, position_id, **kwargs):
        self.closed = kwargs

    async def update_after_close(self, mode, **kwargs):
        self.after = kwargs

    async def snapshot_equity(self, mode, **kwargs):
        self.snapshots.append(kwargs)

    async def force_halt(self, mode, reason, code=None):
        self.halted = reason


@pytest.fixture(autouse=True)
def _price(monkeypatch):
    monkeypatch.setattr(recovery, "last_price", lambda exchange, symbol: Decimal("90"))


@pytest.mark.asyncio
async def test_a_position_is_liquidated_and_the_bot_halted():
    pos = position()
    repo = FakeRepo(pos)
    results = await liquidate_open_positions(
        repo, exchange=object(), broker=PaperBroker(Decimal("0.0026"), 5), mode="PAPER",
        quote_currency="EUR", starting_equity=Decimal("9.37"), now=NOW,
    )

    assert len(results) == 1
    assert results[0].symbol == "BTC/EUR"
    assert results[0].net_pnl_quote < 0
    assert repo.closed["status"] == "EMERGENCY_LIQUIDATED"
    assert repo.closed["exit_reason"] == "EMERGENCY_KILL"
    assert repo.inserted[0]["purpose"] == "EMERGENCY_EXIT"
    assert repo.inserted[0]["status"] == "FILLED"
    assert repo.canceled[0][1] == "CANCELED"
    assert repo.halted is not None
    # La réserve reste intacte : une liquidation d'urgence n'écrème pas.
    assert repo.snapshots[0]["equity"] == repo.snapshots[0]["free_quote"] + Decimal("0.50")


@pytest.mark.asyncio
async def test_the_kill_switch_halts_even_without_a_position():
    repo = FakeRepo(None)
    results = await liquidate_open_positions(
        repo, exchange=object(), broker=PaperBroker(Decimal("0.0026"), 5), mode="PAPER",
        quote_currency="EUR", starting_equity=Decimal("9.37"), now=NOW,
    )
    assert results == []
    assert repo.halted is not None
    assert repo.closed is None


@pytest.mark.asyncio
async def test_emergency_exit_pays_fees_and_slippage():
    repo = FakeRepo(position())
    await liquidate_open_positions(
        repo, exchange=object(), broker=PaperBroker(Decimal("0.0026"), 5), mode="PAPER",
        quote_currency="EUR", starting_equity=Decimal("9.37"), now=NOW,
    )
    # 90 * (1 - 5 bps) = 89,955 : la liquidation n'est pas gratuite.
    assert repo.closed["exit_price"] == Decimal("89.95500")


@pytest.mark.asyncio
async def test_apply_reconciliation_liquidates_the_planned_symbol():
    plan = ReconciliationPlan(
        mode="LIVE", quote_currency="EUR", positions=1, remote_orders=0,
        quote_balance=Decimal("9.37"), findings=(), liquidate=("BTC/EUR",),
    )
    repo = FakeRepo(position())

    actions = await apply_reconciliation(
        plan, repo, exchange=object(), broker=PaperBroker(Decimal("0.0026"), 5),
        starting_equity=Decimal("9.37"), now=NOW,
    )

    assert actions and "liquidé BTC/EUR" in actions[0]
    assert repo.closed["exit_reason"] == "RECONCILIATION"
    assert repo.halted is not None


@pytest.mark.asyncio
async def test_apply_reconciliation_halts_without_liquidating_on_a_simple_alert():
    plan = ReconciliationPlan(
        mode="LIVE", quote_currency="EUR", positions=0, remote_orders=1,
        quote_balance=Decimal("9.37"), findings=(), halt=True,
    )
    repo = FakeRepo(None)

    actions = await apply_reconciliation(
        plan, repo, exchange=object(), broker=PaperBroker(Decimal("0.0026"), 5),
        starting_equity=Decimal("9.37"), now=NOW,
    )

    assert actions == ["halte forcée : écart base/exchange"]
    assert repo.halted is not None
    assert repo.closed is None


def test_apply_reconciliation_refuses_paper_mode():
    plan = ReconciliationPlan(
        mode="PAPER", quote_currency="EUR", positions=0, remote_orders=0,
        quote_balance=None, findings=(),
    )
    with pytest.raises(ValueError, match="LIVE"):
        asyncio.run(apply_reconciliation(
            plan, FakeRepo(None), exchange=object(),
            broker=PaperBroker(Decimal("0.0026"), 5),
            starting_equity=Decimal("9.37"), now=NOW,
        ))


@pytest.mark.asyncio
async def test_panic_exits_write_the_real_fill_to_the_ledger():
    from types import SimpleNamespace

    from app.execution.paper import Fill
    from app.runtime.recovery import record_panic_exits

    pos = position()
    repo = FakeRepo(pos)
    fill = Fill(
        price=Decimal("90"), amount=Decimal("0.046"),
        notional_quote=Decimal("4.14"), fee_quote=Decimal("0.03312"),
        slippage_quote=Decimal("0.0046"), order_id="panic-1",
    )
    exits = [SimpleNamespace(symbol="BTC/EUR", amount=Decimal("0.046"), fill=fill)]

    records = await record_panic_exits(
        repo, exits, "LIVE", quote_currency="EUR",
        starting_equity=Decimal("9.37"), free_quote_after=Decimal("5.00"),
        taker_fee=Decimal("0.008"), now=NOW,
    )

    assert len(records) == 1
    assert records[0].tracked is True
    assert records[0].exit_price == Decimal("90")
    assert repo.closed["status"] == "EMERGENCY_LIQUIDATED"
    assert repo.inserted[0]["purpose"] == "EMERGENCY_EXIT"
    assert repo.inserted[0]["position_id"] == pos.id
    assert repo.snapshots[0]["source"] == "PANIC_SELL"
    assert repo.halted is not None


@pytest.mark.asyncio
async def test_panic_exits_record_an_untracked_balance_and_surface_errors():
    from types import SimpleNamespace

    from app.execution.paper import Fill
    from app.runtime.recovery import PanicLiquidationError, record_panic_exits

    repo = FakeRepo(None)
    fill = Fill(
        price=Decimal("90"), amount=Decimal("0.010"),
        notional_quote=Decimal("0.90"), fee_quote=Decimal("0.0072"),
        slippage_quote=Decimal("0"), order_id="panic-2",
    )
    exits = [SimpleNamespace(symbol="BTC/EUR", amount=Decimal("0.010"), fill=fill)]

    with pytest.raises(PanicLiquidationError) as caught:
        await record_panic_exits(
            repo, exits, "LIVE", quote_currency="EUR",
            starting_equity=Decimal("9.37"), free_quote_after=Decimal("5.00"),
            taker_fee=Decimal("0.008"), errors=["ETH/EUR : vente refusée"], now=NOW,
        )

    assert caught.value.liquidated[0].tracked is False
    assert repo.inserted[0]["position_id"] is None
    assert repo.closed is None
    assert repo.halted is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("remaining", [Decimal(0), Decimal("0.02")])
async def test_durable_panic_reports_fills_without_resetting_losses_or_erasing_residual(remaining):
    from dataclasses import replace
    from types import SimpleNamespace

    from app.execution.paper import Fill
    from app.runtime.recovery import record_panic_exits

    class DurableRepo(FakeRepo):
        booked = False

        async def account(self, mode, starting):
            account = await super().account(mode, starting)
            return replace(account, consecutive_losses=3 if self.booked else 2)

        async def open_positions(self, mode):
            return [self.pos] if self.pos and (not self.booked or remaining) else []

        async def account_exit(self, mode, symbol, fill, **kwargs):
            self.booked = True
            return dict(net=Decimal("-0.20"), fees=Decimal("0.05"),
                        tracked=True, remaining=remaining)

    repo = DurableRepo(position())
    fill = Fill(price=Decimal("90"), amount=Decimal("0.026"),
                notional_quote=Decimal("2.34"), fee_quote=Decimal("0.02"),
                slippage_quote=Decimal(0), order_id="durable-panic")
    records = await record_panic_exits(
        repo, [SimpleNamespace(symbol="BTC/EUR", fill=fill)], "LIVE",
        quote_currency="EUR", starting_equity=Decimal("9.37"),
        free_quote_after=Decimal("5"), taker_fee=Decimal("0.008"), now=NOW,
    )
    assert len(records) == 1
    assert records[0].amount == fill.amount
    assert records[0].net_pnl_quote == Decimal("-0.20")
    assert records[0].tracked
    assert not repo.inserted  # Le journal durable est l'unique comptable.
    if remaining:
        assert repo.snapshots == []
        assert repo.after is None
    else:
        assert repo.after["consecutive_losses"] == 3
        assert repo.snapshots[0]["equity"] == Decimal(5)
    assert repo.halted is not None
