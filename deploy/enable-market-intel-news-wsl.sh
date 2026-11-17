#!/usr/bin/env bash
# Ajoute les sources de veille au worker/API/dashboard sans redémarrer le moteur.
set -euo pipefail
umask 077
SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MANIFEST="$SOURCE/deploy/multiple-positions.sha256"
cd "$SOURCE"
sha256sum --quiet -c "$MANIFEST"
BASE="$(readlink -f /opt/trading-ninja/current)"
[[ -d "$BASE/backend/app" ]] || { echo 'Release installée absente.' >&2; exit 1; }
cmp -s "$BASE/backend/requirements.lock" "$SOURCE/backend/requirements.lock" || {
  echo 'Dépendances différentes : construire une release qualifiée.' >&2; exit 1;
}
[[ $(systemctl show trading-ninja@engine -p Restart --value) == no ]] || exit 1
if [[ ${1:-} == --check && $# == 1 ]]; then
  bash -n "$0"
  echo 'Sources et dépendances vérifiées. Installation prévue : worker, API et dashboard ; moteur conservé.'
  exit 0
fi
[[ $# == 0 && $EUID == 0 && ${SUDO_USER:-} == seb ]] || {
  echo 'Exécuter : sudo bash deploy/enable-market-intel-news-wsl.sh' >&2; exit 1;
}
[[ -f /etc/trading-ninja/market-intel.env && -f /etc/trading-ninja/engine.env ]] || exit 1
ENGINE_PID="$(systemctl show trading-ninja@engine -p MainPID --value)"
REVISION="$(sha256sum "$MANIFEST" | cut -c1-12)"
NEW="/opt/trading-ninja/releases/paper-news9b-$REVISION"
if [[ ! -e "$NEW" ]]; then
  cp -a "$BASE" "$NEW"
  while read -r checksum file; do
    install -d -m 0755 "$(dirname "$NEW/$file")"
    install -m 0644 -o root -g root "$SOURCE/$file" "$NEW/$file"
  done < "$MANIFEST"
  install -m 0644 "$MANIFEST" "$NEW/deploy/multiple-positions.sha256"
fi
(cd "$NEW" && sha256sum --quiet -c deploy/multiple-positions.sha256)
# Migration 0017 et identité du moteur : lectures seulement, aucun cycle ajouté.
runuser -u trading-ninja -- "$NEW/backend/.venv/bin/python" "$NEW/backend/scripts/paper_component.py" engine --env /etc/trading-ninja/engine.env --probe
# Prouver une collecte et une écriture avec le LOGIN isolé avant la bascule.
runuser -u trading-ninja -- "$NEW/backend/.venv/bin/python" "$NEW/backend/scripts/market_intel.py" --config /etc/trading-ninja/market-intel.env --once
systemctl stop trading-ninja-market-intel.service
ln -sfn "$NEW" /opt/trading-ninja/current
systemctl start trading-ninja-market-intel.service
systemctl is-active --quiet trading-ninja-market-intel.service
runuser -u seb -- bash "$SOURCE/scripts/manage.sh" restart api
runuser -u seb -- bash "$SOURCE/scripts/manage.sh" restart dashboard
[[ $(systemctl show trading-ninja@engine -p MainPID --value) == "$ENGINE_PID" ]] || {
  echo 'PID moteur différent : examiner son état avant toute intervention.' >&2; exit 1;
}
printf 'Veille & IA installée : %s\n' "$NEW"
echo 'Moteur conservé ; aucun paramètre financier ni modèle modifié.'
echo 'Recharger http://localhost:8081/#intel et se reconnecter au dashboard.'
