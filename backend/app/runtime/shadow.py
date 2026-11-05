"""Mesure des achats que le bot n'a pas pris.

Un *shadow trade* est un plan d'achat contrefactuel : le modèle a dit BUY et le
pare-feu a refusé, ou le modèle a dit HOLD alors que la règle d'entrée avait
déclenché. Le plan est dimensionné par le **même** `evaluate()`, avec une confiance
forcée à 1,0, puis laissé en attente. Ce module rejoue ensuite les bougies H4
suivantes pour savoir ce qui se serait passé.

Invariants :

- aucune écriture d'ordre, aucune position, aucun argent ;
- le stop est prioritaire sur le take-profit, comme dans `PaperBroker` ;
- une ligne dont le plan forcé est refusé reste `NO_PLAN` : on ne simule pas un
  trade que le pare-feu n'aurait jamais laissé passer ;
- l'évaluation est idempotente : seules les lignes `OPEN` sont traitées, puis
  marquées `STOP_LOSS`, `TAKE_PROFIT` ou `END_OF_DATA`.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from app.core.settings import Settings
from app.engine.risk_gate import RiskDecision
from app.exchange.kraken import resolve_taker_fee
from app.execution.paper import PaperBroker
from app.market.feed import TIMEFRAME_MS

__all__ = ["ShadowOutcome", "evaluate_open_shadow_trades", "simulate_shadow"]


@dataclass(frozen=True, slots=True)
class ShadowOutcome:
    status: str
    exit_at: datetime
    exit_price: Decimal
    pnl_quote: Decimal
    outcome_reason: str


def _close_time(candle: list, duration_ms: int) -> datetime:
    return datetime.fromtimestamp((candle[0] + duration_ms) / 1000, tz=timezone.utc)


def simulate_shadow(
    candles: list[list],
    *,
    entry_at: datetime,
    entry_price: Decimal,
    amount: Decimal,
    stop_loss_price: Decimal,
    take_profit_price: Decimal | None,
    broker: PaperBroker,
    timeframe: str = "4h",
) -> ShadowOutcome | None:
    """Rejoue les bougies postérieures à `entry_at` et rend le résultat simulé.

    `candles` doit contenir uniquement des bougies **clôturées**, triées par temps
    d'ouverture. Le premier prix d'entrée est le prix observé au moment du refus ;
    le slippage et les frais sont ceux du broker papier.
    """
    duration_ms = TIMEFRAME_MS[timeframe]
    entry_ms = int(entry_at.timestamp() * 1000)
    window = [candle for candle in candles if candle[0] + duration_ms > entry_ms]
    if not window:
        return None

    decision = RiskDecision(
        approved=True,
        amount=amount,
        notional_quote=amount * entry_price,
        stop_loss_price=stop_loss_price,
        take_profit_price=take_profit_price,
    )
    entry = broker.entry(decision, entry_price)

    for candle in window:
        trigger = broker.exit_trigger(stop_loss_price, take_profit_price, candle)
        if trigger is not None:
            price, reason = trigger
            exit_fill = broker.exit(amount, price)
            pnl = (
                exit_fill.notional_quote
                - entry.notional_quote
                - entry.fee_quote
                - exit_fill.fee_quote
            )
            return ShadowOutcome(
                status=reason,
                exit_at=_close_time(candle, duration_ms),
                exit_price=exit_fill.price,
                pnl_quote=pnl,
                outcome_reason=reason,
            )

    last = window[-1]
    price = Decimal(str(last[4]))
    exit_fill = broker.exit(amount, price)
    pnl = (
        exit_fill.notional_quote
        - entry.notional_quote
        - entry.fee_quote
        - exit_fill.fee_quote
    )
    return ShadowOutcome(
        status="END_OF_DATA",
        exit_at=_close_time(last, duration_ms),
        exit_price=exit_fill.price,
        pnl_quote=pnl,
        outcome_reason="fin des bougies disponibles",
    )


async def evaluate_open_shadow_trades(
    repo, exchange: Any, settings: Settings, *, limit: int = 200,
) -> int:
    """Ferme les shadow trades `OPEN` en rejouant les bougies disponibles.

    Retourne le nombre de lignes évaluées. Une ligne sans bougie postérieure reste
    `OPEN` : le prochain passage la traitera.
    """
    rows = await repo.open_shadow_trades(settings.execution_mode, limit)
    if not rows:
        return 0

    await asyncio.to_thread(exchange.load_markets)
    timeframe = settings.exchange.daily_timeframe_decision
    duration_ms = TIMEFRAME_MS[timeframe]
    now_ms = exchange.milliseconds()
    fees: dict[str, Decimal] = {}
    closed = 0

    for row in rows:
        amount = row.get("amount")
        stop = row.get("stop_loss_price")
        if amount is None or stop is None:
            continue

        symbol = row["symbol"]
        if symbol not in fees:
            fees[symbol] = resolve_taker_fee(
                exchange, symbol, settings.exchange.taker_fee
            )

        candles = await asyncio.to_thread(
            exchange.fetch_ohlcv, symbol, timeframe, None, 720
        )
        candles = [
            candle for candle in candles if candle[0] + duration_ms <= now_ms
        ]
        broker = PaperBroker(fees[symbol], settings.paper_slippage_bps)
        outcome = simulate_shadow(
            candles,
            entry_at=row["created_at"],
            entry_price=row["decision_price"],
            amount=amount,
            stop_loss_price=stop,
            take_profit_price=row.get("take_profit_price"),
            broker=broker,
            timeframe=timeframe,
        )
        if outcome is None:
            continue

        await repo.close_shadow_trade(
            row["id"],
            status=outcome.status,
            exit_price=outcome.exit_price,
            pnl_quote=outcome.pnl_quote,
            outcome_reason=outcome.outcome_reason,
            evaluated_at=datetime.now(timezone.utc),
        )
        closed += 1
    return closed
