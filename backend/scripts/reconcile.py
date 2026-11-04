#!/usr/bin/env python3
"""Audit de réconciliation — lecture seule, aucun ordre, aucune écriture.

    cd backend
    .venv/bin/python scripts/reconcile.py
    .venv/bin/python scripts/reconcile.py --json

Le script compare trois choses : les positions ouvertes en base pour le mode
courant, les ordres ouverts chez l'exchange, et les soldes de base. En mode
`PAPER`, il doit ne rien trouver côté exchange : c'est un contrôle de propreté
du compte, pas une simulation. En mode `LIVE`, une position sans stop distant
produit un code de sortie 1 et un plan de liquidation.

Aucun chemin de ce script n'envoie d'ordre. La liquidation annoncée par le plan
sera appliquée par l'exécuteur live, quand il existera et aura été revu.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.settings import load_settings  # noqa: E402
from app.db.repository import PostgresRepository  # noqa: E402
from app.exchange.kraken import build_exchange  # noqa: E402
from app.runtime.reconciliation import inspect_exchange  # noqa: E402

__all__ = ["main"]


async def _run(args: argparse.Namespace) -> int:
    settings = load_settings()
    mode = (args.mode or settings.execution_mode).upper()
    if mode not in {"PAPER", "TESTNET", "LIVE"}:
        print(f"REFUS : mode inconnu {mode!r}", file=sys.stderr)
        return 2

    exchange = build_exchange(
        api_key=settings.exchange.api_key, secret=settings.exchange.secret
    )
    exchange.load_markets()
    repo = await PostgresRepository.connect(settings.database())
    try:
        plan = await inspect_exchange(
            repo, exchange, mode, settings.exchange.allowed_symbols,
            quote_currency=settings.exchange.quote_currency,
        )
    finally:
        await repo.close()

    if args.json:
        payload = asdict(plan)
        payload["clean"] = plan.clean
        payload["liquidate"] = list(plan.liquidate)
        print(json.dumps(payload, ensure_ascii=False, default=str, indent=2))
    else:
        for line in plan.lines():
            print(line)

    if plan.critical or plan.halt:
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit de réconciliation base ↔ exchange (lecture seule)"
    )
    parser.add_argument("--mode", choices=["paper", "testnet", "live"],
                        help="mode base à vérifier (défaut : mode configuré)")
    parser.add_argument("--json", action="store_true", help="sortie JSON")
    args = parser.parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001 — un audit doit dire pourquoi il échoue
        print(f"ÉCHEC de l'audit : {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
