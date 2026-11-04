"""Tests de la machine à états : le graphe est une contrainte, pas une convention."""

from __future__ import annotations

import pytest

from app.engine.machine import (
    COOLDOWN, EVALUATING, HALT, IDLE, IN_POSITION, LOCKED, POST_TRADE_SWEEP,
    StateMachine, StateTransitionRefused,
)


class FakeRepo:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def transition(self, mode, expected, target, reason, code=None, context=None):
        self.calls.append((expected, target, reason))


@pytest.mark.asyncio
async def test_expected_transition_is_written():
    repo = FakeRepo()
    machine = StateMachine(repo, "PAPER", IDLE)
    await machine.go(EVALUATING, "cycle")
    assert machine.current == EVALUATING
    assert repo.calls == [(IDLE, EVALUATING, "cycle")]


@pytest.mark.asyncio
async def test_illegal_transition_is_refused_before_writing():
    repo = FakeRepo()
    machine = StateMachine(repo, "PAPER", IDLE)
    with pytest.raises(StateTransitionRefused, match="interdite"):
        await machine.go(IN_POSITION, "saut")
    assert repo.calls == []
    assert machine.current == IDLE


@pytest.mark.asyncio
async def test_locked_and_halted_states_have_no_automatic_exit():
    for state in (LOCKED, HALT):
        machine = StateMachine(FakeRepo(), "PAPER", state)
        for target in (IDLE, EVALUATING, COOLDOWN, POST_TRADE_SWEEP):
            assert machine.can(target) is False
        with pytest.raises(StateTransitionRefused):
            await machine.go(IDLE, "je me déverrouille tout seul")


def test_unknown_state_is_refused_at_construction():
    with pytest.raises(StateTransitionRefused):
        StateMachine(FakeRepo(), "PAPER", "DREAMING")
