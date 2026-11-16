#!/usr/bin/env bash
# Installe le moteur PAPER uniquement. Ne démarre ni n'active aucun service.
set -euo pipefail
SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG=/home/seb/.config/trading-ninja/engine.env
UV=/home/seb/.local/bin/uv
[[ $EUID == 0 && ${SUDO_USER:-} == seb ]] || { echo 'Exécuter avec sudo depuis le compte seb.' >&2; exit 1; }
[[ -d /run/systemd/system && -x "$UV" && -f "$CONFIG" ]] || { echo 'systemd, uv ou configuration privée absente.' >&2; exit 1; }
[[ $(stat -c '%U:%a' "$CONFIG") == seb:600 ]] || { echo 'Configuration privée non conforme.' >&2; exit 1; }
git -c safe.directory="$SOURCE" -C "$SOURCE" diff --quiet HEAD -- backend deploy/systemd || { echo 'Code moteur modifié : installation refusée.' >&2; exit 1; }
! systemctl is-active --quiet trading-ninja@engine || { echo 'Service moteur déjà actif.' >&2; exit 1; }
[[ ! -e /etc/systemd/system/trading-ninja@.service && ! -e /opt/trading-ninja/current ]] || { echo 'Installation existante : aucune substitution.' >&2; exit 1; }
COMMIT=$(git -c safe.directory="$SOURCE" -C "$SOURCE" rev-parse HEAD)
RELEASE="/opt/trading-ninja/releases/$COMMIT"
[[ ! -e "$RELEASE" ]] || { echo 'Release existante : examen nécessaire avant réinstallation.' >&2; exit 1; }
echo 'Prévol de la configuration et de la base…'
runuser -u seb -- "$SOURCE/backend/.venv/bin/python" "$SOURCE/backend/scripts/paper_component.py" engine --env "$CONFIG" --probe
if ! id trading-ninja >/dev/null 2>&1; then
  getent group trading-ninja >/dev/null || groupadd --system trading-ninja
  useradd --system --gid trading-ninja --home-dir /var/lib/trading-ninja --shell /usr/sbin/nologin trading-ninja
fi
install -d -m 0755 /opt/trading-ninja/releases /opt/trading-ninja/python
install -d -m 0700 -o trading-ninja -g trading-ninja /etc/trading-ninja /var/lib/trading-ninja /var/cache/trading-ninja
install -d -m 0755 "$RELEASE"
git -c safe.directory="$SOURCE" -C "$SOURCE" archive "$COMMIT" | tar -x -C "$RELEASE"
export UV_PYTHON_INSTALL_DIR=/opt/trading-ninja/python UV_CACHE_DIR=/var/cache/trading-ninja
"$UV" python install 3.13.17
"$UV" venv "$RELEASE/backend/.venv" --python 3.13.17
"$UV" pip sync --python "$RELEASE/backend/.venv/bin/python" "$RELEASE/backend/requirements.lock"
chown -R root:root "$RELEASE" /opt/trading-ninja/python
install -m 0600 -o trading-ninja -g trading-ninja "$CONFIG" /etc/trading-ninja/engine.env
echo 'Prévol de la release installée…'
runuser -u trading-ninja -- "$RELEASE/backend/.venv/bin/python" "$RELEASE/backend/scripts/paper_component.py" engine --env /etc/trading-ninja/engine.env --probe
ln -s "$RELEASE" /opt/trading-ninja/current
install -m 0644 "$RELEASE/deploy/systemd/trading-ninja@.service" /etc/systemd/system/
install -d -m 0755 /etc/systemd/system/trading-ninja@engine.service.d
install -m 0644 "$RELEASE/deploy/systemd/trading-ninja@engine.service.d/controlled-restart.conf" /etc/systemd/system/trading-ninja@engine.service.d/
systemctl daemon-reload
[[ $(systemctl show trading-ninja@engine -p Restart --value) == no ]] || { echo 'Politique de reprise incorrecte.' >&2; exit 1; }
echo "Moteur PAPER installé sur $COMMIT, arrêté et non activé au boot."
echo 'Démarrage manuel ultérieur : sudo systemctl start trading-ninja@engine'
echo 'Journaux : journalctl -u trading-ninja@engine -f'
