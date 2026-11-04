#!/usr/bin/env bash
# Veille de backtest — conçue pour un cron silencieux.
#
# N'affiche rien quand tout va bien (prompt, modèle et risque inchangés, décisions
# témoins identiques). Affiche un rapport en cas de dérive ou d'échec.
#
#   scripts/backtest_watch.sh
#
# Sort toujours 0 : le message est le signal, pas le code de sortie, pour éviter une
# notification d'échec du planificateur en plus du rapport.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/backend" || {
  echo "Backtest — répertoire backend introuvable : $ROOT/backend"
  exit 0
}

OUT=$(.venv/bin/python scripts/backtest.py watch 2>&1)
CODE=$?

if [ "$CODE" -ne 0 ]; then
  printf 'Backtest — dérive détectée (code %s)\n\n%s\n' "$CODE" "$OUT"
fi

exit 0
