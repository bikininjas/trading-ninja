"""Métriques de l'évaluation LLM : rétention des gagnants, filtration des perdants.

Ces taux décident si le modèle local garde sa place dans le chemin Daily. Un
dénominateur vide doit rester « n/a », jamais 0 % : un mois sans perdant ne doit
pas faire croire que le modèle filtre parfaitement.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.backtest.llm_eval import EvalMetrics, EvalRecord, compute_metrics, verdict

NOW = datetime(2026, 11, 4, tzinfo=timezone.utc)


def record(pnl: str, *, approved: bool, symbol: str = "BTC/EUR") -> EvalRecord:
    return EvalRecord(
        symbol=symbol,
        entry_at=NOW,
        exit_at=NOW,
        pnl_quote=Decimal(pnl),
        action="BUY" if approved else "HOLD",
        confidence=Decimal("0.80") if approved else Decimal("0.20"),
        market_regime="TRENDING_UP",
        thesis="test",
        inference_ms=900,
        degraded=False,
        approved=approved,
    )


def test_retention_and_filtration_are_computed_on_both_populations():
    records = [
        record("10", approved=True),
        record("20", approved=True),
        record("30", approved=True),
        record("40", approved=False),
        record("-5", approved=False),
        record("-6", approved=False),
        record("-7", approved=True),
        record("-8", approved=True),
    ]
    metrics = compute_metrics(records)
    assert metrics.total == 8
    assert metrics.winners == 4
    assert metrics.losers == 4
    assert metrics.approved_winners == 3
    assert metrics.rejected_losers == 2
    assert metrics.retention == Decimal("0.75")
    assert metrics.filtration == Decimal("0.5")
    assert verdict(metrics) == "KO rétention<90%"


def test_a_model_that_keeps_every_winner_and_filters_half_the_losers_passes():
    records = [record("10", approved=True) for _ in range(4)]
    records += [record("-5", approved=False) for _ in range(2)]
    records += [record("-5", approved=True) for _ in range(2)]
    metrics = compute_metrics(records)
    assert metrics.retention == Decimal("1")
    assert metrics.filtration == Decimal("0.5")
    assert verdict(metrics) == "OK"


def test_filtration_below_the_floor_is_refused():
    records = [record("10", approved=True)]
    records += [record("-5", approved=True) for _ in range(9)]
    metrics = compute_metrics(records)
    assert metrics.retention == Decimal("1")
    assert metrics.filtration == Decimal("0")
    assert verdict(metrics).startswith("KO filtration")


def test_empty_populations_stay_not_applicable():
    metrics = compute_metrics([])
    assert metrics.retention is None
    assert metrics.filtration is None
    assert metrics.approval_rate is None
    assert verdict(metrics).startswith("KO")


def test_a_flat_trade_counts_as_a_loss_for_the_model():
    # Un PnL nul n'est pas un gagnant : il ne finance ni les frais ni le risque.
    metrics = compute_metrics([record("0", approved=False)])
    assert metrics.winners == 0
    assert metrics.losers == 1
    assert metrics.rejected_losers == 1
    assert isinstance(metrics, EvalMetrics)
