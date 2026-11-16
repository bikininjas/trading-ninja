#!/usr/bin/env bash
# Installe le code vérifié, prépare 5 places PAPER et ajoute les 5 paires étudiées.
# --check vérifie les sources sans arrêter ni modifier de service.
set -euo pipefail
umask 077
SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MANIFEST="$SOURCE/deploy/multiple-positions.sha256"
cd "$SOURCE"
sha256sum --quiet -c "$MANIFEST"
if [[ ${1:-} == --check ]]; then
  echo 'Sources vérifiées. Préparation : 5 positions PAPER, SOL/LINK/AAVE/SUI/NEAR supplémentaires ; capital et risque conservés.'
  exit 0
fi
[[ $# == 0 && $EUID == 0 && ${SUDO_USER:-} == seb ]] || {
  echo 'Exécuter : sudo bash deploy/enable-multiple-positions-wsl.sh' >&2; exit 1;
}
BASE="$(readlink -f /opt/trading-ninja/current)"
[[ -d "$BASE/backend/app" && -f /etc/trading-ninja/engine.env ]] || {
  echo 'Installation existante introuvable.' >&2; exit 1;
}
[[ $(systemctl show trading-ninja@engine -p Restart --value) == no ]] || {
  echo 'Politique de reprise différente : examen nécessaire.' >&2; exit 1;
}
cmp -s "$BASE/backend/requirements.lock" "$SOURCE/backend/requirements.lock" || {
  echo 'Dépendances différentes : construire une release avec son runtime avant installation.' >&2; exit 1;
}
REVISION="$(sha256sum "$MANIFEST" | cut -c1-12)"
NEW="/opt/trading-ninja/releases/paper-multiple-$REVISION"
[[ ! -e "$NEW" ]] || { echo 'Release déjà présente : vérifier son état avant toute nouvelle installation.' >&2; exit 1; }
cp -a "$BASE" "$NEW"
while read -r checksum file; do
  install -d -m 0755 "$(dirname "$NEW/$file")"
  install -m 0644 -o root -g root "$SOURCE/$file" "$NEW/$file"
done < "$MANIFEST"
(cd "$NEW" && sha256sum --quiet -c "$MANIFEST")
STAGED=/etc/trading-ninja/engine-multiple.env
install -m 0600 -o trading-ninja -g trading-ninja /etc/trading-ninja/engine.env "$STAGED"

configure_env() {
  "$NEW/backend/.venv/bin/python" - "$@" <<'PY'
import os, sys, tempfile
from pathlib import Path
path = Path(sys.argv[1])
lines = path.read_text().splitlines()
values = {}
def env_key(line):
    line = line.strip().removeprefix('export ')
    return line.partition('=')[0].strip() if line and not line.startswith('#') else None
for line in lines:
    key, separator, value = line.strip().removeprefix('export ').partition('=')
    if separator and env_key(line):
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in '\"\'':
            value = value[1:-1]
        values[key.strip()] = value
if values.get('TRADING_MODE', 'paper').lower() != 'paper':
    raise SystemExit('Configuration non PAPER : arrêt.')
symbols = list(dict.fromkeys(symbol.strip() for symbol in [values.get('TRADING_SYMBOL', 'BTC/EUR'),
    *values.get('TRADING_SYMBOLS', '').split(','),
    'SOL/EUR', 'LINK/EUR', 'AAVE/EUR', 'SUI/EUR', 'NEAR/EUR'] if symbol.strip()))
lines = [line for line in lines if env_key(line) not in {'MAX_OPEN_POSITIONS', 'TRADING_SYMBOLS'}]
lines += ['MAX_OPEN_POSITIONS=5', 'TRADING_SYMBOLS=' + ','.join(symbols)]
if len(sys.argv) > 2:
    if sys.argv[2:] != ['--check']:
        raise SystemExit('Option de préparation inconnue.')
    raise SystemExit(0)
stat = path.stat()
fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.env.local.multiple-positions-')
try:
    with os.fdopen(fd, 'w') as target:
        target.write('\n'.join(lines) + '\n')
    os.chown(temporary, stat.st_uid, stat.st_gid)
    os.replace(temporary, path)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY
}
configure_env "$STAGED"
# Refuser une configuration locale incompatible avant tout arrêt de service.
[[ ! -f "$SOURCE/.env.local" ]] || configure_env "$SOURCE/.env.local" --check
# Le prévol ne crée ni ordre ni nouvelle migration et vérifie l'identité SQL limitée.
runuser -u trading-ninja -- "$NEW/backend/.venv/bin/python" "$NEW/backend/scripts/paper_component.py" engine --env "$STAGED" --probe
systemctl stop trading-ninja@engine
runuser -u trading-ninja -- "$NEW/backend/.venv/bin/python" "$NEW/backend/scripts/paper_component.py" engine --env "$STAGED" --probe
install -m 0600 -o trading-ninja -g trading-ninja /etc/trading-ninja/engine.env "/etc/trading-ninja/engine-before-multiple-$REVISION.env"
# Préserver aussi la configuration de l'API avant de toucher la release active.
if [[ -f "$SOURCE/.env.local" ]]; then
  install -m 0600 -o root -g root "$SOURCE/.env.local" "/etc/trading-ninja/source-before-multiple-$REVISION.env"
  configure_env "$SOURCE/.env.local"
fi
install -m 0600 -o trading-ninja -g trading-ninja "$STAGED" /etc/trading-ninja/engine.env
ln -sfn "$NEW" /opt/trading-ninja/current
systemctl start trading-ninja@engine
systemctl is-active --quiet trading-ninja@engine
runuser -u seb -- bash "$SOURCE/scripts/manage.sh" restart api
echo "Release installée : $NEW"
echo 'Moteur démarré. Vérifier le premier cycle et la révision appliquée : journalctl -u trading-ninja@engine -f'
echo 'Recharger le dashboard pour afficher toutes les positions.'
