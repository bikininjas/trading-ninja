"""Règles stratégiques immuables ; les limites de sécurité restent courantes."""

from dataclasses import fields, replace
from app.insights.codec import encode, decode, digest

STRATEGIC = frozenset(
    {
        "entry_mode",
        "stop_mode",
        "stop_atr_multiple",
        "take_profit_r_multiple",
        "swing_exit_mode",
        "swing_breakeven_r",
        "swing_exit_below_ema20",
        "swing_chandelier_period",
        "swing_chandelier_atr_multiple",
        "profit_trailing_enabled",
        "profit_trailing_start_r",
        "profit_trailing_distance_r",
        "min_exit_confidence",
        "min_halt_confidence",
    }
)


def position_policy(settings, *, legacy=False):
    body = {
        "schema_version": 1,
        "strategy": str(settings.risk.entry_mode),
        "timeframe": settings.exchange.daily_timeframe_decision,
        "legacy_adoption": legacy,
        "parameters": {
            f.name: encode(getattr(settings.risk, f.name))
            for f in fields(settings.risk)
            if f.name in STRATEGIC
        },
    }
    return {"id": digest(body), "body": body}


def apply_policy(settings, policy):
    body = policy["body"]
    if body.get("schema_version") != 1 or policy["id"] != digest(body):
        raise ValueError("politique de position invalide")
    if body["timeframe"] != settings.exchange.daily_timeframe_decision:
        raise ValueError("cadence de position incompatible")
    params = body["parameters"]
    if set(params) - STRATEGIC:
        raise ValueError("une politique ne peut remplacer une limite de sécurité")
    return replace(
        settings,
        risk=replace(settings.risk, **{k: decode(v) for k, v in params.items()}),
    )


def settings_impact(before, after, positions):
    changes = {
        f.name: {
            "before": encode(getattr(before.risk, f.name)),
            "after": encode(getattr(after.risk, f.name)),
            "scope": "NEXT_POSITION" if f.name in STRATEGIC else "GLOBAL_SAFETY",
        }
        for f in fields(before.risk)
        if getattr(before.risk, f.name) != getattr(after.risk, f.name)
    }
    return {
        "changes": changes,
        "positions": [
            {
                "id": str(p.id),
                "symbol": p.symbol,
                "policy_id": getattr(p, "policy_id", None),
                "strategy_change": "UNCHANGED"
                if getattr(p, "policy_id", None)
                else "LEGACY_REVIEW_REQUIRED",
            }
            for p in positions
        ],
    }
