"""Rapport de discrimination ; probabilités uniquement avec contrat explicite."""

from collections import defaultdict
from decimal import Decimal as D
import json


def confidence_report(records, *, until, min_samples=20, probabilistic_contract=False):
    cohorts = defaultdict(list)
    excluded = defaultdict(int)
    for record in records:
        if record.get("action") != "BUY":
            excluded["OTHER_ACTION"] += 1
            continue
        raw = record.get("raw_response") or {}
        if isinstance(raw, str):
            raw = json.loads(raw)
        if record.get("degraded") or "repli" in record.get("model_name", ""):
            excluded["DEGRADED"] += 1
            continue
        if (
            not record.get("closed_at")
            or record["closed_at"] >= until
            or record.get("net_pnl_quote") is None
        ):
            excluded["UNRESOLVED_OR_NOT_EXECUTED"] += 1
            continue
        confidence = D(str(record["confidence"]))
        if not confidence.is_finite() or not 0 <= confidence <= 1:
            excluded["INVALID_SCORE"] += 1
            continue
        key = (
            record.get("model_name"),
            raw.get("model_digest"),
            raw.get("prompt_template_digest")
            or "legacy:" + str(record.get("prompt_sha256")),
            raw.get("lessons_digest"),
            record.get("policy_id"),
        )
        cohorts[key].append(
            (
                confidence,
                D(str(record["net_pnl_quote"])) > 0,
                record.get("market_regime"),
            )
        )
    output = []
    for (model, weights, prompt, lessons, policy), items in cohorts.items():
        buckets = []
        for index in range(5):
            selected = [(p, y) for p, y, _ in items if min(4, int(p * 5)) == index]
            count = len(selected)
            buckets.append(
                {
                    "from": D(index) / 5,
                    "to": D(index + 1) / 5,
                    "count": count,
                    "mean_score": sum((p for p, y in selected), D(0)) / count
                    if count
                    else None,
                    "positive_rate": D(sum(y for p, y in selected)) / count
                    if count
                    else None,
                    "evidence": "SUFFICIENT"
                    if count >= min_samples
                    else "INSUFFICIENT",
                }
            )
        base = D(sum(y for p, y, r in items)) / len(items)
        brier = (
            sum(((p - D(int(y))) ** 2 for p, y, r in items), D(0)) / len(items)
            if probabilistic_contract
            else None
        )
        regimes = {
            r: {
                "count": sum(regime == r for p, y, regime in items),
                "positive": sum(y and regime == r for p, y, regime in items),
            }
            for _, _, r in items
        }
        output.append(
            {
                "model": model,
                "model_digest": weights,
                "prompt_digest": prompt,
                "lessons_digest": lessons,
                "policy_id": policy,
                "count": len(items),
                "model_identity_verified": weights is not None,
                "prompt_identity_verified": not str(prompt).startswith("legacy:"),
                "positive_rate": base,
                "buckets": buckets,
                "regimes": regimes,
                "brier": brier,
                "constant_brier": base * (1 - base) if probabilistic_contract else None,
                "evidence": "INSUFFICIENT"
                if len(items) < min_samples
                or weights is None
                or policy is None
                or str(prompt).startswith("legacy:")
                else "DESCRIPTIVE_ONLY",
            }
        )
    return {
        "cohorts": output,
        "excluded": dict(excluded),
        "population": "EXECUTED_BUY_CLOSED_BEFORE_CUTOFF",
        "selection_bias": True,
        "probabilistic_contract": probabilistic_contract,
        "auto_threshold_update": False,
        "warning": "La confiance n’est pas une probabilité ; comparer la politique entière et les refus dans les expériences.",
    }
