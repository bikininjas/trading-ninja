"""Verrou transactionnel interprocessus, indépendant des transactions du ledger.

Le verrou est tenu sur une connexion dédiée : une urgence peut persister HALT
immédiatement puis attendre que l'opération financière en cours se termine.
Compatible avec le pooler en mode transaction, sans bail expirant à l'aveugle.
"""

import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
from functools import wraps
from inspect import signature

_held = ContextVar("financial_guard", default=None)


class FinancialBusy(RuntimeError):
    pass


@asynccontextmanager
async def financial_guard(repo, mode, *, wait=False):
    owner = _held.get()
    if owner and owner[0] is repo and owner[1] is asyncio.current_task():
        if owner[3] != mode:
            raise FinancialBusy(
                "un verrou d'un autre mode ne donne aucun droit d'exécution"
            )
        yield
        return
    guard = getattr(repo, "financial_lock", None)
    if guard is None:
        # Doubles unitaires uniquement ; PostgresRepository expose toujours
        # financial_lock et refuse une configuration de verrou absente.
        yield
        return
    async with guard(mode, wait=wait) as connection:
        token = _held.set((repo, asyncio.current_task(), connection, mode))
        try:
            yield
        finally:
            _held.reset(token)


async def assert_financial_guard(repo, mode=None):
    owner = _held.get()
    if not owner or owner[0] is not repo or owner[1] is not asyncio.current_task():
        raise FinancialBusy("envoi sans propriété d'exécution : refus")
    if mode is not None and owner[3] != mode:
        raise FinancialBusy("mode du verrou d'exécution incorrect")
    # Détecte une perte de connexion/verrou AVANT chaque nouveau side effect.
    try:
        await owner[2].fetchval("SELECT 1")
    except Exception as exc:
        raise FinancialBusy(
            "perte du verrou d'exécution : nouvel envoi interdit"
        ) from exc


@asynccontextmanager
async def ledger_transaction(repo):
    transaction = getattr(repo, "transaction", None)
    if transaction:
        async with transaction():
            yield
    else:
        yield


def serialized_financial(*, mode_from=None, halt_reason=None):
    """Même frontière pour API, CLI et reprise ; réentrance limitée à la tâche."""

    def decorate(function):
        spec = signature(function)

        @wraps(function)
        async def wrapped(*args, **kwargs):
            bound = spec.bind(*args, **kwargs)
            values = bound.arguments
            repo = values["repo"]
            mode = mode_from(values) if mode_from else values["mode"]
            if halt_reason:
                ensure = getattr(repo, "ensure_mode", None)
                if ensure:
                    await ensure(mode)
                await repo.force_halt(mode, halt_reason)
            async with financial_guard(repo, mode, wait=True):
                return await function(*args, **kwargs)

        return wrapped

    return decorate
