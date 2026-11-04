"""Profit Sweeper — calcul pur, sans I/O.

À chaque clôture gagnante, une part du gain net est mise de côté. Le calcul est
séparé de l'écriture pour deux raisons :

- il doit être testable sans base ni exchange ;
- la décision « combien » ne doit jamais dépendre de l'état de la connexion.

Deux statuts seulement en sortie :

- `SIMULATED` : le montant est écrémé dans le ledger papier (aucun virement réel) ;
- `SKIPPED_BELOW_MINIMUM` : le montant calculé est inférieur au seuil de transfert.
  On écrit quand même la ligne : un écrémage trop petit est un **report**, pas un
  échec silencieux. Le capital reste disponible pour trader.

Un trade perdant ne produit **aucune** ligne : la contrainte SQL
`CHECK (realized_net_pnl > 0)` l'interdit, et c'est voulu.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

__all__ = ["SweepPlan", "plan_sweep"]

QUANTUM = Decimal("0.00000001")


@dataclass(frozen=True, slots=True)
class SweepPlan:
    applies: bool
    computed_quote: Decimal
    swept_quote: Decimal
    status: str | None
    reason: str | None


def plan_sweep(net_pnl_quote: Decimal, ratio: Decimal, minimum_quote: Decimal,
               *, deferred_quote: Decimal = Decimal(0), simulated: bool = True) -> SweepPlan:
    """Calcule l'écrémage, reports antérieurs compris.

    `deferred_quote` est la somme des écréma­ges précédemment reportés (sous le
    seuil) et non encore exécutés. Sans cette accumulation, un compte dont les gains
    unitaires restent sous le seuil n'écrèmerait jamais : le seuil deviendrait une
    interdiction déguisée.
    """
    if not (Decimal(0) < ratio < Decimal(1)):
        raise ValueError("sweep_ratio doit être strictement entre 0 et 1")
    if minimum_quote < 0:
        raise ValueError("sweep_min_transfer_quote ne peut pas être négatif")
    if deferred_quote < 0:
        raise ValueError("deferred_quote ne peut pas être négatif")

    if net_pnl_quote <= 0:
        return SweepPlan(False, Decimal(0), Decimal(0), None, "pas de gain net")

    computed = (net_pnl_quote * ratio).quantize(QUANTUM)
    available = (computed + deferred_quote).quantize(QUANTUM)
    if available >= minimum_quote:
        return SweepPlan(
            applies=True,
            computed_quote=available,
            swept_quote=available,
            status="SIMULATED" if simulated else "PENDING",
            reason=None,
        )
    return SweepPlan(
        applies=True,
        computed_quote=computed,
        swept_quote=Decimal(0),
        status="SKIPPED_BELOW_MINIMUM",
        reason=f"{available} < minimum {minimum_quote}",
    )
