#!/usr/bin/env bash
# =============================================================================
# Gestion des composants Trading Ninja
# =============================================================================
#
#   scripts/manage.sh start   [api|dashboard|all]
#   scripts/manage.sh stop    [api|dashboard|all]
#   scripts/manage.sh restart [api|dashboard|all]
#   scripts/manage.sh status
#   scripts/manage.sh logs    [api|dashboard|all]
#
# Les processus tournent en arrière-plan (nohup), chaque composant a son fichier
# PID et son journal dans scripts/.run/ (ignoré par git).
#
# Un PID n'est tué que si sa ligne de commande correspond au composant attendu :
# après un redémarrage de la machine, un PID enregistré peut avoir été recyclé par
# un processus sans rapport. Le script supprime alors le fichier PID sans rien tuer.
#
# Variables d'environnement reconnues :
#   API_HOST, API_PORT        (défaut 127.0.0.1:8080)
#   DASHBOARD_PORT            (défaut : lu dans dashboard/.env, sinon 8081)
#   MANAGE_WAIT_SECONDS       (défaut 20)
# =============================================================================

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"
DASHBOARD="$ROOT/dashboard"
RUN_DIR="$ROOT/scripts/.run"
PYTHON="$BACKEND/.venv/bin/python"

API_HOST="${API_HOST:-127.0.0.1}"
API_PORT="${API_PORT:-8080}"
WAIT_SECONDS="${MANAGE_WAIT_SECONDS:-20}"

# Lit une valeur dans un fichier .env sans l'exécuter (pas de `source`).
env_value() {
  local file="$1" key="$2" default="$3" value=""
  if [[ -f "$file" ]]; then
    value="$(grep -E "^${key}=" "$file" | tail -1 | cut -d= -f2- \
      | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' \
            -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//")"
  fi
  if [[ -n "$value" ]]; then printf '%s' "$value"; else printf '%s' "$default"; fi
}

DASHBOARD_HOST="$(env_value "$DASHBOARD/.env" DASHBOARD_HOST 127.0.0.1)"
DASHBOARD_PORT="${DASHBOARD_PORT:-$(env_value "$DASHBOARD/.env" DASHBOARD_PORT 8081)}"

mkdir -p "$RUN_DIR"

info() { printf '[info] %s\n' "$*"; }
ok()   { printf '[ok]   %s\n' "$*"; }
warn() { printf '[attn] %s\n' "$*" >&2; }
die()  { printf '[erreur] %s\n' "$*" >&2; exit 1; }

pidfile() { printf '%s/%s.pid' "$RUN_DIR" "$1"; }
logfile() { printf '%s/%s.log' "$RUN_DIR" "$1"; }

# Motif présent dans la ligne de commande du processus attendu.
pattern() {
  case "$1" in
    api)       printf 'uvicorn app.api.main' ;;
    dashboard) printf 'node server.js' ;;
  esac
}

port() {
  case "$1" in
    api)       printf '%s' "$API_PORT" ;;
    dashboard) printf '%s' "$DASHBOARD_PORT" ;;
  esac
}

url() {
  case "$1" in
    api)       printf 'http://%s:%s' "$API_HOST" "$API_PORT" ;;
    dashboard) printf 'http://%s:%s' "$DASHBOARD_HOST" "$DASHBOARD_PORT" ;;
  esac
}

read_pid() {
  local file
  file="$(pidfile "$1")"
  [[ -f "$file" ]] || return 0
  cat "$file" 2>/dev/null || true
}

alive() {
  local pid="$1"
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

matches() {
  local pid="$1" want="$2"
  ps -p "$pid" -o args= 2>/dev/null | grep -Fq -- "$want"
}

# Affiche le PID si le composant tourne vraiment ; sinon nettoie le fichier PID.
running_pid() {
  local name="$1" pid
  pid="$(read_pid "$name")"
  [[ -n "$pid" ]] || return 1
  if alive "$pid" && matches "$pid" "$(pattern "$name")"; then
    printf '%s' "$pid"
    return 0
  fi
  if alive "$pid"; then
    warn "PID $pid vivant mais ce n'est pas $name ; fichier PID ignoré"
  fi
  rm -f "$(pidfile "$name")"
  return 1
}

port_in_use() {
  local port="$1"
  ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${port}\$"
}

preflight_api() {
  [[ -x "$PYTHON" ]] || die "python du venv introuvable : $PYTHON (voir backend/README.md)"
  [[ -f "$BACKEND/.env" ]] || die "backend/.env manquant"
  local key
  for key in JWT_SECRET_KEY TOTP_SECRET ADMIN_PASSWORD_HASH; do
    grep -qE "^${key}=[^[:space:]]" "$BACKEND/.env" \
      || die "backend/.env : ${key} manquant ou vide — l'API refuserait de démarrer"
  done
}

preflight_dashboard() {
  command -v node >/dev/null 2>&1 || die "node introuvable dans le PATH"
  [[ -f "$DASHBOARD/server.js" ]] || die "dashboard/server.js manquant"
}

wait_ready() {
  local name="$1" port pid code i
  port="$(port "$name")"
  pid="$(read_pid "$name")"
  for ((i = 0; i < WAIT_SECONDS * 5; i++)); do
    alive "$pid" || return 1
    if port_in_use "$port"; then
      case "$name" in
        api)       code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 \
                          "http://${API_HOST}:${port}/api/status" 2>/dev/null || true)" ;;
        dashboard) code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 \
                          "http://127.0.0.1:${port}/login" 2>/dev/null || true)" ;;
      esac
      [[ -n "$code" && "$code" != "000" ]] && return 0
    fi
    sleep 0.2
  done
  return 1
}

start_one() {
  local name="$1" pid log dir
  if pid="$(running_pid "$name")"; then
    printf '[déjà] %s tourne (PID %s)\n' "$name" "$pid"
    return 0
  fi
  "preflight_$name"

  local p
  p="$(port "$name")"
  if port_in_use "$p"; then
    die "port $p déjà utilisé par un autre processus — $name non démarré"
  fi

  log="$(logfile "$name")"
  printf '\n===== %s — démarrage %s =====\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$name" >>"$log"

  case "$name" in
    api)       dir="$BACKEND" ;;
    dashboard) dir="$DASHBOARD" ;;
  esac

  (
    cd "$dir"
    if [[ "$name" == api ]]; then
      nohup "$PYTHON" -m uvicorn app.api.main:create_app --factory \
        --host "$API_HOST" --port "$API_PORT" >>"$log" 2>&1 </dev/null &
    else
      nohup node server.js >>"$log" 2>&1 </dev/null &
    fi
    echo $! >"$(pidfile "$name")"
  )

  if wait_ready "$name"; then
    ok "$name démarré (PID $(read_pid "$name")) — $(url "$name")"
  else
    warn "$name n'a pas répondu en ${WAIT_SECONDS}s — dernières lignes de $log :"
    tail -n 15 "$log" 2>/dev/null | sed 's/^/       /' || true
    stop_one "$name" >/dev/null 2>&1 || true
    return 1
  fi
}

stop_one() {
  local name="$1" pid i
  pid="$(read_pid "$name")"

  if [[ -z "$pid" ]]; then
    rm -f "$(pidfile "$name")"
    printf '[arrêté] %s (aucun PID enregistré)\n' "$name"
    return 0
  fi
  if ! alive "$pid"; then
    rm -f "$(pidfile "$name")"
    printf '[arrêté] %s (PID %s déjà terminé)\n' "$name" "$pid"
    return 0
  fi
  if ! matches "$pid" "$(pattern "$name")"; then
    warn "PID $pid ne correspond pas à $name — rien tué, fichier PID supprimé"
    rm -f "$(pidfile "$name")"
    return 1
  fi

  kill "$pid" 2>/dev/null || true
  for ((i = 0; i < 50; i++)); do
    alive "$pid" || break
    sleep 0.1
  done
  if alive "$pid"; then
    warn "$name ignore TERM — SIGKILL"
    kill -9 "$pid" 2>/dev/null || true
    sleep 0.2
  fi
  rm -f "$(pidfile "$name")"

  if alive "$pid"; then
    printf '[échec] %s toujours vivant (PID %s)\n' "$name" "$pid" >&2
    return 1
  fi
  ok "$name arrêté (PID $pid)"
}

status_one() {
  local name="$1" pid port
  port="$(port "$name")"
  if pid="$(running_pid "$name")"; then
    printf '%-10s %-9s PID %-7s %s\n' "$name" "en cours" "$pid" "$(url "$name")"
  elif port_in_use "$port"; then
    printf '%-10s %-9s %-11s %s (port occupé par un autre processus)\n' \
      "$name" "conflit" "-" "$(url "$name")"
  else
    printf '%-10s %-9s %-11s %s\n' "$name" "arrêté" "-" "$(url "$name")"
  fi
}

logs_one() {
  local name="$1" file
  file="$(logfile "$name")"
  printf '%s\n' "--- $name ($file) ---"
  if [[ -f "$file" ]]; then
    tail -n "${MANAGE_LOG_LINES:-60}" "$file"
  else
    printf '(aucun journal)\n'
  fi
}

do_start() {
  case "$1" in
    api)       start_one api ;;
    dashboard) start_one dashboard ;;
    all)       start_one api; start_one dashboard ;;
    *)         usage; exit 2 ;;
  esac
}

do_stop() {
  case "$1" in
    api)       stop_one api ;;
    dashboard) stop_one dashboard ;;
    all)       stop_one dashboard || true; stop_one api || true ;;
    *)         usage; exit 2 ;;
  esac
}

usage() {
  cat <<'EOF'
Usage : scripts/manage.sh <action> [composant]

  start   api|dashboard|all   démarre en arrière-plan
  stop    api|dashboard|all   arrête (TERM, puis KILL après 5 s)
  restart api|dashboard|all   arrête puis redémarre
  status                      état des deux composants
  logs    api|dashboard|all   dernières lignes des journaux

Journaux et fichiers PID : scripts/.run/
EOF
}

ACTION="${1:-}"
TARGET="${2:-all}"

case "$ACTION" in
  start)   do_start "$TARGET" ;;
  stop)    do_stop "$TARGET" ;;
  restart) do_stop "$TARGET"; do_start "$TARGET" ;;
  status)  printf 'Trading Ninja — %s\n\n' "$(date '+%Y-%m-%d %H:%M:%S')"
           status_one api; status_one dashboard ;;
  logs)    if [[ "$TARGET" == all ]]; then logs_one api; printf '\n'; logs_one dashboard
           else logs_one "$TARGET"; fi ;;
  -h|--help|help) usage ;;
  *)       usage; exit 2 ;;
esac
