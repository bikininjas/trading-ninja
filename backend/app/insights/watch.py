"""Surveillant de lecture séparé ; ses seules commandes sont des demandes de HALT."""

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from urllib.parse import urlsplit
import httpx

from app.execution.live import LiveBroker
from app.insights.codec import public


def verify_observer_key(exchange):
    payload = exchange.privatePostGetApiKeyInfo()
    result = payload.get("result", payload)
    permissions = result.get("permissions") or []
    tokens = [
        set(str(p).lower().replace("_", " ").replace("-", " ").split())
        for p in permissions
    ]
    if not tokens or any(
        t
        & {
            "create",
            "cancel",
            "modify",
            "withdraw",
            "deposit",
            "add",
            "edit",
            "transfer",
            "manage",
        }
        or not ("query" in t or t == {"access", "websockets"})
        for t in tokens
    ):
        raise ValueError("clé de surveillance exclusivement en lecture requise")
    if not any({"query", "funds"} <= t for t in tokens) or not any(
        {"query", "open", "orders"} <= t for t in tokens
    ):
        raise ValueError("lecture des fonds et des ordres ouverts requise")
    return True


async def observe_protection(
    repo, exchange, mode, *, now=None, expect_runtime=False, timeframe="4h", universe=()
):
    now = now or datetime.now(timezone.utc)
    positions = await repo.open_positions(mode)
    issues = []
    observations = []
    complete = True

    def issue(key, reason, **evidence):
        issues.append(
            {"key": key, "severity": "CRITICAL", "reason": reason, **evidence}
        )

    if mode == "LIVE":
        try:
            if exchange is None:
                raise ValueError("clé de lecture indépendante non configurée")
            await asyncio.to_thread(verify_observer_key, exchange)
            balance = await asyncio.to_thread(exchange.fetch_balance)
            orders = await asyncio.to_thread(exchange.fetch_open_orders)
            totals = balance.get("total") or {}
            tracked = {p.symbol.split("/")[0] for p in positions}
            for symbol in universe:
                base = symbol.split("/")[0]
                quantity = D(str(totals.get(base) or 0))
                if quantity > 0 and base not in tracked:
                    issue(
                        "UNTRACKED:" + symbol,
                        "actif exchange non suivi",
                        symbol=symbol,
                        base_total=quantity,
                    )
            for p in positions:
                quantity = D(str(totals.get(p.symbol.split("/")[0]) or 0))
                if not quantity.is_finite() or quantity < 0:
                    raise ValueError("solde invalide")
                stops = [
                    o
                    for o in orders
                    if o.get("symbol") == p.symbol
                    and LiveBroker._covers_position(o, quantity, p.stop_loss_price)
                ]
                covered = quantity > 0 and bool(stops)
                status = (
                    "PROTECTED"
                    if covered
                    else "RECONCILIATION_REQUIRED"
                    if quantity == 0
                    else "UNPROTECTED"
                )
                observations.append(
                    {
                        "position_id": str(p.id),
                        "symbol": p.symbol,
                        "base_total": quantity,
                        "ledger_amount": p.amount,
                        "stop_price": p.stop_loss_price,
                        "status": status,
                        "stop_ids": [o.get("id") for o in stops],
                    }
                )
                if quantity != p.amount:
                    issue(
                        "QUANTITY:" + str(p.id),
                        "quantité distante différente",
                        symbol=p.symbol,
                        observed=quantity,
                        expected=p.amount,
                    )
                if quantity > 0 and not covered:
                    issue(
                        "STOP:" + str(p.id), "protection insuffisante", symbol=p.symbol
                    )
        except Exception as exc:
            complete = False
            issue(
                "REMOTE_UNKNOWN",
                "lecture exchange inconnue",
                error_type=type(exc).__name__,
            )
    else:
        observations = [
            {
                "position_id": str(p.id),
                "symbol": p.symbol,
                "status": "PAPER_LOCAL_PROTECTION",
            }
            for p in positions
        ]
    cycles = await repo.recent_cycles(mode, 1)
    if expect_runtime:
        if not cycles:
            issue("RUNTIME_MISSING", "aucune preuve de cycle")
        else:
            cycle = cycles[0]
            if cycle["status"] == "RUNNING" and now - cycle["started_at"] > timedelta(
                minutes=10
            ):
                issue("RUNTIME_STALLED", "cycle commencé sans fin depuis dix minutes")
            if cycle["status"] != "RUNNING":
                from app.runtime.scheduler import (
                    next_candle_close,
                    decision_delay_seconds,
                )

                due = next_candle_close(cycle["started_at"], timeframe) + timedelta(
                    seconds=decision_delay_seconds(timeframe) + 600
                )
                if now > due:
                    issue(
                        "RUNTIME_LATE",
                        "échéance de décision dépassée",
                        expected_before=due,
                    )
    observed = {
        "observed_at": now,
        "complete": complete,
        "mode": mode,
        "positions": observations,
        "runtime_expected": expect_runtime,
        "issues": len(issues),
    }
    applied = await repo.record_protection(mode, observed, issues)
    if issues and applied is not False:
        # HALT is a database state, never a cancel/sell. Stops remain untouched.
        await repo.force_halt(
            mode, "surveillance : " + ", ".join(i["key"] for i in issues)[:200]
        )
    return {"observation": observed, "issues": issues}


async def notify_incident(url, payload, *, transport=None):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password:
        raise ValueError("webhook HTTPS sans identifiant dans l’URL requis")
    async with httpx.AsyncClient(
        transport=transport, timeout=5, follow_redirects=False
    ) as client:
        response = await client.post(url, json=public(payload))
        response.raise_for_status()
    return True
