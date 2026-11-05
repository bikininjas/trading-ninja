"""Garde-fou de sortie — le LLM peut solder une position, jamais vendre à découvert.

Un SELL du modèle n'est pas une décision de risque : c'est une liquidation d'une
position existante. Il n'a donc pas besoin du dimensionnement du Risk Gateway, mais
il doit passer un contrôle aussi strict sur ce qu'il a le droit de toucher :

- uniquement une position ouverte ;
- uniquement la quantité réellement détenue ;
- uniquement le symbole de cette position ;
- jamais une taille, un prix ou un stop proposé par le modèle.

Le module est PUR : aucune I/O, aucune horloge implicite.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from app.engine.risk_gate import Action, RiskConfig, TradeIntent

__all__ = ["ExitDecision", "ExitRejectionCode", "OpenPosition", "evaluate_exit"]


class ExitRejectionCode(StrEnum):
    NONE = "NONE"
    ACTION_IS_NOT_SELL = "ACTION_IS_NOT_SELL"
    NO_OPEN_POSITION = "NO_OPEN_POSITION"
    SYMBOL_MISMATCH = "SYMBOL_MISMATCH"
    MALFORMED_EXIT = "MALFORMED_EXIT"
    EXIT_CONFIDENCE_TOO_LOW = "EXIT_CONFIDENCE_TOO_LOW"


@dataclass(frozen=True, slots=True)
class OpenPosition:
    """La seule information dont le garde-fou a besoin. Pas de prix, pas de stop."""

    symbol: str
    amount: Decimal
    entry_price: Decimal


@dataclass(frozen=True, slots=True)
class ExitDecision:
    approved: bool
    rejection_code: ExitRejectionCode = ExitRejectionCode.NONE
    rejection_details: str | None = None
    amount: Decimal | None = None
    reason: str = "LLM_SELL"


def _reject(code: ExitRejectionCode, details: str) -> ExitDecision:
    return ExitDecision(approved=False, rejection_code=code, rejection_details=details)


def evaluate_exit(
    intent: TradeIntent,
    symbol: str,
    position: OpenPosition | None,
    config: RiskConfig,
) -> ExitDecision:
    """Valide une sortie proposée par le modèle. **Ne lève jamais.**"""
    try:
        return _evaluate_exit(intent, symbol, position, config)
    except Exception as exc:  # noqa: BLE001 — fail-closed assumé
        return _reject(ExitRejectionCode.MALFORMED_EXIT, f"{type(exc).__name__}: {exc}")


def _evaluate_exit(
    intent: TradeIntent,
    symbol: str,
    position: OpenPosition | None,
    config: RiskConfig,
) -> ExitDecision:
    try:
        action = Action(intent.action)
        confidence = Decimal(str(intent.confidence))
    except (ValueError, ArithmeticError) as exc:
        return _reject(ExitRejectionCode.MALFORMED_EXIT, f"intention illisible : {exc}")

    if not confidence.is_finite() or not (Decimal(0) <= confidence <= Decimal(1)):
        return _reject(
            ExitRejectionCode.MALFORMED_EXIT,
            f"confidence hors bornes : {intent.confidence}",
        )

    if action not in (Action.SELL, Action.HALT):
        return _reject(
            ExitRejectionCode.ACTION_IS_NOT_SELL,
            f"action={action} ; une sortie exige SELL ou HALT",
        )

    if position is None:
        return _reject(
            ExitRejectionCode.NO_OPEN_POSITION,
            f"aucune position ouverte sur {symbol} : un SELL ne peut pas créer un short",
        )

    if position.symbol != symbol:
        return _reject(
            ExitRejectionCode.SYMBOL_MISMATCH,
            f"position {position.symbol} != symbole demandé {symbol}",
        )

    if not position.amount.is_finite() or position.amount <= 0:
        return _reject(
            ExitRejectionCode.MALFORMED_EXIT,
            f"quantité détenue invalide : {position.amount}",
        )

    floor = config.min_halt_confidence if action is Action.HALT else config.min_exit_confidence
    if confidence < floor:
        return _reject(
            ExitRejectionCode.EXIT_CONFIDENCE_TOO_LOW,
            f"confidence {confidence} < {floor} pour {action}",
        )

    return ExitDecision(
        approved=True,
        amount=position.amount,
        reason="LLM_HALT" if action is Action.HALT else "LLM_SELL",
    )
