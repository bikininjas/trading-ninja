"""Coûts prévisionnels par unité, partagés entre sizing, diagnostic et sorties.

Ce modèle n'est ni une prévision de prix ni une garantie de perte maximale : un
gap peut dépasser le glissement configuré. Les montants restent en Decimal.
"""
from decimal import Decimal


def entry_cost(price: Decimal, fee: Decimal, slippage: Decimal) -> Decimal:
    return price * (1 + slippage) * (1 + fee)


def exit_proceeds(price: Decimal, fee: Decimal, slippage: Decimal) -> Decimal:
    return price * (1 - slippage) * (1 - fee)


def planned_loss(price: Decimal, stop: Decimal, fee: Decimal,
                 slippage: Decimal) -> Decimal:
    return entry_cost(price, fee, slippage) - exit_proceeds(stop, fee, slippage)
