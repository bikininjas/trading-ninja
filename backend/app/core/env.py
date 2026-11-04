"""Lecture des fichiers d'environnement, sans dépendance externe.

Ordre de priorité (le dernier gagne) :
    1. `backend/.env`         — réglages du projet, committable sous forme d'exemple
    2. `.env`  (racine)       — surcharges locales
    3. `.env.local` (racine)  — SECRETS, ignoré par git, jamais committé
    4. os.environ             — surcharge la plus forte (Docker, CI, shell)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

__all__ = ["repo_root", "read_dotenv", "load_env"]


def repo_root() -> Path:
    """Racine du dépôt (…/trading-ninja), calculée depuis ce fichier."""
    return Path(__file__).resolve().parents[3]


def read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def load_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """Fusionne les fichiers .env du dépôt puis l'environnement du processus."""
    root = repo_root()
    merged: dict[str, str] = {}
    for candidate in (
        root / "backend" / ".env",
        root / ".env",
        root / ".env.local",
    ):
        merged.update(read_dotenv(candidate))
    merged.update(os.environ)
    if extra:
        merged.update(extra)
    return merged
