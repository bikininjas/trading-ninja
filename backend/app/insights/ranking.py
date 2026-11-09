"""Classement observable ; sélection activable uniquement en papier."""

from decimal import Decimal as D


def rank_candidates(candidates, *, now, max_age_seconds=120):
    ranked = []
    for item in candidates:
        age = (now - item["observed_at"]).total_seconds()
        cost = item.get("cost") or {}
        eligible = (
            0 <= age <= max_age_seconds
            and bool(item.get("approved"))
            and cost.get("available") is True
        )
        ranked.append(
            {
                **item,
                "eligible": eligible,
                "reason": "RANKED"
                if eligible
                else "STALE_CANDIDATE"
                if not 0 <= age <= max_age_seconds
                else "COST_UNAVAILABLE"
                if not cost.get("available")
                else "RISK_REJECTED",
                "policy_version": "lexicographic-v1",
            }
        )
    ranked.sort(
        key=lambda x: (
            not x["eligible"],
            x["cost"].get("roundtrip_cost_pct", D("Infinity")),
            x.get("structure_atr", D(0)),
            x["symbol"],
        )
    )
    return [{**row, "rank": i + 1} for i, row in enumerate(ranked)]
