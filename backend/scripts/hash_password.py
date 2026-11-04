"""Génère un hash de mot de passe administrateur (scrypt).

    .venv/bin/python scripts/hash_password.py

Le mot de passe n'est jamais affiché ni journalisé : seule la saisie cachée est
lue, et seul le hash sort. Coller la ligne `ADMIN_PASSWORD_HASH=...` dans
`backend/.env`, puis redémarrer l'API.
"""

from __future__ import annotations

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.security import hash_password  # noqa: E402


def main() -> int:
    password = getpass.getpass("Mot de passe administrateur : ")
    if len(password) < 12:
        print("REFUS : 12 caractères minimum pour une API d'administration.", file=sys.stderr)
        return 1
    if password != getpass.getpass("Confirmation : "):
        print("REFUS : les deux saisies diffèrent.", file=sys.stderr)
        return 1
    print("\nAjouter cette ligne à backend/.env :\n")
    print(f"ADMIN_PASSWORD_HASH={hash_password(password)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
