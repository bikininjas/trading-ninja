"""Évaluation prospective de plans fixes, sans connaissance des prix futurs.

Les coûts, la période et l'horizon sont figés à la décision. Une bougie d'entrée
partielle touchant une sortie est ambiguë : aucun gain/perte n'est inventé.
Une fin de téléchargement n'est jamais une clôture. Stop prioritaire si les deux
niveaux sont touchés dans une même bougie. Ce laboratoire ne simule pas le trailing.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import asyncio

from app.engine.risk_gate import RiskDecision
from app.execution.paper import PaperBroker
from app.market.feed import TIMEFRAME_MS

VERSION = 'forward-v1'
HORIZON_BARS = 42


@dataclass(frozen=True)
class TrainingOutcome:
    status: str
    exit_at: datetime
    exit_price: Decimal | None
    pnl_quote: Decimal | None
    reason: str
    eligible: bool


def simulate_training(row, candles, now):
    context = row['simulation_context']
    if context.get('version') != VERSION:
        raise ValueError('contexte prospectif absent')
    period = TIMEFRAME_MS[context['timeframe']]
    observed = datetime.fromisoformat(context['observed_at'])
    if observed.tzinfo is None:
        raise ValueError('date de décision sans fuseau')
    entry_ms = int(observed.timestamp()*1000)
    start = entry_ms // period * period
    horizon = int(context['horizon_bars'])
    if not 1 <= horizon <= 100:
        raise ValueError('horizon de simulation invalide')
    end = start + horizon * period
    now_ms = int(now.timestamp()*1000)
    window = {}
    for candle in candles:
        stamp = int(candle[0])
        if start <= stamp < end and stamp+period <= now_ms:
            if stamp in window and window[stamp] != candle:
                raise ValueError('bougies contradictoires')
            values = [Decimal(str(v)) for v in candle[1:5]]
            if len(values)!=4 or any(not v.is_finite() or v<=0 for v in values):
                raise ValueError('prix invalides')
            op, hi, lo, close = values
            if hi < max(op,close,lo) or lo > min(op,close,hi):
                raise ValueError('OHLC incohérent')
            window[stamp] = candle
    def unknown(reason):
        return TrainingOutcome('END_OF_DATA', now, None, None, reason, False)
    if not window:
        if now_ms >= end and candles:
            return unknown('INCOMPLETE_HISTORY')
        return None
    stop, take = row['stop_loss_price'], row.get('take_profit_price')
    amount, price = row['amount'], row['decision_price']
    broker = PaperBroker(Decimal(context['taker_fee']), int(context['slippage_bps']))
    decision = RiskDecision(approved=True,amount=amount,notional_quote=amount*price,
                            stop_loss_price=stop,take_profit_price=take)
    entry = broker.entry(decision,price)
    def close_at(candle, value, status):
        fill = broker.exit(amount,value)
        pnl = fill.notional_quote-entry.notional_quote-entry.fee_quote-fill.fee_quote
        at = datetime.fromtimestamp((int(candle[0])+period)/1000,timezone.utc)
        return TrainingOutcome(status,at,fill.price,pnl,
                               'HORIZON' if status=='END_OF_DATA' else status,True)
    for stamp in range(start,end,period):
        candle = window.get(stamp)
        if candle is None:
            return unknown('INCOMPLETE_HISTORY') if now_ms >= end else None
        if stamp == start and entry_ms != start:
            # Le haut/bas peut précéder l'achat fictif : pas de label inventé.
            if Decimal(str(candle[3]))<=stop or (take is not None and Decimal(str(candle[2]))>=take):
                return unknown('AMBIGUOUS_ENTRY_BAR')
        else:
            triggered = broker.exit_trigger(stop,take,candle)
            if triggered:
                value,reason = triggered
                return close_at(candle,value,reason)
        if stamp+period == end:
            return close_at(candle,Decimal(str(candle[4])),'END_OF_DATA')
    return None


async def evaluate_training_shadows(repo, exchange, *, now, limit=100):
    rows = await repo.training_shadows(limit)
    cache = {}
    evaluated = 0
    for row in rows:
        context = row['simulation_context']
        key = (row['symbol'],context['timeframe'])
        if key not in cache:
            # Uniquement OHLC public ; aucun accès aux fonds ou aux ordres.
            cache[key] = await asyncio.to_thread(exchange.fetch_ohlcv,*key,None,720)
        outcome = simulate_training(row,cache[key],now)
        await repo.update_training_shadow(row,outcome)
        evaluated += outcome is not None
    return evaluated
