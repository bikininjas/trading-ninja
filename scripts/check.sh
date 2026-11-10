#!/usr/bin/env bash
# Vérification complète de l'état du dépôt en une commande.
#
#   bash scripts/check.sh
#
# Sortie : code 0 si tout est vert, 1 sinon. Chaque ligne dit POURQUOI elle est
# verte ou rouge, pour qu'un humain ou un agent puisse conclure sans lire le code.

set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
ROOT="$PWD"
fail=0
warn=0

titre() { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()    { printf '  \033[32mOK\033[0m     %s\n' "$*"; }
ko()    { printf '  \033[31mÉCHEC\033[0m  %s\n' "$*"; fail=1; }
alerte(){ printf '  \033[33mALERTE\033[0m %s\n' "$*"; warn=1; }

# --- 1. Outillage -----------------------------------------------------------
titre "1. Outillage"
if [ -x backend/.venv/bin/python ]; then
  PYVER=$(backend/.venv/bin/python -V 2>&1)
  ok "venv projet : $PYVER"
else
  ko "backend/.venv absent — lancer : uv venv backend/.venv --python "$(cat .python-version)" && uv pip sync --python backend/.venv/bin/python backend/requirements.lock"
fi
command -v node >/dev/null && ok "node : $(node -v)" || alerte "node absent du PATH"

# --- 2. Secrets -------------------------------------------------------------
titre "2. Secrets"
if git check-ignore -q .env.local; then
  ok ".env.local ignoré par git"
else
  ko ".env.local N'EST PAS ignoré par git"
fi
if [ "$(git config core.hooksPath)" = ".githooks" ]; then
  ok "crochet pre-commit actif"
else
  ko "crochet inactif — lancer : git config core.hooksPath .githooks"
fi
if git ls-files --error-unmatch .env.local >/dev/null 2>&1; then
  ko ".env.local est suivi par git — le retirer immédiatement"
else
  ok ".env.local non suivi"
fi

# --- 3. Tests ---------------------------------------------------------------
titre "3. Tests"
if [ -x backend/.venv/bin/python ]; then
  OUT=$(cd backend && .venv/bin/python -m pytest 2>&1)
  RC=$?
  if [ "$RC" -eq 0 ]; then
    COMPTE=$(printf '%s\n' "$OUT" | grep -Eo '[0-9]+ passed' | tail -1)
    TEST_SAUTES=$(printf '%s\n' "$OUT" | grep -Eo '[0-9]+ skipped' | tail -1 | grep -Eo '^[0-9]+' || true)
    ok "${COMPTE:-tests} — contrat de schéma inclus"
  else
    ko "tests en échec :"
    printf '%s\n' "$OUT" | tail -20 | sed 's/^/         /'
  fi
fi

# --- 4. Base de données -----------------------------------------------------
titre "4. Base de données"
if timeout 45 bash -c 'cd backend && .venv/bin/python scripts/db_migrate.py status' >/tmp/tn_status 2>&1; then
  if grep -q "EN ATTENTE\|MODIFIÉE\|ORPHELINE" /tmp/tn_status; then
    ko "migrations non appliquées ou divergentes :"
    grep -E "EN ATTENTE|MODIFIÉE|ORPHELINE" /tmp/tn_status | sed 's/^/         /'
  else
    ok "$(grep -cE '^[0-9]{4}_[a-z0-9_]+\.sql' /tmp/tn_status) migration(s) appliquée(s), aucune en attente"
  fi
else
  alerte "base injoignable (réseau / IPv6 ?) — voir backend/README.md § Base de données"
fi

# Les drawdowns sont calculés en SQL, pas seulement par `_drawdown()` : le jeu
# figé ci-dessous écrit puis annule une transaction, et compare les trois valeurs
# à un calcul manuel. Une base injoignable est une alerte, pas un échec.
if [ -x backend/.venv/bin/python ]; then
  OUT=$(timeout 45 bash -c 'cd backend && .venv/bin/python scripts/verify_drawdowns.py' 2>&1)
  RC=$?
  if [ "$RC" -eq 0 ]; then
    ok "drawdowns vérifiés sur un jeu figé (transaction annulée)"
  elif [ "$RC" -eq 3 ]; then
    alerte "drawdowns non vérifiés : base injoignable"
  else
    ko "vérification des drawdowns en échec :"
    printf '%s\n' "$OUT" | tail -10 | sed 's/^/         /'
  fi
fi

# --- 5. Cohérence documentaire ---------------------------------------------
titre "5. Documentation"
for f in README.md AGENTS.md docs/ROADMAP.md docs/JOURNAL.md docs/DECISIONS.md docs/ARCHITECTURE.md docs/CODE_INDEX.md docs/KRAKEN.md; do
  [ -f "$ROOT/$f" ] && ok "$f présent" || ko "$f manquant"
done

# Les documents qui décrivent l'ÉTAT COURANT doivent annoncer le vrai nombre de
# tests. Le journal est exclu (chiffres historiques). AGENTS.md est exclu aussi :
# son décompte est facultatif, donc il peut
# légitimement rester en retard — traitée en alerte, jamais en échec.
if [ -n "${COMPTE:-}" ]; then
  REEL=$(printf '%s' "$COMPTE" | grep -oE '^[0-9]+')
  # Les contrats PostgreSQL sont collectés mais opt-in sans base locale de test.
  REEL=$((REEL + ${TEST_SAUTES:-0}))
  for f in docs/ARCHITECTURE.md backend/README.md; do
    [ -f "$ROOT/$f" ] || continue
    ANNONCE=$(grep -oE '[0-9]+ tests?' "$ROOT/$f" 2>/dev/null | grep -oE '[0-9]+' | head -1)
    if [ -z "$ANNONCE" ]; then
      alerte "$f n'annonce aucun nombre de tests"
    elif [ "$ANNONCE" = "$REEL" ]; then
      ok "$f annonce $REEL tests, comme la réalité"
    else
      ko "$f annonce $ANNONCE tests, la réalité est $REEL"
    fi
  done

  ANNONCE_AGENTS=$(grep -oE '[0-9]+ tests?' "$ROOT/AGENTS.md" 2>/dev/null | grep -oE '[0-9]+' | head -1)
  if [ "$ANNONCE_AGENTS" = "$REEL" ]; then
    ok "AGENTS.md annonce $REEL tests"
  else
    alerte "AGENTS.md annonce ${ANNONCE_AGENTS:-?} tests au lieu de $REEL (consulter le guide de reprise)"
  fi
fi

# La roadmap unique doit conserver les huit portes de qualification.
for PORTE in {0..7}; do
  if grep -qE "^## R${PORTE} — " docs/ROADMAP.md; then
    ok "porte R${PORTE} présente dans la roadmap"
  else
    ko "porte R${PORTE} absente de docs/ROADMAP.md"
  fi
done

# --- 6. Index du code -------------------------------------------------------
# L'index est calculé depuis les sources ; s'il n'est pas régénéré après une
# modification, il décrit un code qui n'existe plus. Le vérifier ici évite qu'un
# index périmé serve de carte à un agent ou à un humain.
titre "6. Index du code"
if [ -x backend/.venv/bin/python ]; then
  if OUT=$(cd "$ROOT" && backend/.venv/bin/python scripts/index_codebase.py --check 2>&1); then
    ok "$OUT"
  else
    ko "$OUT"
    printf '         corriger : cd %s && backend/.venv/bin/python scripts/index_codebase.py\n' "$ROOT"
  fi
fi

# --- 7. Dashboard -----------------------------------------------------------
titre "7. Dashboard Node.js"
if command -v node >/dev/null 2>&1; then
  if (cd "$ROOT/dashboard" && node --check server.js >/dev/null 2>&1); then
    ok "server.js : syntaxe valide"
  else
    ko "server.js : erreur de syntaxe"
  fi
  if OUT=$(cd "$ROOT/dashboard" && node --test test/*.test.js 2>&1); then
    N=$(printf '%s\n' "$OUT" | grep -E '^ℹ pass' | grep -oE '[0-9]+' | head -1)
    ok "${N:-18} tests dashboard — zéro dépendance"
  else
    ko "tests dashboard en échec :"
    printf '%s\n' "$OUT" | tail -15 | sed 's/^/         /'
  fi
else
  alerte "node absent : dashboard non vérifié"
fi

# --- 8. Scripts de gestion ---------------------------------------------------
titre "8. Scripts de gestion"
if bash -n "$ROOT/scripts/manage.sh" 2>/dev/null; then
  ok "manage.sh : syntaxe valide"
else
  ko "manage.sh : erreur de syntaxe"
fi
if [[ -x "$ROOT/scripts/manage.sh" ]]; then
  ok "manage.sh exécutable"
else
  ko "manage.sh non exécutable (chmod +x scripts/manage.sh)"
fi
if bash -n "$ROOT/scripts/backtest_watch.sh" 2>/dev/null; then
  ok "backtest_watch.sh : syntaxe valide"
else
  ko "backtest_watch.sh : erreur de syntaxe"
fi
if [[ -x "$ROOT/scripts/backtest_watch.sh" ]]; then
  ok "backtest_watch.sh exécutable"
else
  ko "backtest_watch.sh non exécutable (chmod +x)"
fi
if [[ -f "$ROOT/backend/tests/fixtures/backtest_golden.json" ]]; then
  ok "golden de backtest présent"
else
  ko "golden de backtest absent (scripts/backtest.py record)"
fi

# --- Bilan ------------------------------------------------------------------
titre "Bilan"
if [ "$fail" -ne 0 ]; then
  printf '  \033[31mÉCHEC\033[0m — au moins une vérification est rouge.\n'
  exit 1
fi
if [ "$warn" -ne 0 ]; then
  printf '  \033[33mVERT avec alertes\033[0m — rien de bloquant, mais à lire.\n'
  exit 0
fi
printf '  \033[32mTOUT EST VERT\033[0m\n'
