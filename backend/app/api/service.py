"""Vue « administration » : lecture de l'état et actions de contrôle.

Aucune décision de trading ici. L'API observe et arrête ; elle ne propose jamais
d'acheter. Les montants sortent en chaînes de caractères : un `Decimal` converti en
flottant par un sérialiseur JSON perdrait des chiffres, et le projet interdit les
flottants pour l'argent.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from app.core.settings import Settings
from app.db.repository import PostgresRepository

__all__ = ["ControlRefused", "build_status", "jsonable", "pause", "resume"]


class ControlRefused(RuntimeError):
    """Commande refusée parce qu'elle laisserait le bot dans un état dangereux."""


def jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(item) for item in value]
    return value


def _position_payload(position, fee_rate: Decimal = Decimal("0")) -> dict | None:
    if position is None:
        return None
    entry_notional = position.entry_price * position.amount
    net_risk = position.risk_amount_quote + entry_notional * fee_rate * 2
    return {
        "id": str(position.id),
        "symbol": position.symbol,
        "entry_price": str(position.entry_price),
        "amount": str(position.amount),
        "stop_loss_price": str(position.stop_loss_price),
        "take_profit_price": jsonable(position.take_profit_price),
        "opened_at": position.opened_at.isoformat(),
        "risk_amount_quote": str(position.risk_amount_quote),
        "fee_rate": str(fee_rate),
        "estimated_net_risk_quote": str(net_risk),
    }


async def build_status(repo: PostgresRepository, settings: Settings) -> dict:
    mode = settings.execution_mode
    state, cooldown = await repo.state(mode)
    account = await repo.account(mode, settings.paper_start_equity)
    position = await repo.open_position(mode)
    signals = await repo.recent("llm_signals", mode, 5)
    evaluations = await repo.recent("risk_evaluations", mode, 5)
    orders = await repo.recent("orders", mode, 5)
    halt_reason = await repo.halt_details(mode) if state == "EMERGENCY_HALT" else None
    return {
        "mode": mode,
        "state": state,
        "cooldown_until": cooldown.isoformat() if cooldown else None,
        "halt": {
            "reason": halt_reason,
            "auto_resume_at": cooldown.isoformat()
            if state == "EMERGENCY_HALT" and cooldown else None,
        },
        "equity": str(account.equity),
        "free_quote": str(account.free_quote),
        "reserve_quote": str(account.reserve_quote),
        "growth_quote": str(account.growth_quote),
        "active_equity": str(account.trading_equity),
        "capital_steps": {
            "promotions": account.promotions,
            "step_quote": str(settings.capital_policy.step_quote),
            "growth_trigger": str(settings.capital_policy.growth_trigger),
            "interval_days": settings.capital_policy.interval_days,
            "next_review_at": (account.last_promotion_at + timedelta(days=settings.capital_policy.interval_days)).isoformat()
            if account.last_promotion_at else None,
        },
        "drawdowns": {
            "daily": str(account.daily_drawdown_pct),
            "weekly": str(account.weekly_drawdown_pct),
            "from_hwm": str(account.from_hwm_drawdown_pct),
        },
        "consecutive_losses": account.consecutive_losses,
        "orders_last_hour": account.orders_last_hour,
        "position": _position_payload(position, settings.exchange.taker_fee),
        "recent": {
            "signals": [jsonable(row) for row in signals],
            "evaluations": [jsonable(row) for row in evaluations],
            "orders": [jsonable(row) for row in orders],
        },
    }


async def pause(repo: PostgresRepository, settings: Settings) -> dict:
    # Mettre en pause avec une position ouverte arrêterait la surveillance du stop :
    # le bot ne verrait plus la bougie qui perce le niveau. On refuse, et on laisse
    # le choix entre attendre la clôture et liquider explicitement.
    position = await repo.open_position(settings.execution_mode)
    if position is not None:
        raise ControlRefused(
            f"position ouverte sur {position.symbol} : la laisser se clôturer ou "
            "utiliser le panic-sell"
        )
    await repo.force_lock(settings.execution_mode, "pause administrateur")
    return await build_status(repo, settings)


async def resume(repo: PostgresRepository, settings: Settings) -> dict:
    await repo.force_resume(settings.execution_mode, "reprise administrateur")
    return await build_status(repo, settings)
