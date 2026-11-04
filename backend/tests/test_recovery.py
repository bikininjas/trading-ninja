"""Tests du kill-switch : liquidation, annulation des ordres, halte forcée."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.db.repository import AccountSnapshot, PositionRow
from app.execution.paper import PaperBroker
from app.runtime import recovery
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
