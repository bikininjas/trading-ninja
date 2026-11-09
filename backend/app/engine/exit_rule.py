"""Règles de sortie pures, indépendantes de l'exchange et de l'horloge.

Le Chandelier Exit (Chuck LeBeau) suit le plus haut des ``N`` dernières bougies
et place le stop ``N × ATR`` en dessous. Le stop ne peut que monter : c'est un
cliquet, jamais un niveau recalculé à la baisse. Une position est soldée quand
la bougie **clôture** sous ce niveau, pas sur une mèche intrabar.

Ce module ne décide pas *si* une position existe et n'envoie aucun ordre. Il
fournit uniquement le niveau et le test de clôture au backtest et au runtime.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_DOWN

__all__ = [
    "advance_profit_stop",
    "chandelier_stop",
    "should_exit_chandelier",
    "update_chandelier_stop",
]


def advance_profit_stop(*, entry_price: Decimal, initial_distance: Decimal,
                        current_stop: Decimal, close: Decimal,
                        price_tick: Decimal, config) -> Decimal:
    """Cliquet sur clôtures : actif dès +2R net, stop au moins au seuil net.

    Le nouveau stop s'applique à la bougie suivante : ne jamais rejouer le plus
    bas de la bougie qui a servi à le calculer. Aucun plafond de gain n'est posé.
    """
    if not config.profit_trailing_enabled or initial_distance <= 0:
        return current_stop
    fee, slip = config.execution_fee_pct, config.execution_slippage_pct
    # entry_price est le fill réel/simulé : son slippage est déjà payé.
    break_even = entry_price * (1 + fee) / ((1 - fee) * (1 - slip))
    net_gain = close * (1 - fee) * (1 - slip) - entry_price * (1 + fee)
    if net_gain < config.profit_trailing_start_r * initial_distance:
        return current_stop
    candidate = max(break_even, close - config.profit_trailing_distance_r * initial_distance)
    candidate = (candidate / price_tick).to_integral_value(rounding=ROUND_DOWN) * price_tick
    # Arrondir le seuil de rentabilité vers le bas pourrait le rendre perdant.
    if candidate < break_even:
        candidate += price_tick
    if candidate >= close:
        return current_stop
    return max(current_stop, candidate)


def _positive(value: Decimal, name: str) -> Decimal:
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    if not value.is_finite() or value <= 0:
        raise ValueError(f"{name} doit être fini et > 0 : {value!r}")
    return value


def chandelier_stop(
    highest_high: Decimal,
    atr: Decimal,
    atr_multiple: Decimal = Decimal("3.0"),
) -> Decimal:
    """Niveau Chandelier brut : plus haut récent moins un multiple d'ATR.

    Le résultat doit rester strictement positif ; un ATR démesuré qui produirait
    un stop nul ou négatif est une donnée invalide, pas un stop gratuit.
    """
    high = _positive(highest_high, "highest_high")
    volatility = _positive(atr, "atr")
    multiple = _positive(atr_multiple, "atr_multiple")
    stop = high - volatility * multiple
    if stop <= 0:
        raise ValueError(f"stop Chandelier non positif : {stop}")
    return stop


def update_chandelier_stop(
    current_stop: Decimal | None,
    highest_high: Decimal,
    atr: Decimal,
    atr_multiple: Decimal = Decimal("3.0"),
) -> Decimal:
    """Fait monter le stop Chandelier, jamais descendre.

    ``current_stop`` peut être le stop structurel initial : le trailing ne le
    remplace que s'il est plus haut, ce qui évite de desserrer la protection.
    """
    candidate = chandelier_stop(highest_high, atr, atr_multiple)
    if current_stop is None:
        return candidate
    current = _positive(current_stop, "current_stop")
    return max(current, candidate)


def should_exit_chandelier(close: Decimal, stop: Decimal) -> bool:
    """Vrai si la clôture passe **strictement** sous le stop Chandelier."""
    last = _positive(close, "close")
    level = _positive(stop, "stop")
    return last < level
