#!/usr/bin/env bash
# =============================================================================
# Gestion des composants Trading Ninja
# =============================================================================
#
#   scripts/manage.sh start   [api|dashboard|engine|intel|all]
#   scripts/manage.sh stop    [api|dashboard|engine|intel|all]
#   scripts/manage.sh restart [api|dashboard|engine|intel|all]
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

engine_systemd_installed() {
  [[ -f /etc/systemd/system/trading-ninja@.service && -f /etc/systemd/system/trading-ninja@engine.service.d/controlled-restart.conf ]]
}

engine_systemctl() {
  if [[ $EUID == 0 ]]; then /usr/bin/systemctl "$@"; else sudo -n /usr/bin/systemctl "$@"; fi
}

intel_systemd_installed() {
  [[ "$(systemctl show trading-ninja-market-intel.service -p LoadState --value 2>/dev/null || true)" == loaded ]]
}

# Vérifier toutes les autorisations avant de toucher aux processus, notamment
# avant un restart all depuis un terminal WSL non interactif.
preflight_systemd_permissions() {
  local action="$1" target="$2" unit verb
  local -a units=() verbs=()
  [[ $EUID != 0 ]] || return 0
  if [[ "$target" == engine || "$target" == all ]] && engine_systemd_installed; then
    units+=(trading-ninja@engine)
  fi
  if [[ "$target" == intel || "$target" == all ]] && intel_systemd_installed; then
    units+=(trading-ninja-market-intel.service)
  fi
  case "$action" in
    start) verbs=(start) ;;
    stop) verbs=(stop) ;;
    restart) verbs=(stop start) ;;
  esac
  for unit in "${units[@]}"; do
    for verb in "${verbs[@]}"; do
      # « sudo -l » seul confirme aussi les règles exigeant un mot de passe.
      if ! sudo -n -l /usr/bin/systemctl "$verb" "$unit" >/dev/null 2>&1 ||
         { ! sudo -n -v >/dev/null 2>&1 && ! sudo -n -l -l /usr/bin/systemctl "$verb" "$unit" 2>/dev/null | grep -Eq '^[[:space:]]*Options:.*!authenticate'; }; then
        die "autorisation sudo sans mot de passe absente pour $verb $unit. Installer avec : sudo bash deploy/install-manage-sudoers.sh"
      fi
    done
  done
}

# Motif présent dans la ligne de commande du processus attendu.
pattern() {
  case "$1" in
    api)       printf 'uvicorn app.api.main' ;;
    dashboard) printf 'node server.js' ;;
    engine)    printf 'app.main --serve' ;;
  esac
}

port() {
  case "$1" in
    api)       printf '%s' "$API_PORT" ;;
    dashboard) printf '%s' "$DASHBOARD_PORT" ;;
    engine)    printf '' ;;
  esac
}

url() {
  case "$1" in
    api)       printf 'http://%s:%s' "$API_HOST" "$API_PORT" ;;
    dashboard) printf 'http://%s:%s' "$DASHBOARD_HOST" "$DASHBOARD_PORT" ;;
    engine)    printf 'PAPER (bougies H4)' ;;
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

process_dir() {
  case "$1" in
    api|engine) printf '%s' "$BACKEND" ;;
    dashboard)  printf '%s' "$DASHBOARD" ;;
  esac
}

discover_pid() {
  local name="$1" pid expected actual
  expected="$(process_dir "$name")"
  while read -r pid; do
    [[ -n "$pid" ]] || continue
    if matches "$pid" "$(pattern "$name")"; then
      actual="$(readlink -f "/proc/$pid/cwd" 2>/dev/null || true)"
      if [[ "$actual" == "$expected" ]]; then
        printf '%s' "$pid"
        return 0
      fi
    fi
  done < <(ps -eo pid=)
  return 1
}

# Affiche le PID si le composant tourne vraiment ; sinon nettoie le fichier PID.
running_pid() {
  local name="$1" pid
  pid="$(read_pid "$name")"
  if [[ -z "$pid" ]]; then
    if pid="$(discover_pid "$name")"; then
      printf '%s\n' "$pid" >"$(pidfile "$name")"
      printf '%s' "$pid"
      return 0
    fi
    return 1
  fi
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
  local key
  for key in JWT_SECRET_KEY TOTP_SECRET ADMIN_PASSWORD_HASH; do
    local found=false file
    for file in "$BACKEND/.env" "$ROOT/.env" "$ROOT/.env.local"; do
      if [[ -f "$file" ]] && grep -qE "^${key}=[^[:space:]]" "$file"; then found=true; fi
    done
    [[ "$found" == true ]] || die "$key absent de backend/.env, .env ou .env.local — l'API refuserait de démarrer"
  done
}

preflight_dashboard() {
  command -v node >/dev/null 2>&1 || die "node introuvable dans le PATH"
  [[ -f "$DASHBOARD/server.js" ]] || die "dashboard/server.js manquant"
}

preflight_engine() {
  [[ -x "$PYTHON" ]] || die "python du venv introuvable : $PYTHON (voir backend/README.md)"
  [[ -f "$BACKEND/.env" || -f "$ROOT/.env" || -f "$ROOT/.env.local" ]] \
    || die "configuration absente : crée backend/.env ou .env.local à la racine"
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
        engine) code="ready" ;;
      esac
      [[ -n "$code" && "$code" != "000" ]] && return 0
    fi
    sleep 0.2
  done
  return 1
}

start_one() {
  local name="$1" pid log dir
  if [[ "$name" == intel ]]; then
    if ! intel_systemd_installed; then info 'veille MarketIntel non installée'; return 0; fi
    if systemctl is-active --quiet trading-ninja-market-intel.service; then ok 'veille MarketIntel déjà active'; return 0; fi
    engine_systemctl start trading-ninja-market-intel.service
    systemctl is-active --quiet trading-ninja-market-intel.service || { warn 'veille MarketIntel non active ; consulter les journaux'; return 1; }
    ok 'veille MarketIntel lancée'
    return 0
  fi
  if [[ "$name" == engine ]] && engine_systemd_installed; then
    if systemctl is-active --quiet trading-ninja@engine; then
      ok 'engine systemd déjà actif'
      return 0
    fi
    if pid="$(running_pid engine)"; then
      die "un moteur hors systemd tourne déjà (PID $pid) : l'arrêter avant la bascule"
    fi
    engine_systemctl start trading-ninja@engine || return 1
    systemctl is-active --quiet trading-ninja@engine || { warn 'engine systemd non actif ; consulter les journaux'; return 1; }
    info 'engine systemd PAPER lancé ; initialisation en cours, vérifier le premier cycle dans les journaux'
    return 0
  fi
  if pid="$(running_pid "$name")"; then
    printf '[déjà] %s tourne (PID %s)\n' "$name" "$pid"
    return 0
  fi
  "preflight_$name"

  local p
  p="$(port "$name")"
  if [[ -n "$p" ]] && port_in_use "$p"; then
    die "port $p déjà utilisé par un autre processus — $name non démarré"
  fi

  log="$(logfile "$name")"
  printf '\n===== %s — démarrage %s =====\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$name" >>"$log"

  case "$name" in
    api)       dir="$BACKEND" ;;
    dashboard) dir="$DASHBOARD" ;;
    engine)    dir="$BACKEND" ;;
  esac

  (
    cd "$dir"
    if [[ "$name" == api ]]; then
      nohup setsid "$PYTHON" -m uvicorn app.api.main:create_app --factory \
        --host "$API_HOST" --port "$API_PORT" >>"$log" 2>&1 </dev/null &
    elif [[ "$name" == engine ]]; then
      nohup setsid env TRADING_MODE=paper I_UNDERSTAND_REAL_MONEY=false \
        EXCHANGE_SANDBOX_MODE=true EXCHANGE_API_KEY= EXCHANGE_SECRET= \
        KRAKEN_API_KEY= KRAKEN_SECRET= KRAKEN_SECRET_KEY= \
        BINANCE_API_KEY= BINANCE_SECRET_KEY= \
        "$PYTHON" -m app.main --serve >>"$log" 2>&1 </dev/null &
    else
      nohup setsid node server.js >>"$log" 2>&1 </dev/null &
    fi
    echo $! >"$(pidfile "$name")"
  )

  if [[ "$name" == engine ]]; then sleep 1; fi
  if [[ "$name" == engine ]] && alive "$(read_pid "$name")"; then
    info "engine PAPER lancé (PID $(read_pid "$name")) ; initialisation en cours, vérifier le premier cycle dans $log"
  elif wait_ready "$name"; then
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
  if [[ "$name" == intel ]]; then
    if ! intel_systemd_installed; then info 'veille MarketIntel non installée'; return 0; fi
    if systemctl is-active --quiet trading-ninja-market-intel.service; then
      engine_systemctl stop trading-ninja-market-intel.service || return 1
      ok 'veille MarketIntel arrêtée'
    else
      printf '[arrêté] veille MarketIntel\n'
    fi
    return 0
  fi
  if [[ "$name" == engine ]] && engine_systemd_installed; then
    if systemctl is-active --quiet trading-ninja@engine; then
      engine_systemctl stop trading-ninja@engine || return 1
      ok 'engine systemd arrêté'
    else
      printf '[arrêté] engine systemd\n'
    fi
    return 0
  fi
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
  if [[ "$name" == intel ]]; then
    if ! intel_systemd_installed; then printf '%-10s %s\n' intel 'non installée'; return 0; fi
    if systemctl is-active --quiet trading-ninja-market-intel.service; then
      pid="$(systemctl show trading-ninja-market-intel.service -p MainPID --value)"
      printf '%-10s %-9s PID %-7s %s\n' intel 'en cours' "$pid" 'veille PAPER (systemd)'
    else
      printf '%-10s %-9s %s\n' intel "$(systemctl is-failed --quiet trading-ninja-market-intel.service && printf échec || printf arrêté)" 'veille PAPER (systemd)'
    fi
    return 0
  fi
  if [[ "$name" == engine ]] && engine_systemd_installed; then
    if systemctl is-active --quiet trading-ninja@engine; then
      pid="$(systemctl show trading-ninja@engine -p MainPID --value)"
      printf '%-10s %-9s PID %-7s %s\n' "$name" 'en cours' "$pid" 'PAPER (systemd, bougies H4)'
    else
      printf '%-10s %-9s %-11s %s\n' "$name" "$(systemctl is-failed --quiet trading-ninja@engine && printf échec || printf arrêté)" '-' 'PAPER (systemd)'
    fi
    return 0
  fi
  port="$(port "$name")"
  if pid="$(running_pid "$name")"; then
    printf '%-10s %-9s PID %-7s %s\n' "$name" "en cours" "$pid" "$(url "$name")"
  elif [[ -n "$port" ]] && port_in_use "$port"; then
    printf '%-10s %-9s %-11s %s (port occupé par un autre processus)\n' \
      "$name" "conflit" "-" "$(url "$name")"
  else
    printf '%-10s %-9s %-11s %s\n' "$name" "arrêté" "-" "$(url "$name")"
  fi
}

logs_one() {
  local name="$1" file
  if [[ "$name" == intel ]]; then
    intel_systemd_installed || { printf '%s\n' 'veille MarketIntel non installée'; return 0; }
    journalctl --no-pager -u trading-ninja-market-intel.service -n "${MANAGE_LOG_LINES:-60}"
    return
  fi
  if [[ "$name" == engine ]] && engine_systemd_installed; then
    printf '%s\n' '--- engine (systemd) ---'
    journalctl --no-pager -u trading-ninja@engine -n "${MANAGE_LOG_LINES:-60}"
    return
  fi
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
    engine)    start_one engine ;;
    intel)     start_one intel ;;
    all)       start_one api; start_one dashboard; start_one intel; start_one engine ;;
    *)         usage; exit 2 ;;
  esac
}

do_stop() {
  case "$1" in
    api)       stop_one api ;;
    dashboard) stop_one dashboard ;;
    engine)    stop_one engine ;;
    intel)     stop_one intel ;;
    all)       local failed=0; stop_one engine || failed=1; stop_one intel || failed=1; stop_one dashboard || failed=1; stop_one api || failed=1; return "$failed" ;;
    *)         usage; exit 2 ;;
  esac
}

usage() {
  cat <<'EOF'
Usage : scripts/manage.sh <action> [composant]

  start   api|dashboard|engine|intel|all   démarre (engine toujours PAPER)
  stop    api|dashboard|engine|intel|all   arrête
  restart api|dashboard|engine|intel|all   arrête puis redémarre
  status                                    état des composants
  logs    api|dashboard|engine|intel|all   dernières lignes des journaux

Journaux et fichiers PID : scripts/.run/ ; moteur systemd : journalctl.
Pour les services systemd, installer les autorisations ciblées une seule fois :
sudo bash deploy/install-manage-sudoers.sh
EOF
}

ACTION="${1:-}"
TARGET="${2:-all}"

case "$ACTION" in
  start)   preflight_systemd_permissions start "$TARGET"; do_start "$TARGET" ;;
  stop)    preflight_systemd_permissions stop "$TARGET"; do_stop "$TARGET" ;;
  restart) preflight_systemd_permissions restart "$TARGET"; do_stop "$TARGET"; do_start "$TARGET" ;;
  status)  printf 'Trading Ninja — %s\n\n' "$(date '+%Y-%m-%d %H:%M:%S')"
           status_one api; status_one dashboard; status_one engine; status_one intel ;;
  logs)    if [[ "$TARGET" == all ]]; then logs_one api; printf '\n'; logs_one dashboard; printf '\n'; logs_one engine; printf '\n'; logs_one intel
           else logs_one "$TARGET"; fi ;;
  -h|--help|help) usage ;;
  *)       usage; exit 2 ;;
esac
