"""Lecture publique des minima : estimation indicative, sans ordre ni signal."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING

from app.engine.costs import entry_cost
from app.exchange.kraken import build_exchange, market_info


def _number(value):
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError("valeur non finie")
    return number


def estimate(exchange, symbol, quote, free_quote, applied):
    """Ne conclut que sur les minima et le solde ; les autres contrôles restent inconnus."""
    info = market_info(exchange, symbol)
    if not info.active or info.quote != quote:
        raise ValueError("marché inactif ou devise incompatible")
    ticker = exchange.fetch_ticker(symbol)
    price = _number(ticker.get("last"))
    constraints = info.constraints
    step, minimum, notional = map(_number, (constraints.amount_step, constraints.min_amount, constraints.min_notional))
    if price <= 0 or step <= 0 or minimum <= 0 or notional < 0:
        raise ValueError("données marché invalides")
    quantity = (max(minimum, notional / price) / step).to_integral_value(rounding=ROUND_CEILING) * step
    amount = quantity * price
    risk = applied.get("risk") if isinstance(applied, dict) else None
    fee = slip = None
    try:
        if isinstance(risk, dict):
            fee, slip = _number(risk["execution_fee_pct"]), _number(risk["execution_slippage_pct"])
            if not (0 <= fee <= Decimal("0.05") and 0 <= slip < 1):
                fee = slip = None
    except (KeyError, ValueError, InvalidOperation, TypeError):
        fee = slip = None
    total = quantity * entry_cost(price, fee, slip) if fee is not None else None
    balance = _number(free_quote) if free_quote is not None else None
    if balance is not None and balance < 0:
        raise ValueError("solde invalide")
    return {
        "symbol": symbol, "quote": quote, "observed_at": datetime.now(timezone.utc),
        "ticker_timestamp_ms": ticker.get("timestamp"), "price": price,
        "min_amount": minimum, "amount_step": step, "min_notional": notional,
        "minimum_quantity": quantity, "minimum_order_quote": amount,
        "fee_rate": fee, "slippage_rate": slip,
        "estimated_entry_fee": quantity * price * (1 + slip) * fee if fee is not None else None,
        "estimated_entry_total": total, "free_quote": balance,
        "cash_sufficient": balance >= total if total is not None and balance is not None else None,
    }


def read_market(exchange_id, symbol, quote, free_quote, applied):
    if exchange_id != 'kraken':
        raise ValueError('diagnostic disponible uniquement pour Kraken')
    exchange = build_exchange()
    exchange.timeout = 5000
    exchange.load_markets()
    return estimate(exchange, symbol, quote, free_quote, applied)
