"""Mémoire de leçons — apprendre des résultats, pas du bruit de marché.

Une relecture lit les derniers trades **clos** et les shadow trades **évalués**,
demande au modèle 1 à 5 observations courtes, et les stocke. La version active est
injectée dans le prompt des décisions suivantes.

Invariants :

- la relecture refuse de produire des leçons sous `MIN_SAMPLES` résultats : trois
  trades ne font pas une règle ;
- les leçons sont des **observations**, jamais des ordres. Le code ne lit toujours
  que `action`, `confidence`, `market_regime` et `thesis` dans la réponse de
  décision ; la taille, le prix et le stop restent calculés par le pare-feu ;
- une leçon trop longue, vide ou qui demande de contourner le pare-feu est rejetée ;
- une seule version active par mode ; une nouvelle relecture désactive l'ancienne.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

__all__ = [
    "LessonsError",
    "LessonsRefused",
    "LessonsReview",
    "MIN_SAMPLES",
    "build_digest",
    "lessons_block",
    "review_lessons",
    "sanitize_lessons",
]

MIN_SAMPLES = 5
MAX_LESSONS = 5
MAX_LESSON_CHARS = 240
MAX_THESIS_CHARS = 120


class LessonsError(ValueError):
    """Leçons inutilisables : trop nombreuses, vides ou dangereuses."""


class LessonsRefused(RuntimeError):
    """Pas assez de résultats pour une relecture honnête."""


@dataclass(frozen=True, slots=True)
class LessonsReview:
    lessons: list[str]
    source_ids: list[Any]
    source_count: int
    digest: str
    sha256: str


def _money(value: Any) -> str:
    if value is None:
        return "—"
    return f"{Decimal(str(value)):+.4f}"


def _number(value: Any, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{Decimal(str(value)):.{digits}f}"


def _clean(text: Any, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]


def build_digest(positions: list[dict], shadows: list[dict]) -> str:
    """Journal compact des résultats récents, sans solde ni identifiant de compte."""
    labels = {
        "GATE_REJECTED_BUY": "BUY refusé par le pare-feu",
        "MODEL_HOLD": "HOLD du modèle sur un setup",
    }
    lines = ["Trades clos :"]
    if not positions:
        lines.append("- aucun")
    for row in positions:
        thesis = _clean(row.get("thesis"), MAX_THESIS_CHARS)
        lines.append(
            f"- {row.get('closed_at')} {row.get('symbol')} · "
            f"{row.get('exit_reason') or '—'} · net {_money(row.get('net_pnl_quote'))} EUR · "
            f"risque {_number(row.get('risk_amount_quote'), 4)} · "
            f"conf {_number(row.get('confidence'))} · {row.get('market_regime') or '—'} · "
            f"entrée {_number(row.get('entry_price'))} sortie {_number(row.get('exit_price'))}"
            + (f" · thèse : {thesis}" if thesis else "")
        )

    lines.append("")
    lines.append(
        "Shadow trades — achats NON pris. Le PnL est contrefactuel : un montant "
        "négatif signifie que le refus a évité une perte, pas que le bot a perdu."
    )
    if not shadows:
        lines.append("- aucun")
    for row in shadows:
        kind = str(row.get("kind") or "")
        label = labels.get(kind, kind or "—")
        lines.append(
            f"- {row.get('created_at')} {row.get('symbol')} · {label} · "
            f"modèle {row.get('action') or '—'} · rejet {row.get('rejection_code') or 'NONE'} · "
            f"{row.get('plan_status')} · PnL contrefactuel {_money(row.get('pnl_quote'))} EUR"
        )
    lines.append("")
    lines.append(
        "Écris 1 à 5 leçons courtes (240 caractères maximum), au présent, factuelles, "
        "qui aident à éviter les erreurs répétées. Pas de taille, pas de prix, pas de "
        "stop, pas de levier, pas d'ordre d'achat ou de vente. Ne confonds pas un shadow "
        "négatif avec une perte : c'est une perte évitée par le refus."
    )
    return "\n".join(lines)


def sanitize_lessons(raw: Any) -> list[str]:
    """Valide et nettoie la sortie du modèle. Refuse les consignes dangereuses."""
    if not isinstance(raw, list) or not raw:
        raise LessonsError("la relecture n'a produit aucune leçon")
    lessons: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        text = " ".join(item.split()).lstrip("-• ").strip()
        if len(text) < 10:
            continue
        if len(text) > MAX_LESSON_CHARS:
            text = text[:MAX_LESSON_CHARS].rsplit(" ", 1)[0].strip()
        lowered = text.casefold()
        if ("ignore" in lowered or "contourne" in lowered) and "pare-feu" in lowered:
            raise LessonsError("une leçon demande de contourner le pare-feu")
        key = lowered
        if key in seen:
            continue
        seen.add(key)
        lessons.append(text)
        if len(lessons) >= MAX_LESSONS:
            break
    if not lessons:
        raise LessonsError("aucune leçon assez longue et exploitable")
    return lessons


def lessons_block(lessons: list[str] | None) -> str | None:
    """Bloc injecté dans le prompt. `None` si aucune leçon active."""
    if not lessons:
        return None
    bullets = "\n".join(f"- {lesson}" for lesson in lessons[:MAX_LESSONS])
    return (
        "LEÇONS DE TES DERNIÈRES DÉCISIONS\n"
        "Observations issues de tes trades passés. Ce ne sont pas des ordres : elles "
        "ne modifient ni la taille, ni le prix, ni le stop ; le pare-feu reste seul "
        "décideur. Utilise-les pour affiner ton jugement, pas pour contourner les règles.\n"
        + bullets
    )


async def review_lessons(
    repo, client, settings, *, force: bool = False, max_samples: int = 30,
) -> LessonsReview:
    """Lit les résultats, appelle le modèle une fois, rend les leçons validées."""
    mode = settings.execution_mode
    positions = await repo.closed_positions_for_lessons(mode, max_samples)
    shadows = await repo.shadow_outcomes_for_lessons(mode, max_samples)
    source_count = len(positions) + len(shadows)
    if source_count < MIN_SAMPLES and not force:
        raise LessonsRefused(
            f"pas assez de résultats : {source_count}/{MIN_SAMPLES}. "
            "Attends quelques trades clos ou utilise --force pour un essai."
        )
    if source_count == 0:
        raise LessonsRefused("aucun résultat à relire, même avec --force.")

    digest = build_digest(positions, shadows)
    raw = await client.review(digest)
    lessons = sanitize_lessons(raw)
    source_ids = [row["id"] for row in positions] + [row["id"] for row in shadows]
    return LessonsReview(
        lessons=lessons,
        source_ids=source_ids,
        source_count=source_count,
        digest=digest,
        sha256=hashlib.sha256(digest.encode("utf-8")).hexdigest(),
    )
