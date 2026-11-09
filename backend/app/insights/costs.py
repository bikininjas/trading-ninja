"""Coût de carnet observé : aucune espérance inventée et aucun sizing ici."""

from datetime import datetime, timezone
from decimal import Decimal as D


def estimate_costs(book, amount, fee_rate, *, now, max_age_ms=5000, reference=None):
    if amount <= 0 or not amount.is_finite() or not D(0) <= fee_rate < D(".1"):
        raise ValueError("quantité ou frais invalides")
    stamp = book.get("timestamp")
    if stamp is None:
        return {
            "available": False,
            "reason": "SOURCE_TIME_MISSING",
            "model_version": "book-v1",
        }
    age = int(now.timestamp() * 1000) - int(stamp)
    if not 0 <= age <= max_age_ms:
        return {
            "available": False,
            "reason": "STALE_BOOK",
            "age_ms": age,
            "model_version": "book-v1",
        }

    def levels(side):
        rows = [(D(str(p)), D(str(q))) for p, q, *_ in book.get(side, [])]
        if not rows or any(
            not p.is_finite() or not q.is_finite() or p <= 0 or q <= 0 for p, q in rows
        ):
            raise ValueError("carnet incohérent")
        prices = [p for p, q in rows]
        if prices != sorted(prices, reverse=side == "bids"):
            raise ValueError("carnet désordonné")
        return rows

    try:
        bids, asks = levels("bids"), levels("asks")
        if bids[0][0] >= asks[0][0]:
            raise ValueError("carnet croisé")

        def consume(rows):
            left, cost = amount, D(0)
            for p, q in rows:
                taken = min(left, q)
                cost += taken * p
                left -= taken
                if left == 0:
                    return cost
            return None

        buy, sell = consume(asks), consume(bids)
        if buy is None or sell is None:
            return {
                "available": False,
                "reason": "INSUFFICIENT_DEPTH",
                "model_version": "book-v1",
            }
        mid = (asks[0][0] + bids[0][0]) / 2
        fees = (buy + sell) * fee_rate
        spread = amount * (asks[0][0] - bids[0][0])
        impact = (buy - amount * asks[0][0]) + (amount * bids[0][0] - sell)
        total = buy - sell + fees
        return {
            "available": True,
            "model_version": "book-v1",
            "as_of": datetime.fromtimestamp(stamp / 1000, timezone.utc),
            "amount": amount,
            "midpoint": mid,
            "entry_vwap": buy / amount,
            "exit_vwap_scenario": sell / amount,
            "spread_quote": spread,
            "impact_quote": impact,
            "fees_quote": fees,
            "roundtrip_cost_quote": total,
            "roundtrip_cost_pct": total / (mid * amount),
            "breakeven_exit_price": buy * (1 + fee_rate) / (amount * (1 - fee_rate)),
            "exit_assumption": "CURRENT_BOOK_SCENARIO_NOT_FORECAST",
            "age_ms": age,
        }
    except (ValueError, ArithmeticError, TypeError, IndexError) as exc:
        return {
            "available": False,
            "reason": "INVALID_BOOK",
            "detail": str(exc),
            "model_version": "book-v1",
        }
