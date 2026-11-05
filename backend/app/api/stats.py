"""Statistiques du dashboard — lecture seule, aucun calcul de trading.

Les montants restent des chaînes de caractères : un `Decimal` converti en flottant
par un sérialiseur JSON perdrait des chiffres, et le projet interdit les flottants
pour l'argent.
"""

from __future__ import annotations

from decimal import Decimal

from app.api.service import jsonable
from app.core.settings import Settings

__all__ = ["build_stats"]


async def build_stats(repo, settings: Settings) -> dict:
    mode = settings.execution_mode
    curve = await repo.equity_curve(mode)
    metrics = await repo.dashboard_metrics(mode)
    daily = await repo.daily_pnl(mode)
    rejections = await repo.rejection_stats(mode)
    llm = await repo.llm_stats(mode)
    shadow = await repo.shadow_summary(mode)
    active_lessons = await repo.active_lessons(mode)
    lesson_history = await repo.recent_lessons(mode, 1)

    account = await repo.account(mode, settings.paper_start_equity)
    position = await repo.open_position(mode)

    total = int(metrics.get("total_trades") or 0)
    wins = int(metrics.get("wins") or 0)
    losses = int(metrics.get("losses") or 0)
    win_rate = Decimal(wins) / Decimal(total) if total else Decimal(0)

    gross_profit = Decimal(str(metrics.get("gross_profit", 0) or 0))
    gross_loss = Decimal(str(metrics.get("gross_loss", 0) or 0))
    fees = Decimal(str(metrics.get("fees", 0) or 0))
    avg_win = Decimal(str(metrics.get("avg_win", 0) or 0))
    avg_loss = Decimal(str(metrics.get("avg_loss", 0) or 0))

    payoff_ratio = (avg_win / abs(avg_loss)) if avg_loss != 0 else Decimal(0)
    profit_factor_denominator = abs(gross_loss) + fees
    profit_factor_net = (
        gross_profit / profit_factor_denominator
        if profit_factor_denominator != 0 else Decimal(0)
    )

    engaged_position = Decimal(0)
    if position is not None:
        engaged_position = position.entry_price * position.amount

    rejection_total = sum(int(row["count"]) for row in rejections) or 1
    rejection_rows = [
        {
            "code": row["code"],
            "count": int(row["count"]),
            "percentage": str(Decimal(int(row["count"])) / Decimal(rejection_total)),
        }
        for row in rejections
    ]

    counts = {action: int(llm["counts"].get(action, 0)) for action in ("BUY", "SELL", "HOLD", "HALT")}

    return {
        "equity_curve": [
            {
                "at": jsonable(row["taken_at"]),
                "equity": jsonable(row["equity_quote"]),
                "free": jsonable(row["free_quote"]),
                "position_value": jsonable(row["position_value_quote"]),
            }
            for row in curve
        ],
        "pnl": {
            "trades": total,
            "wins": wins,
            "losses": losses,
            "win_rate": str(win_rate),
            "net_pnl": jsonable(metrics.get("net_pnl", Decimal("0"))),
            "fees": jsonable(metrics.get("fees", Decimal("0"))),
            "r_multiple": jsonable(metrics.get("r_multiple", Decimal("0"))),
            "payoff_ratio": str(payoff_ratio),
            "profit_factor_net": str(profit_factor_net),
            "gross_profit": jsonable(gross_profit),
            "gross_loss": jsonable(gross_loss),
        },
        "capital": {
            "free_quote": jsonable(account.free_quote),
            "engaged_position_quote": jsonable(engaged_position),
            "reserve_quote": jsonable(account.reserve_quote),
            "equity": jsonable(account.equity),
        },
        "daily": [
            {
                "day": jsonable(row["day"]),
                "trades": int(row["trades"]),
                "wins": int(row["wins"]),
                "net_pnl": jsonable(row["net_pnl"]),
            }
            for row in daily
        ],
        "rejections": rejection_rows,
        "shadow": {
            "total": int(shadow["total"]),
            "planned": int(shadow["planned"]),
            "no_plan": int(shadow["no_plan"]),
            "open": int(shadow["open"]),
            "closed": int(shadow["closed"]),
            "wins": int(shadow["wins"]),
            "losses": int(shadow["losses"]),
            "net_pnl": jsonable(shadow["net_pnl"]),
            "by_kind": {
                str(key): int(value) for key, value in shadow["by_kind"].items()
            },
            "by_rejection": [
                {"code": row["code"], "count": int(row["count"])}
                for row in shadow["by_rejection"]
            ],
        },
        "lessons": {
            "active": list(active_lessons),
            "version": int(lesson_history[0]["version"]) if (lesson_history and active_lessons) else None,
            "updated_at": jsonable(lesson_history[0]["created_at"]) if (lesson_history and active_lessons) else None,
            "source_count": int(lesson_history[0]["source_count"]) if (lesson_history and active_lessons) else 0,
        },
        "llm": {
            "model": settings.llm.model,
            "counts": counts,
            "total": int(llm["total"]),
            "degraded": int(llm["degraded"]),
            "avg_inference_ms": int(llm["avg_inference_ms"]),
            "last": jsonable(llm["last"]) if llm["last"] else None,
        },
    }
