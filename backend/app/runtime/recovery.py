"""Liquidation d'urgence — le seul chemin qui ferme une position sans pare-feu.

Un kill-switch n'est pas un trade : c'est un arrêt. Il ne demande donc pas
d'approbation au Risk Gateway (qui ne statue que sur les achats) et il fonctionne
même si le processus principal est arrêté, puisque tout passe par la base et par
des appels publics à l'exchange.

Le prix de sortie est le prix courant, moins le slippage et les frais taker du
broker : une liquidation n'est jamais gratuite, et le ledger doit le refléter.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from app.db.repository import PostgresRepository
from app.execution.paper import PaperBroker
from app.market.feed import last_price

__all__ = ["Liquidation", "liquidate_open_positions"]

EMERGENCY_REASON = "EMERGENCY_KILL"


@dataclass(frozen=True, slots=True)
class Liquidation:
    symbol: str
    amount: Decimal
    exit_price: Decimal
    net_pnl_quote: Decimal
    fees_quote: Decimal
    orders_canceled: int


async def liquidate_open_positions(
    repo: PostgresRepository,
    exchange: Any,
    broker: PaperBroker,
    mode: str,
    *,
    quote_currency: str,
    starting_equity: Decimal,
    reason: str = EMERGENCY_REASON,
    now: datetime | None = None,
) -> list[Liquidation]:
    """Solde toutes les positions ouvertes, annule leurs ordres, puis halte le bot."""
    moment = now or datetime.now(timezone.utc)
    account = await repo.account(mode, starting_equity)
    cash = account.free_quote
    reserve = account.reserve_quote
    consecutive = account.consecutive_losses
    results: list[Liquidation] = []

    while True:
        position = await repo.open_position(mode)
        if position is None:
            break

        price = last_price(exchange, position.symbol)
        fill = broker.exit(position.amount, price)
        entry_fee = broker.fee(position.entry_price * position.amount)
        gross = (fill.price - position.entry_price) * position.amount
        fees = entry_fee + fill.fee_quote
        net = gross - fees

        canceled = 0
        for order in await repo.open_orders(mode):
            if order["position_id"] == position.id or order["symbol"] == position.symbol:
                await repo.update_order_status(
                    order["id"], "CANCELED", f"{reason} : position liquidée"
                )
                canceled += 1

        await repo.insert_order(
            mode, client_order_id=f"{mode}-{position.symbol.replace('/', '')}-KILL-"
                                  f"{uuid.uuid4().hex[:8]}",
            symbol=position.symbol, side="SELL", order_type="MARKET",
            purpose="EMERGENCY_EXIT", status="FILLED",
            requested_amount=position.amount, filled_amount=position.amount,
            requested_price=None, stop_price=None, average_fill_price=fill.price,
            fee_amount=fill.fee_quote, fee_currency=quote_currency,
            slippage_quote=fill.slippage_quote, position_id=position.id,
        )
        await repo.close_position(
            position.id, exit_price=fill.price, closed_amount=position.amount,
            gross_pnl_quote=gross, total_fees_quote=fees, net_pnl_quote=net,
            exit_reason=reason, status="EMERGENCY_LIQUIDATED",
        )

        cash = cash + fill.notional_quote - fill.fee_quote
        consecutive = consecutive + 1 if net < 0 else 0
        await repo.update_after_close(
            mode, consecutive_losses=consecutive, equity=cash + reserve
        )
        await repo.snapshot_equity(
            mode, equity=cash + reserve, free_quote=cash,
            position_value_quote=Decimal(0), symbol=position.symbol, source="KILL_SWITCH",
        )
        results.append(Liquidation(
            symbol=position.symbol, amount=position.amount, exit_price=fill.price,
            net_pnl_quote=net, fees_quote=fees, orders_canceled=canceled,
        ))

    # Même sans position, un kill-switch est une panne volontaire : le bot doit
    # rester arrêté jusqu'à une intervention humaine.
    await repo.force_halt(mode, f"{reason} à {moment:%Y-%m-%d %H:%M:%S} UTC")
    return results
