"""Mémoire de leçons — relecture des résultats, affichage, purge.

    python scripts/lessons.py show
    python scripts/lessons.py review --dry-run
    python scripts/lessons.py review
    python scripts/lessons.py history
    python scripts/lessons.py clear

`review` fait **un seul** appel au modèle : il lit les derniers trades clos et les
shadow trades évalués, demande 1 à 5 leçons, les valide, puis les stocke. La version
active est injectée dans le prompt des décisions suivantes.

Aucun ordre, aucune position, aucun argent. Les leçons ne peuvent pas modifier la
taille, le prix ou le stop : le pare-feu reste seul décideur.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.settings import load_settings
from app.db.repository import PostgresRepository
from app.llm.client import LLMError, SignalClient
from app.llm.lessons import (
    MIN_SAMPLES,
    LessonsError,
    LessonsRefused,
    review_lessons,
)

LESSON_TTL_DAYS = 30


async def cmd_show(repo, settings) -> int:
    lessons = await repo.active_lessons(settings.execution_mode)
    if not lessons:
        print("Aucune leçon active.")
        return 0
    print(f"Leçons actives — mode {settings.execution_mode}")
    print("-" * 58)
    for lesson in lessons:
        print(f"- {lesson}")
    return 0


async def cmd_history(repo, settings, limit: int) -> int:
    rows = await repo.recent_lessons(settings.execution_mode, limit)
    if not rows:
        print("Aucune leçon enregistrée.")
        return 0
    print(f"Historique des leçons — mode {settings.execution_mode}")
    print("-" * 58)
    for row in rows:
        state = "ACTIVE" if row["active"] else "inactive"
        valid = row["valid_until"].strftime("%Y-%m-%d") if row["valid_until"] else "—"
        print(
            f"v{row['version']:<3} {row['created_at']:%Y-%m-%d %H:%M} "
            f"{state:8} jusqu'au {valid} · {row['source_count']} résultats"
        )
        for lesson in row["lessons"]:
            print(f"     - {lesson}")
    return 0


async def cmd_clear(repo, settings) -> int:
    await repo.deactivate_lessons(settings.execution_mode)
    print("Leçons actives désactivées.")
    return 0


async def cmd_review(repo, settings, args) -> int:
    positions = await repo.closed_positions_for_lessons(settings.execution_mode, 30)
    shadows = await repo.shadow_outcomes_for_lessons(settings.execution_mode, 30)
    source_count = len(positions) + len(shadows)
    if source_count == 0:
        print("Relecture impossible : aucun trade clos ni shadow trade évalué.")
        return 1
    if source_count < MIN_SAMPLES and not args.force:
        print(
            f"Relecture refusée : {source_count}/{MIN_SAMPLES} résultats. "
            "Attends quelques trades ou utilise --force pour un essai."
        )
        return 1

    client = SignalClient(settings.llm)
    if not await client.warmup():
        print("Modèle injoignable : relecture annulée.")
        return 2

    try:
        review = await review_lessons(
            repo, client, settings, force=args.force
        )
    except LessonsRefused as exc:
        print(f"Relecture refusée : {exc}")
        return 1
    except (LLMError, LessonsError) as exc:
        print(f"Relecture échouée : {exc}")
        return 1

    print(
        f"Relecture de {review.source_count} résultats — {len(review.lessons)} leçon(s)"
    )
    print("-" * 58)
    for lesson in review.lessons:
        print(f"- {lesson}")
    if args.show_digest:
        print("\nJournal soumis au modèle :")
        print(review.digest)
    if args.dry_run:
        print("\n--dry-run : rien n'a été stocké.")
        return 0

    valid_until = datetime.now(timezone.utc) + timedelta(days=LESSON_TTL_DAYS)
    lesson_id, version = await repo.insert_lessons(
        settings.execution_mode,
        lessons=review.lessons,
        source_trade_ids=review.source_ids,
        source_count=review.source_count,
        model_name=settings.llm.model,
        prompt_version="lessons-v1",
        valid_until=valid_until,
        review_sha256=review.sha256,
    )
    print(
        f"\nversion {version} stockée ({lesson_id}) — active jusqu'au "
        f"{valid_until:%Y-%m-%d}."
    )
    return 0


async def main(args) -> int:
    settings = load_settings()
    repo = await PostgresRepository.connect(settings.database())
    try:
        if args.command == "show":
            return await cmd_show(repo, settings)
        if args.command == "history":
            return await cmd_history(repo, settings, args.limit)
        if args.command == "clear":
            return await cmd_clear(repo, settings)
        return await cmd_review(repo, settings, args)
    finally:
        await repo.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mémoire de leçons du bot")
    parser.add_argument("command", choices=("review", "show", "history", "clear"))
    parser.add_argument("--dry-run", action="store_true",
                        help="affiche les leçons sans les stocker")
    parser.add_argument("--force", action="store_true",
                        help="autorise une relecture sous le minimum de résultats")
    parser.add_argument("--show-digest", action="store_true",
                        help="affiche le journal envoyé au modèle")
    parser.add_argument("--limit", type=int, default=10,
                        help="nombre de versions dans l'historique (défaut 10)")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(parse_args())))
