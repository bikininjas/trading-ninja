"""Analyse descriptive, prix observés à la clôture et couverture explicite."""

from datetime import timedelta
from decimal import Decimal as D

STEP = {"1h": 3600, "4h": 14400, "1d": 86400}


def trade_diagnostics(data, fee_rate=D(0)):
    p = data["position"]
    orders = data["orders"]
    opened = p["opened_at"]
    end = data["until"]
    quote = p["symbol"].split("/")[-1]
    fees_known = not data.get("orders_truncated", False) and all(
        o.get("fee_currency") == quote or D(o["fee_amount"]) == 0 for o in orders
    )
    amount = D(p["initial_amount"])
    entry = D(p["entry_price"])
    entry_fee = sum((D(o["fee_amount"]) for o in orders if o["side"] == "BUY"), D(0))
    sells = sorted(
        (o for o in orders if o["side"] == "SELL"), key=lambda o: o["created_at"]
    )
    step = STEP[data["timeframe"]]
    observations = {}
    archived = set()
    for c in data["candles"]:
        close_at = c["open_time"] + timedelta(seconds=step)
        if opened < close_at <= end:
            observations[close_at] = D(c["close_price"])
            archived.add(close_at)
    # Snapshots carry position value, not necessarily the original full quantity.
    for mark in data["marks"]:
        if not opened <= mark["taken_at"] <= end:
            continue
        remaining = amount - sum(
            (
                D(o["filled_amount"])
                for o in sells
                if o["created_at"] <= mark["taken_at"]
            ),
            D(0),
        )
        if remaining > 0 and D(mark["position_value_quote"]) > 0:
            observations.setdefault(
                mark["taken_at"], D(mark["position_value_quote"]) / remaining
            )
    samples = []
    for at, price in sorted(observations.items()):
        known = [o for o in sells if o["created_at"] <= at]
        sold = sum((D(o["filled_amount"]) for o in known), D(0))
        remaining = max(D(0), amount - sold)
        proceeds = sum(
            (
                D(o["filled_amount"]) * D(o["average_fill_price"]) - D(o["fee_amount"])
                for o in known
            ),
            D(0),
        )
        net = proceeds + remaining * price * (1 - fee_rate) - amount * entry - entry_fee
        samples.append(
            {
                "at": at,
                "price": price,
                "remaining": remaining,
                "net_pnl_quote": net if fees_known else None,
            }
        )
    closed = p["closed_at"] is not None
    final = D(p["net_pnl_quote"]) if closed else None
    if final is not None:
        samples.append(
            {
                "at": p["closed_at"],
                "price": D(p["exit_price"]),
                "remaining": D(0),
                "net_pnl_quote": final,
            }
        )
    values = (
        [x["net_pnl_quote"] for x in samples if x["net_pnl_quote"] is not None]
        if fees_known
        else []
    )
    peak = max(values) if values else None
    trough = min(values) if values else None
    first = next(
        (
            x["at"]
            for x in samples
            if x["net_pnl_quote"] is not None and x["net_pnl_quote"] > 0
        ),
        None,
    )
    expected = max(0, int(end.timestamp()) // step - int(opened.timestamp()) // step)
    coverage = min(D(1), D(len(archived)) / expected) if expected else D(0)
    risk = D(p["risk_amount_quote"])
    return {
        "position_id": str(p["id"]),
        "symbol": p["symbol"],
        "policy_id": p.get("policy_id"),
        "resolution": "OBSERVED_CLOSES_AND_FILLS",
        "coverage": coverage,
        "coverage_status": "COMPLETE" if coverage == 1 else "PARTIAL",
        "coverage_basis": "ARCHIVED_DECISION_CLOSES",
        "observations": len(observations),
        "truncated": bool(
            data.get("observations_truncated") or data.get("orders_truncated")
        ),
        "mfe_quote": peak,
        "mae_quote": trough,
        "first_net_positive_at": first,
        "seconds_to_positive": D(str((first - opened).total_seconds()))
        if first
        else None,
        "duration_seconds": int((end - opened).total_seconds()),
        "realized_net_pnl_quote": final,
        "realized_r": final / risk if final is not None and risk > 0 else None,
        "giveback_quote": peak - final
        if peak is not None and final is not None
        else None,
        "capture_ratio": final / peak
        if peak is not None and peak > 0 and final is not None
        else None,
        "fees_quote": sum((D(o["fee_amount"]) for o in orders), D(0))
        if fees_known
        else None,
        "fees_status": "KNOWN_QUOTE" if fees_known else "CONVERSION_UNAVAILABLE",
        "slippage_quote": sum(
            (
                D(o["slippage_quote"])
                for o in orders
                if o.get("slippage_quote") is not None
            ),
            D(0),
        )
        if orders and all(o.get("slippage_quote") is not None for o in orders)
        else None,
        "intrabar_extremes_known": False,
        "series": samples,
        "calculation_version": "trade-v1",
    }


def opportunity_summary(events, *, limit=5000):
    from collections import Counter

    stages = Counter(e["stage"] for e in events)
    terminals = Counter(e["stage"] for e in events if e["terminal"])
    unique = {e["opportunity_id"] for e in events if e.get("opportunity_id")}
    return {
        "events": len(events),
        "unique_opportunities": len(unique),
        "terminal_attempts": sum(terminals.values()),
        "stages": dict(stages),
        "rejection_codes": dict(
            Counter(
                e.get("body", {}).get("code")
                for e in events
                if e.get("body", {}).get("code")
            )
        ),
        "terminal_reasons": dict(terminals),
        "truncated": len(events) >= limit,
        "coverage": "OBSERVED_EVENTS_ONLY",
        "last_reason": next((e for e in events if e["terminal"]), None),
    }


def backtest_trade_diagnostics(trade, candles, step_ms):
    """Même lecture par trade pour les rapports H4/D1, sans modifier leur stratégie."""
    from datetime import datetime, timezone

    observations = []
    for candle in candles:
        at = datetime.fromtimestamp((candle[0] + step_ms) / 1000, timezone.utc)
        if trade.entry_at < at < trade.exit_at:
            # Historical trades store the total roundtrip fee; it is applied as a
            # conservative constant for this descriptive net excursion estimate.
            net = (
                D(str(candle[4])) - trade.entry_price
            ) * trade.amount - trade.fees_quote
            observations.append((at, net))
    observations.append((trade.exit_at, trade.pnl_quote))
    values = [v for _, v in observations]
    peak = max(values)
    low = min(values)
    first = next((at for at, v in observations if v > 0), None)
    risk = (trade.entry_price - trade.stop_loss_price) * trade.amount
    return {
        "resolution": "DECISION_CLOSES_AND_EXIT_FILL",
        "intrabar_extremes_known": False,
        "mfe_quote": str(peak),
        "mae_quote": str(low),
        "first_net_positive_at": first.isoformat() if first else None,
        "giveback_quote": str(peak - trade.pnl_quote),
        "capture_ratio": str(trade.pnl_quote / peak) if peak > 0 else None,
        "realized_r": str(trade.pnl_quote / risk) if risk > 0 else None,
        "fees_quote": str(trade.fees_quote),
        "fee_assumption": "RECORDED_ROUNDTRIP_TOTAL",
        "observations": len(values),
    }
