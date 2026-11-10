"""Liquidation d'urgence — le seul chemin qui ferme une position sans pare-feu.

Un kill-switch n'est pas un trade : c'est un arrêt. Il ne demande donc pas
d'approbation au Risk Gateway (qui ne statue que sur les achats) et il fonctionne
même si le processus principal est arrêté, puisque tout passe par la base et par
des appels publics à l'exchange.

Le prix de sortie est le prix courant, moins le slippage et les frais taker du
broker : une liquidation n'est jamais gratuite, et le ledger doit le refléter.
"""

from __future__ import annotations

from app.core.coordination import serialized_financial

import uuid
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from inspect import isawaitable
from typing import Any

from app.engine.capital import CapitalPolicy
from app.db.repository import PostgresRepository
from app.market.feed import last_price

__all__ = [
    "Liquidation",
    "PanicLiquidationError",
    "PanicRecord",
    "liquidate_open_positions",
    "record_panic_exits",
]

EMERGENCY_REASON = "EMERGENCY_KILL"


class PanicLiquidationError(RuntimeError):
    """Une liquidation d'urgence a laissé une exposition ou une erreur.

    Les ventes réussies ont déjà été écrites en base et le bot est halte : cette
    exception ne sert qu'à rendre l'échec visible à l'appelant (API, dashboard).
    """

    def __init__(self, message: str, *, liquidated: list["PanicRecord"],
                 errors: list[str]) -> None:
        super().__init__(message)
        self.liquidated = liquidated
        self.errors = errors


async def _maybe_await(value: Any) -> Any:
    if isawaitable(value):
        return await value
    return value


@dataclass(frozen=True, slots=True)
class Liquidation:
    symbol: str
    amount: Decimal
    exit_price: Decimal
    net_pnl_quote: Decimal
    fees_quote: Decimal
    orders_canceled: int


@dataclass(frozen=True, slots=True)
class PanicRecord:
    """Vente d'urgence constatée chez l'exchange, écrite au ledger.

    ``tracked`` est faux quand le solde vendu n'avait pas de position en base :
    une urgence solde l'exchange d'abord, la comptabilité ensuite.
    """

    symbol: str
    amount: Decimal
    exit_price: Decimal
    net_pnl_quote: Decimal
    fees_quote: Decimal
    orders_canceled: int
    tracked: bool


@serialized_financial(halt_reason='urgence financière')
async def liquidate_open_positions(repo, exchange, broker, mode, **kwargs):
    # Partage le verrou du bot : un panic papier ne solde pas la même position
    # pendant qu'un cycle normal l'évalue. Les doubles historiques n'ont pas de verrou.
    lock = repo.cycle_lock(mode) if hasattr(repo, 'cycle_lock') else nullcontext()
    async with lock:
        return await _liquidate_open_positions(repo, exchange, broker, mode, **kwargs)


async def _liquidate_open_positions(
    repo: PostgresRepository,
    exchange: Any,
    broker: Any,
    mode: str,
    *,
    quote_currency: str,
    starting_equity: Decimal,
    reason: str = EMERGENCY_REASON,
    now: datetime | None = None,
    only_symbols: set[str] | None = None,
    capital_policy: CapitalPolicy | None = None,
) -> list[Liquidation]:
    """Solde toutes les positions ouvertes, annule leurs ordres, puis halte le bot."""
    ensure = getattr(repo, "ensure_mode", None)
    if ensure is not None:
        await ensure(mode)
    moment = now or datetime.now(timezone.utc)
    account = await repo.account(mode, starting_equity)
    live = hasattr(broker, "free_quote")
    reserve = account.reserve_quote
    growth = account.growth_quote
    cash = ((await _maybe_await(broker.free_quote())) - reserve - growth) if live else account.free_quote
    capital = await repo.capital_state(mode) if hasattr(repo, 'capital_state') else None
    if capital is not None and (capital_policy is None or not capital_policy.enabled):
        raise ValueError("politique de capital requise pour la liquidation de cette enveloppe")
    consecutive = account.consecutive_losses
    results: list[Liquidation] = []

    positions = await repo.open_positions(mode)
    if only_symbols is not None:
        positions = [p for p in positions if p.symbol in only_symbols]
    for position in positions:
        price = last_price(exchange, position.symbol)
        fill = await _maybe_await(
            broker.exit(
                position.amount, price, symbol=position.symbol,
                stop_price=position.stop_loss_price,
            )
        )
        if live and hasattr(repo,'account_exit'):
            result=await repo.account_exit(mode,position.symbol,fill,purpose='EMERGENCY_EXIT',reason=reason,
                quote=quote_currency,taker_fee=broker.fee(Decimal(1)),emergency=True)
            free=await broker.free_quote()
            remaining=result['remaining'] if result else Decimal(0)
            await repo.snapshot_equity(mode,equity=free+remaining*price,free_quote=max(Decimal(0),free-reserve-growth),
                position_value_quote=remaining*price,symbol=position.symbol,source='EMERGENCY_EXIT')
            results.append(Liquidation(position.symbol,fill.amount,fill.price,result['net'] if result else Decimal(0),result['fees'] if result else fill.fee_quote,0))
            await repo.force_halt(mode,'liquidation réelle : vérification humaine requise')
            if remaining>0:raise PanicLiquidationError('liquidation partielle, reliquat protégé à contrôler',liquidated=results,errors=['reliquat non nul'])
            continue
        getter = getattr(repo, "entry_fee", None)
        entry_fee = await _maybe_await(getter(position.id)) if getter else None
        if entry_fee is None or entry_fee <= 0:
            entry_fee = broker.fee(position.entry_price * position.amount)
        gross = (fill.price - position.entry_price) * position.amount
        fees = entry_fee + fill.fee_quote
        net = gross - fees

        transaction = repo.transaction() if hasattr(repo, 'transaction') else nullcontext()
        async with transaction:
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

            if capital is not None:
                net = net.quantize(Decimal('0.0000000001'))
                capital = await repo.allocate_capital(mode, position.id, net, moment, capital_policy, allow_promotion=False)
                cash, reserve, growth = capital.active_quote, capital.reserve_quote, capital.growth_quote
            else:
                cash = cash + fill.notional_quote - fill.fee_quote
            if live:
                cash = await _maybe_await(broker.free_quote()) - reserve - growth
            consecutive = consecutive + 1 if net < 0 else 0
            await repo.update_after_close(
                mode, consecutive_losses=consecutive, equity=cash + reserve + growth
            )
            await repo.snapshot_equity(
                mode, equity=cash + reserve + growth, free_quote=cash,
                position_value_quote=Decimal(0), symbol=position.symbol, source="KILL_SWITCH",
            )
            results.append(Liquidation(
                symbol=position.symbol, amount=position.amount, exit_price=fill.price,
                net_pnl_quote=net, fees_quote=fees, orders_canceled=canceled,
            ))

            await repo.force_halt(mode, f"{reason} à {moment:%Y-%m-%d %H:%M:%S} UTC")

    # Même sans position, un kill-switch est une panne volontaire : le bot doit
    # rester arrêté jusqu'à une intervention humaine.
    await repo.force_halt(mode, f"{reason} à {moment:%Y-%m-%d %H:%M:%S} UTC")
    return results


@serialized_financial(halt_reason='urgence financière')
async def record_panic_exits(
    repo: PostgresRepository,
    exits: Any,
    mode: str,
    *,
    quote_currency: str,
    starting_equity: Decimal,
    free_quote_after: Decimal | None,
    taker_fee: Decimal,
    errors: list[str] | None = None,
    reason: str = EMERGENCY_REASON,
    now: datetime | None = None,
) -> list[PanicRecord]:
    """Écrit au ledger les ventes réelles d'un panic-sell, puis halte le bot.

    Contrairement à ``liquidate_open_positions``, la vente a **déjà eu lieu**
    chez l'exchange : le prix et les frais viennent du fill, jamais d'une
    estimation. Un solde vendu sans position en base produit quand même une
    ligne d'ordre, sinon l'argent apparaîtrait de nulle part dans le ledger.
    """
    ensure = getattr(repo, "ensure_mode", None)
    if ensure is not None:
        await ensure(mode)
    moment = now or datetime.now(timezone.utc)
    account = await repo.account(mode, starting_equity)
    reserve = account.reserve_quote
    consecutive = account.consecutive_losses
    open_positions = await repo.open_positions(mode)
    by_symbol = {position.symbol: position for position in open_positions}
    records: list[PanicRecord] = []

    for item in exits:
        fill = getattr(item, "fill", None)
        if fill is None:
            continue
        if mode == "LIVE" and hasattr(repo, "account_exit"):
            result = await repo.account_exit(
                mode, str(item.symbol), fill, purpose="EMERGENCY_EXIT", reason=reason,
                quote=quote_currency, taker_fee=taker_fee, emergency=True,
            )
            if result is not None:
                records.append(PanicRecord(
                    symbol=str(item.symbol), amount=fill.amount, exit_price=fill.price,
                    net_pnl_quote=result["net"], fees_quote=result["fees"],
                    orders_canceled=0, tracked=result["tracked"],
                ))
            continue
        symbol = str(item.symbol)
        position = by_symbol.pop(symbol, None)
        closed_amount = min(fill.amount, position.amount) if position else Decimal(0)
        net = Decimal(0)
        fees = fill.fee_quote
        canceled = 0
        position_id = None

        if position is not None:
            position_id = position.id
            getter = getattr(repo, "entry_fee", None)
            entry_fee = await _maybe_await(getter(position.id)) if getter else None
            if entry_fee is None or entry_fee <= 0:
                entry_fee = position.entry_price * position.amount * taker_fee
            gross = (fill.price - position.entry_price) * closed_amount
            fees = entry_fee + fill.fee_quote
            net = gross - fees
            for order in await repo.open_orders(mode):
                if order["position_id"] == position.id or order["symbol"] == position.symbol:
                    await repo.update_order_status(
                        order["id"], "CANCELED", f"{reason} : panic-sell"
                    )
                    canceled += 1
            await repo.close_position(
                position.id, exit_price=fill.price, closed_amount=closed_amount,
                gross_pnl_quote=gross, total_fees_quote=fees, net_pnl_quote=net,
                exit_reason=reason, status="EMERGENCY_LIQUIDATED",
            )
            consecutive = consecutive + 1 if net < 0 else 0

        await repo.insert_order(
            mode, client_order_id=f"{mode}-{symbol.replace('/', '')}-PANIC-"
                                  f"{uuid.uuid4().hex[:8]}",
            symbol=symbol, side="SELL", order_type="MARKET",
            purpose="EMERGENCY_EXIT", status="FILLED",
            requested_amount=item.amount, filled_amount=fill.amount,
            requested_price=None, stop_price=None, average_fill_price=fill.price,
            fee_amount=fill.fee_quote, fee_currency=quote_currency,
            slippage_quote=fill.slippage_quote, position_id=position_id,
        )
        records.append(PanicRecord(
            symbol=symbol, amount=fill.amount, exit_price=fill.price,
            net_pnl_quote=net, fees_quote=fees, orders_canceled=canceled,
            tracked=position is not None,
        ))

    # Un solde illisible ne doit pas écrire une équité fausse (0 EUR) : on halte
    # et on signale, mais on laisse la dernière valeur connue intacte.
    durable_live = mode == "LIVE" and hasattr(repo, "account_exit")
    if durable_live:
        # account_exit a déjà appliqué les pertes cumulatives. Ne pas les écraser
        # avec le compte lu avant les ventes ; un reliquat n'est pas du cash nul.
        consecutive = (await repo.account(mode, starting_equity)).consecutive_losses
        residual_positions = await repo.open_positions(mode)
    else:
        residual_positions = []
    if free_quote_after is not None and not residual_positions:
        equity = free_quote_after
        await repo.update_after_close(
            mode, consecutive_losses=consecutive, equity=equity
        )
        await repo.snapshot_equity(
            mode, equity=equity, free_quote=max(Decimal(0), free_quote_after - reserve),
            position_value_quote=Decimal(0), symbol=None, source="PANIC_SELL",
        )

    # Un panic-sell est un arrêt : le bot ne repart pas sans un humain.
    await repo.force_halt(mode, f"{reason} (panic-sell) à {moment:%Y-%m-%d %H:%M:%S} UTC")

    failures = list(errors or ())
    if failures:
        raise PanicLiquidationError(
            "panic-sell incomplet : " + " | ".join(failures),
            liquidated=records, errors=failures,
        )
    return records
