"""Faisabilité d'un ordre avec un petit capital — calcul pur, sans I/O.

Un compte trop petit ne provoque pas une erreur : il produit un refus
`SIZING_BELOW_EXCHANGE_MINIMUM` à chaque évaluation, et le bot semble simplement
« ne jamais trouver d'opportunité ». C'est le pire des diagnostics.

Ce module répond à la question avant le premier signal : **avec cette équité, cette
volatilité et ce profil de risque, le plus petit ordre autorisé par l'exchange
tient-il dans le budget ?** Et si non, de combien d'équité manque-t-il ?

Trois plafonds décident, dans cet ordre :

1. le plafond absolu (`max_position_notional_quote`) — s'il est sous le minimum de
   l'exchange, la paire est intraitable quel que soit le capital ;
2. le budget de risque (`risk_per_trade_pct`) — le minimum d'ordre risque
   `min_order_quote × distance_au_stop / équité`, qui doit rester sous le budget ;
3. l'exposition (`max_notional_pct`) — le minimum d'ordre doit tenir dans la part
   d'équité autorisée par position.

Le calcul ne relâche aucun garde-fou : il dit seulement où est le mur.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.engine.risk_gate import ExchangeConstraints, RiskConfig

__all__ = ["Affordability", "assess"]


@dataclass(frozen=True, slots=True)
class Affordability:
    """Verdict chiffré pour une paire, à une équité donnée."""

    symbol: str
    min_order_quote: Decimal
    stop_distance_pct: Decimal
    effective_risk_pct_at_minimum: Decimal
    required_equity: Decimal | None
    binding: str  # "risk" | "exposure" | "absolute_cap" — ce qui fixe l'équité requise
    affordable: bool
    equity: Decimal

    @property
    def shortfall(self) -> Decimal:
        """Ce qui manque pour que le plus petit ordre tienne. Zéro si c'est bon."""
        if self.required_equity is None:
            return Decimal("0")
        return max(Decimal("0"), self.required_equity - self.equity)


def assess(
    symbol: str,
    *,
    price: Decimal,
    atr_14: Decimal,
    constraints: ExchangeConstraints,
    config: RiskConfig,
    equity: Decimal,
    risk_pct: Decimal | None = None,
) -> Affordability:
    """Calcule le minimum d'ordre et l'équité qu'il exige.

    `risk_pct` permet de simuler un autre profil sans construire une `RiskConfig` :
    c'est ce qui sert à répondre « et si je passais à 1,75 % ? ».
    """
    for name, value in (("price", price), ("atr_14", atr_14), ("equity", equity)):
        if not isinstance(value, Decimal):
            raise TypeError(f"{name} doit être un Decimal")
        if not value.is_finite() or value <= 0:
            raise ValueError(f"{name} doit être fini et strictement positif")

    effective_risk = risk_pct if risk_pct is not None else config.effective_risk_pct(0)
    if effective_risk <= 0:
        raise ValueError("le budget de risque doit être strictement positif")

    stop_distance_pct = (config.stop_atr_multiple * atr_14) / price
    min_order_quote = max(constraints.min_amount * price, constraints.min_notional)
    risk_at_minimum = min_order_quote * stop_distance_pct / equity

    if min_order_quote > config.max_position_notional_quote:
        return Affordability(
            symbol=symbol,
            min_order_quote=min_order_quote,
            stop_distance_pct=stop_distance_pct,
            effective_risk_pct_at_minimum=risk_at_minimum,
            required_equity=None,
            binding="absolute_cap",
            affordable=False,
            equity=equity,
        )

    required_by_risk = min_order_quote * stop_distance_pct / effective_risk
    required_by_exposure = min_order_quote / config.max_notional_pct
    binding = "risk" if required_by_risk >= required_by_exposure else "exposure"
    required_equity = max(required_by_risk, required_by_exposure)

    return Affordability(
        symbol=symbol,
        min_order_quote=min_order_quote,
        stop_distance_pct=stop_distance_pct,
        effective_risk_pct_at_minimum=risk_at_minimum,
        required_equity=required_equity,
        binding=binding,
        affordable=equity >= required_equity,
        equity=equity,
    )
