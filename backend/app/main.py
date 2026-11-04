"""Point d'entrée du moteur — mode papier uniquement dans cette version.

    python -m app.main --once
    python -m app.main --cycles 5 --interval 60
    python -m app.main --status

L'exécution réelle n'est **pas** câblée ici : le module Kraken sait construire un
bracket, mais aucun chemin ne l'appelle. Passer en `live` exige deux gestes
explicites et un exécuteur revu — ce n'est pas une option de ligne de commande.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from decimal import Decimal

from app.core.settings import Settings, load_settings
from app.db.repository import PostgresRepository
from app.exchange.kraken import build_exchange, market_info
from app.execution.paper import PaperBroker
from app.llm.client import SignalClient
from app.runtime.orchestrator import Orchestrator

__all__ = ["main"]


async def _status(repo: PostgresRepository, mode: str) -> None:
    state, cooldown = await repo.state(mode)
    print(f"mode {mode} · état {state}" + (f" · cooldown jusqu'à {cooldown:%Y-%m-%d %H:%M} UTC"
                                          if cooldown else ""))
    for label, table in (("signaux", "llm_signals"), ("décisions", "risk_evaluations"),
                         ("positions", "positions"), ("ordres", "orders")):
        rows = await repo.recent(table, mode, limit=3)
        print(f"\n{label} (3 derniers) :")
        if not rows:
            print("  aucun")
            continue
        for row in rows:
            if table == "llm_signals":
                print(f"  {row['created_at']:%H:%M} {row['symbol']} {row['action']} "
                      f"{row['confidence']:.2f} · {row['market_regime']} · {row['thesis'][:80]}")
            elif table == "risk_evaluations":
                print(f"  {row['created_at']:%H:%M} {row['symbol']} "
                      f"{'APPROUVÉ' if row['approved'] else row['rejection_code']} "
                      f"{row['rejection_details'] or ''}"[:160])
            elif table == "positions":
                print(f"  {row['opened_at']:%H:%M} {row['symbol']} {row['status']} "
                      f"entrée {row['entry_price']} stop {row['stop_loss_price']} "
                      f"net {row['net_pnl_quote']} ({row['exit_reason'] or '—'})")
            else:
                print(f"  {row['created_at']:%H:%M} {row['symbol']} {row['purpose']} "
                      f"{row['status']} {row['requested_amount']} @ {row['average_fill_price']}")


def _taker_fee(exchange, symbols: tuple[str, ...]) -> Decimal:
    fees = [market_info(exchange, symbol).taker_fee for symbol in symbols]
    return max(fees) if fees else Decimal("0.0026")


async def _run(args: argparse.Namespace) -> int:
    settings: Settings = load_settings()
    if settings.mode != "paper":
        print(
            "REFUS : cette version n'exécute qu'en mode papier. Le chemin d'envoi réel "
            "n'est pas câblé, et le passer en live exige une revue dédiée.",
            file=sys.stderr,
        )
        return 2

    repo = await PostgresRepository.connect(settings.database())
    try:
        if args.status:
            await _status(repo, settings.execution_mode)
            return 0

        exchange = build_exchange(
            api_key=settings.exchange.api_key, secret=settings.exchange.secret
        )
        exchange.load_markets()
        client = SignalClient(settings.llm)
        if not args.no_warmup:
            print(f"préchauffage du modèle {settings.llm.model}…", flush=True)
            ready = await client.warmup()
            print("  modèle chargé" if ready else "  ATTENTION : préchauffage impossible, "
                  "les appels risquent de basculer en repli")
        broker = PaperBroker(
            taker_fee=_taker_fee(exchange, settings.exchange.allowed_symbols),
            slippage_bps=settings.paper_slippage_bps,
        )
        orchestrator = Orchestrator(settings, repo, exchange, client, broker)

        for cycle in range(1, args.cycles + 1):
            report = await orchestrator.run_once()
            print(f"\n=== cycle {cycle}/{args.cycles} ({report.elapsed_ms} ms) ===")
            for line in report.lines():
                print(line)
            if cycle < args.cycles:
                await asyncio.sleep(args.interval)
        return 0
    finally:
        await repo.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Moteur de trading — mode papier")
    parser.add_argument("--once", action="store_true", help="un seul cycle (défaut)")
    parser.add_argument("--cycles", type=int, default=1, help="nombre de cycles")
    parser.add_argument("--interval", type=float, default=60.0,
                        help="secondes entre deux cycles (défaut : 60)")
    parser.add_argument("--status", action="store_true", help="affiche l'état et l'historique")
    parser.add_argument("--no-warmup", action="store_true", help="ne pas préchauffer le modèle")
    args = parser.parse_args(argv)
    if args.cycles < 1:
        parser.error("--cycles doit être >= 1")
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
