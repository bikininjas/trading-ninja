"""Rapproche les reçus durables sans renvoyer les ordres retrouvés."""

from decimal import Decimal
import uuid

from app.core.coordination import ledger_transaction
from app.execution.live import UncertainExecutionError


async def recover_executions(repo, broker, mode, quote, *, include_trades=False):
    recover = getattr(broker, "recover_requests", None)
    if recover is None:
        return 0
    receipts = await recover()
    count = 0
    for row, fill in receipts:
        async with ledger_transaction(repo):
            if row["request"]["side"] == "buy" and await repo.fill_recorded(
                mode, fill.order_id
            ):
                await repo.book_request(mode, fill.order_id)
                continue
            if row["request"]["side"] == "buy":
                existing = [
                    p
                    for p in await repo.open_positions(mode)
                    if p.symbol == row["symbol"]
                ]
                if existing:
                    raise UncertainExecutionError(
                        "achat retrouvé mais position déjà suivie : rapprochement manuel requis"
                    )
                stop = Decimal(
                    str(
                        row["request"]
                        .get("params", {})
                        .get("close", {})
                        .get("price", "0")
                    )
                )
                if stop <= 0:
                    raise UncertainExecutionError(
                        "achat retrouvé sans géométrie de stop prouvée"
                    )
                position_id = await repo.create_position(
                    mode,
                    symbol=row["symbol"],
                    entry_price=fill.price,
                    amount=fill.amount,
                    notional_quote=fill.notional_quote,
                    stop_loss_price=stop,
                    take_profit_price=None,
                    risk_amount_quote=max(
                        abs(fill.price - stop) * fill.amount, Decimal("0.0000000001")
                    ),
                    r_multiple_at_tp=None,
                    signal_id=None,
                    evaluation_id=None,
                    protective_order_id=None,
                    opened_at=row["created_at"],
                )
                await repo.insert_order(
                    mode,
                    client_order_id="recovered-" + uuid.uuid4().hex,
                    symbol=row["symbol"],
                    side="BUY",
                    order_type="MARKET",
                    purpose="ENTRY",
                    status="FILLED",
                    requested_amount=Decimal(row["request"]["amount"]),
                    filled_amount=fill.amount,
                    requested_price=None,
                    stop_price=None,
                    average_fill_price=fill.price,
                    fee_amount=fill.fee_quote,
                    fee_currency=quote,
                    slippage_quote=fill.slippage_quote,
                    position_id=position_id,
                    exchange_order_id=fill.order_id,
                )
                await repo.book_request(mode, fill.order_id)
            else:
                await repo.account_exit(
                    mode,
                    row["symbol"],
                    fill,
                    purpose="EMERGENCY_EXIT",
                    reason="RECOVERED_EXECUTION",
                    quote=quote,
                    taker_fee=broker.fee(Decimal(1)),
                    emergency=True,
                )
            count += 1
    # Les stops attachés sont créés par Kraken, hors des requêtes marché du bot.
    # Leurs fills partiels doivent aussi réduire la quantité suivie, une seule fois.
    if include_trades and hasattr(broker, "sell_fills_since"):
        for position in await repo.open_positions(mode):
            fills = await broker.sell_fills_since(
                position.symbol, since=int(position.opened_at.timestamp() * 1000)
            )
            for fill in fills:
                result = await repo.account_exit(
                    mode,
                    position.symbol,
                    fill,
                    purpose="STOP_LOSS",
                    reason="RECOVERED_EXECUTION",
                    quote=quote,
                    taker_fee=broker.fee(Decimal(1)),
                    emergency=True,
                )
                if result:
                    count += 1
    # A confirmed receipt is insufficient without a covering remote stop.
    for position in await repo.open_positions(mode):
        base = await broker.total_base(position.symbol)
        if base <= 0:
            continue
        if base != position.amount:
            raise UncertainExecutionError(
                "quantité distante non rapprochée : reprise interdite"
            )
        stop_id = await broker.ensure_protection(
            position.symbol, base, position.stop_loss_price
        )
        await repo.update_protective_stop(
            position.id, position.stop_loss_price, stop_id
        )
    if count:
        await repo.force_halt(
            mode, "exécutions rapprochées : vérifier le compte avant reprise manuelle"
        )
    return count
