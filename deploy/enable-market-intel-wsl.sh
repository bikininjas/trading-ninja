#!/usr/bin/env bash
# Déploie les sources qualifiées en PAPER et active la veille avec le 9B existant.
# Conserve univers, places, capital, risque et politique de reprise du moteur.
set -euo pipefail
umask 077
SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MANIFEST="$SOURCE/deploy/multiple-positions.sha256"
PRIVATE=/home/seb/.config/trading-ninja/market-intel.env
cd "$SOURCE"
sha256sum --quiet -c "$MANIFEST"
BASE="$(readlink -f /opt/trading-ninja/current)"
[[ -d "$BASE/backend/app" && -f "$PRIVATE" ]] || {
  echo 'Release initiale ou configuration privée de veille absente.' >&2; exit 1;
}
[[ $(stat -c '%U:%a' "$PRIVATE") == seb:600 ]] || {
  echo 'Configuration privée de veille non conforme.' >&2; exit 1;
}
cmp -s "$BASE/backend/requirements.lock" "$SOURCE/backend/requirements.lock" || {
  echo 'Dépendances différentes : construire une release qualifiée.' >&2; exit 1;
}
[[ $(systemctl show trading-ninja@engine -p Restart --value) == no ]] || {
  echo 'Politique de reprise moteur différente : examen requis.' >&2; exit 1;
}
if [[ ${1:-} == --check && $# == 1 ]]; then
  bash -n "$0"
  python3 - "$SOURCE/deploy/trading-ninja-market-intel.service.example" <<'PY'
from pathlib import Path
from tempfile import TemporaryDirectory
import shutil, subprocess, sys
with TemporaryDirectory(prefix='tn-intel-unit-') as directory:
    unit=Path(directory)/'trading-ninja-market-intel.service'
    shutil.copyfile(sys.argv[1],unit)
    subprocess.run(['systemd-analyze','verify',str(unit)],check=True)
PY
  echo 'Sources, dépendances, fichier privé et reprise contrôlée vérifiés. Aucun changement.'
  exit 0
fi
[[ $# == 0 && $EUID == 0 && ${SUDO_USER:-} == seb ]] || {
  echo 'Exécuter : sudo bash deploy/enable-market-intel-wsl.sh' >&2; exit 1;
}
[[ -f /etc/trading-ninja/engine.env ]] || exit 1
REVISION="$(sha256sum "$MANIFEST" | cut -c1-12)"
NEW="/opt/trading-ninja/releases/paper-market-intel9b-$REVISION"
if [[ ! -e "$NEW" ]]; then
  cp -a "$BASE" "$NEW"
  while read -r checksum file; do
    install -d -m 0755 "$(dirname "$NEW/$file")"
    install -m 0644 -o root -g root "$SOURCE/$file" "$NEW/$file"
  done < "$MANIFEST"
  install -m 0644 "$MANIFEST" "$NEW/deploy/multiple-positions.sha256"
fi
(cd "$NEW" && sha256sum --quiet -c deploy/multiple-positions.sha256)
STAGED=/etc/trading-ninja/engine-market-intel9b.env
WORKER=/etc/trading-ninja/market-intel.env
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
install -m 0600 -o trading-ninja -g trading-ninja /etc/trading-ninja/engine.env "$STAGED"
if [[ -f "$WORKER" ]]; then
  install -m 0600 -o root -g root "$WORKER" "/etc/trading-ninja/market-intel-before-$STAMP.env"
fi
install -m 0600 -o trading-ninja -g trading-ninja "$PRIVATE" "$WORKER"
# Ne charger ni afficher les secrets dans le shell. Modifier seulement les clés autorisées.
"$NEW/backend/.venv/bin/python" - "$NEW/backend" "$STAGED" "$WORKER" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from app.core.env import read_dotenv
from app.core.settings import load_settings

def update(path, changes):
    lines=path.read_text().splitlines()
    kept=[line for line in lines if line.strip().removeprefix('export ').partition('=')[0].strip() not in changes]
    path.write_text('\n'.join(kept+[f'{key}={value}' for key,value in changes.items()])+'\n')

engine,worker=map(Path,sys.argv[2:])
before=load_settings(env_file=engine,use_environment=False)
assert before.mode=='paper' and before.llm.model=='qwen3.5:9b', 'Moteur PAPER 9B requis'
changes={'MARKET_INTEL_ENABLED':'true','MARKET_INTEL_MODEL':'qwen3.5:9b','LLM_MODEL_NAME':'qwen3.5:9b'}
update(engine,changes)
after=load_settings(env_file=engine,use_environment=False)
assert (before.risk,before.capital_policy,before.exchange,before.paper_start_equity)==(after.risk,after.capital_policy,after.exchange,after.paper_start_equity)
update(worker,{**changes,'TRADING_MODE':'paper','I_UNDERSTAND_REAL_MONEY':'false',
    'LLM_BASE_URL':after.llm.base_url,'EXCHANGE_ID':'kraken','TRADING_SYMBOL':after.exchange.allowed_symbols[0],
    'TRADING_SYMBOLS':','.join(after.exchange.allowed_symbols)})
print('PAPER 9B : univers, nombre de places, capital et risque conservés.')
PY
runuser -u trading-ninja -- "$NEW/backend/.venv/bin/python" "$NEW/backend/scripts/paper_component.py" engine --env "$STAGED" --probe
# Une première collecte réelle avec le LOGIN isolé, avant de toucher au moteur.
runuser -u trading-ninja -- "$NEW/backend/.venv/bin/python" "$NEW/backend/scripts/market_intel.py" --config "$WORKER" --once
systemctl stop trading-ninja@engine
runuser -u trading-ninja -- "$NEW/backend/.venv/bin/python" "$NEW/backend/scripts/paper_component.py" engine --env "$STAGED" --probe
install -m 0600 -o root -g root /etc/trading-ninja/engine.env "/etc/trading-ninja/engine-before-intel-$STAMP.env"
install -m 0600 -o trading-ninja -g trading-ninja "$STAGED" /etc/trading-ninja/engine.env
# Conserver les autres paramètres de l’API du dépôt, dont son identité SQL.
if [[ -f "$SOURCE/.env.local" ]]; then
  install -m 0600 -o root -g root "$SOURCE/.env.local" "/etc/trading-ninja/source-before-intel-$STAMP.env"
  "$NEW/backend/.venv/bin/python" - "$SOURCE/.env.local" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1]); keys={'MARKET_INTEL_ENABLED':'true','MARKET_INTEL_MODEL':'qwen3.5:9b','LLM_MODEL_NAME':'qwen3.5:9b'}
lines=[line for line in p.read_text().splitlines() if line.strip().removeprefix('export ').partition('=')[0].strip() not in keys]
p.write_text('\n'.join(lines+[f'{key}={value}' for key,value in keys.items()])+'\n')
PY
fi
ln -sfn "$NEW" /opt/trading-ninja/current
install -m 0644 "$NEW/deploy/trading-ninja-market-intel.service.example" /etc/systemd/system/trading-ninja-market-intel.service
systemd-analyze verify /etc/systemd/system/trading-ninja-market-intel.service
systemctl daemon-reload
systemctl start trading-ninja@engine
systemctl is-active --quiet trading-ninja@engine
systemctl enable --now trading-ninja-market-intel.service
systemctl is-active --quiet trading-ninja-market-intel.service
runuser -u seb -- bash "$SOURCE/scripts/manage.sh" restart api
printf 'Release PAPER installée : %s\n' "$NEW"
echo 'Veille activée avec qwen3.5:9b ; vérifier premier cycle, digest et heartbeat.'
echo 'Aucun modèle téléchargé ; risque, capital, univers et places conservés.'
