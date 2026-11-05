"""Métriques pures pour l'évaluation du LLM sur les signaux swing D1.

Le backtest mécanique fournit les trades « théoriques » (tous les setups pris
avec une confiance de 1,0). Le LLM est ensuite rejoué sur chaque date d'entrée :
on mesure sa capacité à **conserver** les trades gagnants et à **filtrer** les
perdants. Aucun ordre, aucune écriture en base : ce module ne calcule que des
taux.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

__all__ = ["EvalMetrics", "EvalRecord", "compute_metrics", "verdict"]


@dataclass(frozen=True, slots=True)
class EvalRecord:
    symbol: str
    entry_at: datetime
    exit_at: datetime
    pnl_quote: Decimal
    action: str
    confidence: Decimal
    market_regime: str
    thesis: str
    inference_ms: int
    degraded: bool
    approved: bool

    @property
    def winner(self) -> bool:
        return self.pnl_quote > 0


@dataclass(frozen=True, slots=True)
class EvalMetrics:
    total: int
    winners: int
    losers: int
    approved: int
    approved_winners: int
    rejected_losers: int
    retention: Decimal | None
    filtration: Decimal | None
    approval_rate: Decimal | None

    @property
    def verdict_label(self) -> str:
        return verdict(self)


def _rate(numerator: int, denominator: int) -> Decimal | None:
    if denominator <= 0:
        return None
    return Decimal(numerator) / Decimal(denominator)


def compute_metrics(records: list[EvalRecord]) -> EvalMetrics:
    """Calcule rétention et filtration, sans arrondir avant l'affichage."""
    winners = [record for record in records if record.winner]
    losers = [record for record in records if not record.winner]
    approved_winners = [record for record in winners if record.approved]
    rejected_losers = [record for record in losers if not record.approved]
    return EvalMetrics(
        total=len(records),
        winners=len(winners),
        losers=len(losers),
        approved=sum(1 for record in records if record.approved),
        approved_winners=len(approved_winners),
        rejected_losers=len(rejected_losers),
        retention=_rate(len(approved_winners), len(winners)),
        filtration=_rate(len(rejected_losers), len(losers)),
        approval_rate=_rate(sum(1 for record in records if record.approved), len(records)),
    )


def verdict(
    metrics: EvalMetrics,
    *,
    min_filtration: Decimal = Decimal("0.25"),
    min_retention: Decimal = Decimal("0.90"),
) -> str:
    """Critère du brief : filtrer ≥25 % des perdants sans perdre >10 % des gagnants."""
    reasons: list[str] = []
    if metrics.filtration is None or metrics.filtration < min_filtration:
        reasons.append(f"filtration<{min_filtration:.0%}")
    if metrics.retention is None or metrics.retention < min_retention:
        reasons.append(f"rétention<{min_retention:.0%}")
    return "OK" if not reasons else "KO " + ",".join(reasons)
