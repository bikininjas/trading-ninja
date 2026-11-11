"""Simulation prospective : contexte figé, prix causaux et erreurs isolées.

Une bougie d'entrée partielle touchant une sortie reste ambiguë. Une fin de
téléchargement n'est pas une clôture. Seul le préfixe jusqu'à la sortie compte :
les prix ou volumes postérieurs ne peuvent pas invalider un résultat déjà établi.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import asyncio

from app.engine.risk_gate import RiskDecision
from app.execution.paper import PaperBroker
from app.market.feed import TIMEFRAME_MS

VERSION = 'forward-v1'
HORIZON_BARS = 42


class InvalidProposal(ValueError):
    """Le plan enregistré n'est pas exploitable ; aucun PnL ne doit être inventé."""


class SimulationDataUnavailable(RuntimeError):
    """Certains prix manquent ; les autres simulations ont été traitées."""
    def __init__(self, evaluated_count, failed_count):
        super().__init__('données de simulation indisponibles')
        self.evaluated_count = evaluated_count
        self.failed_count = failed_count


@dataclass(frozen=True)
class TrainingOutcome:
    status: str
    exit_at: datetime
    exit_price: Decimal | None
    pnl_quote: Decimal | None
    reason: str
    eligible: bool


@dataclass(frozen=True)
class TrainingPlan:
    timeframe: str
    observed: datetime
    horizon: int
    price: Decimal
    amount: Decimal
    stop: Decimal
    take: Decimal | None
    fee: Decimal
    slippage: int


def _decimal(value):
    if isinstance(value, bool):
        raise ValueError('nombre attendu')
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError('nombre non fini')
    return result


def _integer(value):
    result = _decimal(value)
    if result != result.to_integral_value():
        raise ValueError('entier attendu')
    return int(result)


def training_plan(row):
    try:
        context = row['simulation_context']
        if context.get('version') != VERSION:
            raise ValueError('contexte prospectif absent')
        timeframe = context['timeframe']
        if timeframe not in TIMEFRAME_MS:
            raise ValueError('période inconnue')
        observed = datetime.fromisoformat(context['observed_at'])
        if observed.tzinfo is None:
            raise ValueError('date sans fuseau')
        horizon = _integer(context['horizon_bars'])
        price, amount, stop = (_decimal(row[key]) for key in ['decision_price','amount','stop_loss_price'])
        take = _decimal(row['take_profit_price']) if row.get('take_profit_price') is not None else None
        fee, slippage = _decimal(context['taker_fee']), _integer(context['slippage_bps'])
        if not (1 <= horizon <= 100 and amount > 0 and 0 < stop < price
                and (take is None or take > price) and 0 <= fee < 1 and 0 <= slippage < 10_000):
            raise ValueError('plan incohérent')
        if not isinstance(row['symbol'], str) or not row['symbol']:
            raise ValueError('paire absente')
        return TrainingPlan(timeframe,observed,horizon,price,amount,stop,take,fee,slippage)
    except (KeyError, TypeError, AttributeError, ValueError, InvalidOperation, OverflowError) as exc:
        raise InvalidProposal('plan de simulation invalide') from exc


def _bar(candle):
    try:
        values = [_decimal(value) for value in candle[1:5]]
        if len(values) != 4 or any(value <= 0 for value in values):
            raise ValueError('prix invalides')
        op, hi, lo, close = values
        if hi < max(op,close,lo) or lo > min(op,close,hi):
            raise ValueError('OHLC incohérent')
        return [candle[0], *values]
    except (TypeError, InvalidOperation) as exc:
        raise ValueError('OHLC invalide') from exc


def simulate_training(row, candles, now):
    plan = training_plan(row)
    if not isinstance(candles,(list,tuple)):
        raise ValueError('liste de bougies attendue')
    if now.tzinfo is None:
        raise ValueError('horloge sans fuseau')
    period = TIMEFRAME_MS[plan.timeframe]
    entry_ms = int(plan.observed.timestamp()*1000)
    start = entry_ms // period * period
    end = start + plan.horizon * period
    now_ms = int(now.timestamp()*1000)
    window = {}
    for candle in candles:
        if not isinstance(candle,(list,tuple)) or not candle:
            continue
        try:
            stamp = _integer(candle[0])
        except (ValueError, InvalidOperation):
            continue
        if start <= stamp < end and stamp+period <= now_ms:
            window.setdefault(stamp,[]).append(candle)
    def unknown(reason):
        return TrainingOutcome('END_OF_DATA',now,None,None,reason,False)
    if not window:
        return unknown('INCOMPLETE_HISTORY') if now_ms >= end and candles else None
    broker = PaperBroker(plan.fee,plan.slippage)
    decision = RiskDecision(approved=True,amount=plan.amount,notional_quote=plan.amount*plan.price,
                            stop_loss_price=plan.stop,take_profit_price=plan.take)
    entry = broker.entry(decision,plan.price)
    def close_at(candle,value,status):
        fill = broker.exit(plan.amount,value)
        pnl = fill.notional_quote-entry.notional_quote-entry.fee_quote-fill.fee_quote
        at = datetime.fromtimestamp((int(candle[0])+period)/1000,timezone.utc)
        return TrainingOutcome(status,at,fill.price,pnl,'HORIZON' if status=='END_OF_DATA' else status,True)
    for stamp in range(start,end,period):
        candidates = window.get(stamp)
        if candidates is None:
            return unknown('INCOMPLETE_HISTORY') if now_ms >= end else None
        # Les volumes ne changent pas les sorties ; seules les valeurs OHLC comptent.
        candle = _bar(candidates[0])
        if any(_bar(other)[1:] != candle[1:] for other in candidates[1:]):
            raise ValueError('bougies contradictoires')
        if stamp == start and entry_ms != start:
            if candle[3] <= plan.stop or (plan.take is not None and candle[2] >= plan.take):
                return unknown('AMBIGUOUS_ENTRY_BAR')
        else:
            triggered = broker.exit_trigger(plan.stop,plan.take,candle)
            if triggered:
                value,reason = triggered
                return close_at(candle,value,reason)
        if stamp+period == end:
            return close_at(candle,candle[4],'END_OF_DATA')
    return None


async def evaluate_training_shadows(repo, exchange, *, now, limit=100):
    rows = await repo.training_shadows(limit)
    cache = {}
    evaluated = failed = 0
    for row in rows:
        try:
            plan = training_plan(row)
        except InvalidProposal:
            outcome = TrainingOutcome('END_OF_DATA',now,None,None,'INVALID_PROPOSAL',False)
        else:
            key = (row['symbol'],plan.timeframe)
            if key not in cache:
                try:
                    cache[key] = await asyncio.to_thread(exchange.fetch_ohlcv,*key,None,720)
                except Exception as exc:
                    cache[key] = exc
            try:
                if isinstance(cache[key],Exception):
                    raise cache[key]
                outcome = simulate_training(row,cache[key],now)
            except Exception:
                # Une donnée externe défectueuse reste à réessayer. Les écritures
                # SQL sont hors de ce bloc : une erreur de persistance reste fatale.
                failed += 1
                outcome = None
        await repo.update_training_shadow(row,outcome)
        evaluated += outcome is not None
    if failed:
        raise SimulationDataUnavailable(evaluated,failed)
    return evaluated
