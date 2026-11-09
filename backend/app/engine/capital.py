"""Capital actif, réserve protégée et croissance : calcul pur, partagé au rejeu.

Les enveloppes sont virtuelles. Un transfert entre elles ne crée aucun PnL.
Seul un nouveau plus haut de PnL réalisé peut alimenter les deux cagnottes.
"""
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_DOWN

ZERO = Decimal(0)
QUANTUM = Decimal('0.0000000001')  # NUMERIC(28,10)


@dataclass(frozen=True)
class CapitalPolicy:
    enabled: bool = False
    reserve_ratio: Decimal = Decimal('.30')
    growth_trigger: Decimal = Decimal('1')
    step_quote: Decimal = Decimal('.25')
    step_max_pct: Decimal = Decimal('.05')
    interval_days: int = 7
    max_active_quote: Decimal = Decimal('20')

    def __post_init__(self):
        for name in ('reserve_ratio', 'growth_trigger', 'step_quote', 'step_max_pct', 'max_active_quote'):
            value = getattr(self, name)
            if not value.is_finite() or value <= 0:
                raise ValueError(f'{name} doit être fini et positif')
        if self.reserve_ratio >= 1 or self.step_max_pct > Decimal('.05'):
            raise ValueError('réserve < 100 % et palier ≤ 5 % requis')
        if self.interval_days < 7:
            raise ValueError('au plus un palier par semaine')
        if self.step_quote > self.growth_trigger:
            raise ValueError('le palier ne peut dépasser le seuil de cagnotte')


@dataclass(frozen=True)
class CapitalState:
    initial_quote: Decimal
    active_quote: Decimal
    reserve_quote: Decimal
    growth_quote: Decimal
    realized_pnl: Decimal
    surplus_hwm: Decimal
    last_promotion_at: datetime
    promotions: int = 0

    @property
    def active_drawdown(self):
        loss = max(ZERO, self.surplus_hwm - self.realized_pnl)
        peak = self.active_quote + loss
        return loss / peak if peak > 0 else ZERO

    @property
    def total(self):
        return self.active_quote + self.reserve_quote + self.growth_quote


def start_capital(equity: Decimal, at: datetime, *, reserve: Decimal = ZERO) -> CapitalState:
    if not equity.is_finite() or equity <= 0 or not ZERO <= reserve <= equity:
        raise ValueError('capital initial / réserve incohérents')
    if at.tzinfo is None:
        raise ValueError('date sans fuseau')
    return CapitalState(equity, equity - reserve, reserve, ZERO, ZERO, ZERO, at)


def allocate_close(state: CapitalState, pnl: Decimal, at: datetime,
                   policy: CapitalPolicy, *, allow_promotion: bool = True) -> CapitalState:
    if not pnl.is_finite() or at.tzinfo is None or at < state.last_promotion_at:
        raise ValueError('PnL ou date de clôture invalide')
    realized = state.realized_pnl + pnl
    surplus = max(ZERO, realized - state.surplus_hwm)
    reserved = (surplus * policy.reserve_ratio).quantize(QUANTUM, rounding=ROUND_DOWN)
    result = replace(state, active_quote=state.active_quote + pnl - surplus,
                     reserve_quote=state.reserve_quote + reserved,
                     growth_quote=state.growth_quote + surplus - reserved,
                     realized_pnl=realized, surplus_hwm=max(state.surplus_hwm, realized))
    # Une perte extrême ne doit jamais être absorbée silencieusement par la réserve.
    if result.active_quote < 0:
        raise ValueError('capital actif épuisé : réconciliation nécessaire')
    if (allow_promotion and realized >= result.surplus_hwm and pnl > 0
            and result.growth_quote >= policy.growth_trigger
            and at >= state.last_promotion_at + timedelta(days=policy.interval_days)):
        step = min(policy.step_quote, result.active_quote * policy.step_max_pct,
                   policy.max_active_quote - result.active_quote, result.growth_quote)
        step = step.quantize(Decimal('.01'), rounding=ROUND_DOWN)
        if step > 0:
            result = replace(result, active_quote=result.active_quote + step,
                             growth_quote=result.growth_quote - step,
                             last_promotion_at=at, promotions=state.promotions + 1)
    if result.total != state.total + pnl:
        raise ValueError('conservation du capital violée')
    return result
