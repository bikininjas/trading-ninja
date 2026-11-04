"""Machine à états persistée.

Les états sont ceux de l'énumération SQL `system_state`. Une transition non prévue
lève avant d'écrire : une machine à états qui accepte tout n'est pas une machine à
états, c'est un champ texte.

`LOCKED_CIRCUIT_BREAKER` et `EMERGENCY_HALT` n'ont aucune sortie automatique : seul
un humain peut les lever. C'est volontaire — un bot qui se déverrouille tout seul
après un coupe-circuit annule le coupe-circuit.
"""

from __future__ import annotations

from typing import Any, Protocol

__all__ = ["ALLOWED", "StateMachine", "StateTransitionRefused"]

IDLE = "IDLE"
EVALUATING = "EVALUATING"
EXECUTING = "EXECUTING"
IN_POSITION = "IN_POSITION"
POST_TRADE_SWEEP = "POST_TRADE_SWEEP"
COOLDOWN = "COOLDOWN"
LOCKED = "LOCKED_CIRCUIT_BREAKER"
HALT = "EMERGENCY_HALT"

ALLOWED: dict[str, frozenset[str]] = {
    IDLE: frozenset({EVALUATING, LOCKED, HALT}),
    EVALUATING: frozenset({IDLE, EXECUTING, LOCKED, HALT}),
    EXECUTING: frozenset({IN_POSITION, IDLE, HALT}),
    IN_POSITION: frozenset({POST_TRADE_SWEEP, LOCKED, HALT}),
    POST_TRADE_SWEEP: frozenset({COOLDOWN, IDLE, HALT}),
    COOLDOWN: frozenset({IDLE, LOCKED, HALT}),
    LOCKED: frozenset(),
    HALT: frozenset(),
}


class StateTransitionRefused(RuntimeError):
    """Transition absente du graphe."""


class _Repository(Protocol):
    async def transition(self, mode: str, expected: str, target: str, reason: str,
                         code: str | None = None, context: dict | None = None) -> None: ...


class StateMachine:
    def __init__(self, repo: _Repository, mode: str, current: str):
        if current not in ALLOWED:
            raise StateTransitionRefused(f"état inconnu : {current}")
        self._repo = repo
        self._mode = mode
        self.current = current

    def can(self, target: str) -> bool:
        return target in ALLOWED[self.current]

    async def go(self, target: str, reason: str, *, code: str | None = None,
                 context: dict | None = None) -> "StateMachine":
        if not self.can(target):
            raise StateTransitionRefused(
                f"{self._mode} : transition {self.current} → {target} interdite "
                f"(autorisées : {', '.join(sorted(ALLOWED[self.current])) or 'aucune'})"
            )
        await self._repo.transition(self._mode, self.current, target, reason, code, context)
        self.current = target
        return self
