"""Régression de backtest : le comportement doit rester celui du golden.

Ce test ne fait aucun appel réseau : les bougies, les contraintes de marché et les
décisions du modèle sont figées dans le fichier golden. Il attrape trois familles de
changements :

- le **prompt** ou le **modèle** a changé → les décisions enregistrées ne sont plus
  représentatives, il faut ré-enregistrer ;
- le **risque**, les **frais** ou le **capital de départ** ont changé → les métriques
  bougent, il faut mesurer l'impact et bénir explicitement ;
- le **code** (pare-feu, dimensionnement, sortie, frais) a changé → le rejeu produit
  d'autres métriques, et le diff dit lesquelles.

La procédure de mise à jour est volontairement manuelle : relire le diff, puis
`replay --bless`. Un test qui se bénit tout seul ne protège de rien.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.backtest.engine import run_backtest
from app.backtest.golden import (
    ReplayLLM,
    diff_metrics,
    load_bundle,
    market_from_bundle,
    metrics_of,
    prompt_fingerprint,
    risk_fingerprint,
)
from app.core.settings import load_settings

FIXTURE = Path(__file__).parent / "fixtures" / "backtest_golden.json"
RECORD_HINT = (
    "Ré-enregistrer les décisions du modèle :\n"
    "  cd backend && .venv/bin/python scripts/backtest.py record\n"
    "puis relire le diff du golden avant de le committer."
)
BLESS_HINT = (
    "Si le nouvel équilibre est voulu :\n"
    "  cd backend && .venv/bin/python scripts/backtest.py replay --bless\n"
    "puis relire le diff du golden (métriques et empreintes) avant de committer."
)


def _bundle_and_settings():
    if not FIXTURE.exists():
        pytest.skip(f"golden absent : {FIXTURE}")
    return load_bundle(FIXTURE), load_settings()


def test_le_prompt_et_le_modele_sont_ceux_du_golden():
    bundle, settings = _bundle_and_settings()
    assert prompt_fingerprint(settings) == bundle.prompt_fingerprint, (
        "Le prompt ou le modèle a changé depuis l'enregistrement du golden.\n"
        + RECORD_HINT
    )


def test_le_profil_de_risque_est_celui_du_golden():
    bundle, settings = _bundle_and_settings()
    info = market_from_bundle(bundle)
    assert risk_fingerprint(settings, info) == bundle.risk_fingerprint, (
        "Profil de risque, frais ou capital de départ différent du golden.\n"
        "Mesurer l'impact avec `scripts/backtest.py replay --min-confidence …`,\n"
        + BLESS_HINT
    )


@pytest.mark.asyncio
async def test_le_rejeu_produit_les_metriques_attendues():
    bundle, settings = _bundle_and_settings()
    result = await run_backtest(
        symbol=bundle.symbol,
        info=market_from_bundle(bundle),
        h4=bundle.h4,
        h1=bundle.h1,
        settings=settings,
        propose=ReplayLLM(bundle.decisions),
        start_index=bundle.start_index,
        end_index=bundle.end_index,
    )
    actual = metrics_of(result)
    differences = diff_metrics(bundle.expected, actual)
    assert not differences, (
        "Le backtest de régression a changé de résultat :\n"
        + "\n".join(differences)
        + "\n\n"
        + BLESS_HINT
    )
