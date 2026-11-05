"""Rapport des shadow trades — les achats que le bot n'a pas pris.

    python scripts/shadow_report.py
    python scripts/shadow_report.py --evaluate
    python scripts/shadow_report.py --evaluate --limit 50

`--evaluate` rejoue les bougies H4 disponibles pour les lignes `OPEN` et écrit
leur résultat (STOP_LOSS, TAKE_PROFIT, END_OF_DATA). Sans `--evaluate`, le script
ne fait que lire la base.

Aucun ordre, aucune position, aucun argent : c'est une mesure.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.settings import load_settings
from app.db.repository import PostgresRepository
from app.runtime.shadow import evaluate_open_shadow_trades

KIND_LABELS = {
    "GATE_REJECTED_BUY": "refus du pare-feu",
    "MODEL_HOLD": "HOLD du modèle",
}


async def report(args) -> int:
    settings = load_settings()
    repo = await PostgresRepository.connect(settings.database())
    try:
        if args.evaluate:
            from app.exchange.kraken import build_exchange

            exchange = build_exchange()
            closed = await evaluate_open_shadow_trades(
                repo, exchange, settings, limit=args.limit
            )
            print(f"{closed} shadow trade(s) évalué(s) sur les bougies disponibles.\n")

        summary = await repo.shadow_summary(settings.execution_mode)
        total = summary["total"]
        print(f"Shadow trades — mode {settings.execution_mode}")
        print("-" * 58)
        if total == 0:
            print("Aucun shadow trade enregistré pour l'instant.")
            print(
                "Le bot en crée quand un BUY est refusé par le pare-feu ou quand "
                "le modèle répond HOLD sur un setup d'entrée valide."
            )
            return 0

        print(f"total                 {total}")
        for kind, label in KIND_LABELS.items():
            print(f"  {label:19} {summary['by_kind'].get(kind, 0)}")
        print(f"avec un plan          {summary['planned']}")
        print(f"sans plan             {summary['no_plan']} (minimum exchange, contre-tendance…)")
        print(f"en attente            {summary['open']}")
        print(f"évalués               {summary['closed']}")
        print(
            f"résultat simulé       {summary['wins']} gagnants · "
            f"{summary['losses']} perdants · "
            f"{summary['net_pnl']:+.4f} EUR"
        )
        if summary["by_rejection"]:
            print("\nrefus du pare-feu par code :")
            for row in summary["by_rejection"]:
                print(f"  {row['code']:32} {row['count']}")
        print(
            "\nRappel : un shadow trade n'est pas un ordre. Le plan est recalculé "
            "avec une confiance forcée à 1,0, puis rejoué sur les bougies suivantes."
        )
        return 0
    finally:
        await repo.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Rapport des shadow trades")
    parser.add_argument("--evaluate", action="store_true",
                        help="rejoue les bougies et ferme les shadow trades OPEN")
    parser.add_argument("--limit", type=int, default=200,
                        help="nombre maximum de lignes évaluées (défaut 200)")
    return asyncio.run(report(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
