"""Point d'entrée du moteur — papier par défaut, live sous verrous explicites.

    python -m app.main --once
    python -m app.main --cycles 5 --interval 60
    python -m app.main --status

Le mode `live` exige `TRADING_MODE=live`, `EXCHANGE_SANDBOX_MODE=false` et
`I_UNDERSTAND_REAL_MONEY=true`, puis une réconciliation propre au démarrage.
Aucun de ces verrous n'est posé par ce fichier.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone

from app.core.settings import Settings, load_settings
from app.db.repository import PostgresRepository
from app.exchange.kraken import build_exchange
from app.execution.factory import make_broker
from app.llm.client import SignalClient
from app.runtime.orchestrator import CycleReport, Orchestrator
from app.runtime.reconciliation import inspect_exchange
from app.runtime.scheduler import decision_delay_seconds, next_candle_close

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


async def _serve(orchestrator: Orchestrator, timeframe: str, *, max_cycles: int = 0) -> None:
    """Boucle alignée sur les clôtures de bougies, pas sur un intervalle fixe."""
    cycle = 0
    while max_cycles <= 0 or cycle < max_cycles:
        next_close = next_candle_close(datetime.now(timezone.utc), timeframe)
        cycle += 1
        report: CycleReport = await orchestrator.run_once()
        suffix = "" if max_cycles <= 0 else f"/{max_cycles}"
        print(f"\n=== cycle {cycle}{suffix} ({report.elapsed_ms} ms) ===")
        for line in report.lines():
            print(line)
        if max_cycles > 0 and cycle >= max_cycles:
            break
        now = datetime.now(timezone.utc)
        delay = decision_delay_seconds(timeframe)
        wait = (next_close - now).total_seconds() + delay
        if wait > 0:
            evaluation_at = next_close + timedelta(seconds=delay)
            print(
                f"prochaine bougie {timeframe} clôturée à "
                f"{next_close:%Y-%m-%d %H:%M} UTC ; évaluation à "
                f"{evaluation_at:%Y-%m-%d %H:%M} UTC — attente {wait / 60:.1f} min",
                flush=True,
            )
            await asyncio.sleep(wait)
        else:
            print(
                f"bougie {next_close:%H:%M} UTC clôturée pendant le cycle : "
                "évaluation immédiate",
                flush=True,
            )


async def _run(args: argparse.Namespace) -> int:
    settings: Settings = load_settings(env_file=args.config)
    if settings.mode == "testnet":
        print(
            "REFUS : Kraken spot n'a pas de testnet dans CCXT 4.5.91. "
            "Utiliser paper, ou live avec les deux verrous.",
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
        if settings.mode == "live":
            # On ne démarre jamais en live sur un état douteux : une position
            # sans stop couvrant doit être liquidée ou réconciliée avant tout.
            plan = await inspect_exchange(
                repo, exchange, settings.execution_mode,
                settings.exchange.allowed_symbols,
                quote_currency=settings.exchange.quote_currency,
            )
            if not plan.clean:
                for line in plan.lines():
                    print(line)
                print(
                    "REFUS : réconciliation non propre. Lancer "
                    "`scripts/reconcile.py --mode live --apply --confirm` "
                    "après vérification, puis redémarrer.",
                    file=sys.stderr,
                )
                return 2
        client = SignalClient(settings.llm)
        if not args.no_warmup:
            print(f"préchauffage du modèle {settings.llm.model}…", flush=True)
            ready = await client.warmup()
            print("  modèle chargé" if ready else "  ATTENTION : préchauffage impossible, "
                  "les appels risquent de basculer en repli")
        broker = make_broker(settings, exchange, settings.exchange.allowed_symbols)
        orchestrator = Orchestrator(
            settings, repo, exchange, client, broker,
            settings_loader=lambda **kwargs: load_settings(env_file=args.config, **kwargs),
        )

        if args.serve:
            await _serve(
                orchestrator, settings.exchange.daily_timeframe_decision,
                max_cycles=args.serve_cycles,
            )
            return 0

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
    parser.add_argument("--config", help="fichier de configuration explicite (les variables d'environnement restent prioritaires)")
    parser.add_argument("--once", action="store_true", help="un seul cycle (défaut)")
    parser.add_argument("--cycles", type=int, default=1, help="nombre de cycles")
    parser.add_argument("--interval", type=float, default=60.0,
                        help="secondes entre deux cycles (défaut : 60)")
    parser.add_argument("--status", action="store_true", help="affiche l'état et l'historique")
    parser.add_argument("--no-warmup", action="store_true", help="ne pas préchauffer le modèle")
    parser.add_argument(
        "--serve", action="store_true",
        help="boucle alignée sur les bougies clôturées (une évaluation par bougie)",
    )
    parser.add_argument(
        "--serve-cycles", type=int, default=0,
        help="nombre de cycles de --serve avant arrêt (0 = jusqu'à interruption)",
    )
    args = parser.parse_args(argv)
    if args.cycles < 1:
        parser.error("--cycles doit être >= 1")
    if args.serve_cycles < 0:
        parser.error("--serve-cycles doit être >= 0")
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
