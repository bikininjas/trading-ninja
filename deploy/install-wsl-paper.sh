#!/usr/bin/env bash
# Préparation uniquement : aucune activation/démarrage de service ni ordre.
set -euo pipefail
SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ACTION="${1:---check}"
CONFIG_DIR="${2:-}"
if [[ "$ACTION" != --check && "$ACTION" != --install ]]; then
  echo 'Usage: deploy/install-wsl-paper.sh --check | --install /repertoire-prive-config' >&2
  exit 2
fi
missing=0
for binary in systemctl git node uv ollama caddy runuser; do
  if ! command -v "$binary" >/dev/null; then echo "MANQUANT : $binary"; missing=1; fi
done
if [[ ! -d /run/systemd/system ]]; then
  echo 'MANQUANT : systemd actif dans WSL (le préparer avant installation).'
  missing=1
fi
if command -v node >/dev/null && [[ "$(node --version)" != v24.* ]]; then
  echo 'MANQUANT : Node 24'; missing=1
fi
if [[ "$ACTION" == --check ]]; then
  [[ "$missing" == 0 ]] && echo 'Dépendances présentes ; ni secrets, ni SQL, ni modèle, ni accès TLS encore qualifiés.'
  exit "$missing"
fi
[[ "$missing" == 0 ]] || exit 1
[[ "$EUID" == 0 ]] || { echo 'Installation système : exécuter via sudo sur le WSL cible.' >&2; exit 2; }
[[ -d "$CONFIG_DIR" ]] || { echo 'Répertoire privé des cinq configurations requis.' >&2; exit 2; }
[[ -z "$(git -C "$SOURCE" status --porcelain)" ]] || { echo 'Checkout non propre : refuser une release différente de HEAD.' >&2; exit 1; }
for component in engine api dashboard observer; do
  if systemctl is-active --quiet "trading-ninja@$component"; then
    echo "Service $component actif : effectuer un arrêt contrôlé avant mise à jour." >&2; exit 1
  fi
done
if systemctl is-active --quiet trading-ninja-watchdog.timer; then
  echo 'Sonde planifiée active : prévoir la fenêtre de maintenance avant installation.' >&2; exit 1
fi
for component in engine api dashboard observer watchdog; do
  file="$CONFIG_DIR/$component.env"
  [[ -f "$file" ]] || { echo "Configuration absente : $component.env" >&2; exit 2; }
  mode="$(stat -c %a "$file")"
  [[ "$mode" == 600 || "$mode" == 400 ]] || { echo "Configuration $component non privée (0600/0400 requis)." >&2; exit 2; }
  if [[ -f "/etc/trading-ninja/$component.env" ]] && ! cmp -s "$file" "/etc/trading-ninja/$component.env"; then
    echo "Configuration $component déjà installée et différente : aucune substitution silencieuse." >&2; exit 1
  fi
done
if ! id trading-ninja >/dev/null 2>&1; then
  getent group trading-ninja >/dev/null || groupadd --system trading-ninja
  useradd --system --gid trading-ninja --home-dir /var/lib/trading-ninja --shell /usr/sbin/nologin trading-ninja
fi
install -d -m 0755 /opt/trading-ninja /opt/trading-ninja/releases
install -d -m 0700 -o trading-ninja -g trading-ninja /etc/trading-ninja /var/lib/trading-ninja /var/cache/trading-ninja
install -d -m 0755 -o trading-ninja -g trading-ninja /opt/trading-ninja/python
COMMIT="$(git -C "$SOURCE" rev-parse HEAD)"
RELEASE="/opt/trading-ninja/releases/$COMMIT"
[[ ! -e "$RELEASE" ]] || { echo 'Release déjà présente ; vérifier son installation existante.' >&2; exit 1; }
install -d -m 0755 "$RELEASE"
git -C "$SOURCE" archive "$COMMIT" | tar -x -C "$RELEASE"
chown -R trading-ninja:trading-ninja "$RELEASE/backend"
UV_BIN="$(command -v uv)"
runuser -u trading-ninja -- env UV_PYTHON_INSTALL_DIR=/opt/trading-ninja/python UV_CACHE_DIR=/var/cache/trading-ninja \
  "$UV_BIN" python install 3.13.17
runuser -u trading-ninja -- env UV_PYTHON_INSTALL_DIR=/opt/trading-ninja/python UV_CACHE_DIR=/var/cache/trading-ninja \
  "$UV_BIN" venv "$RELEASE/backend/.venv" --python 3.13.17
runuser -u trading-ninja -- env UV_CACHE_DIR=/var/cache/trading-ninja \
  "$UV_BIN" pip sync --python "$RELEASE/backend/.venv/bin/python" "$RELEASE/backend/requirements.lock"
for component in engine api dashboard observer watchdog; do
  install -m 0600 -o trading-ninja -g trading-ninja "$CONFIG_DIR/$component.env" "/etc/trading-ninja/$component.env"
  runuser -u trading-ninja -- "$RELEASE/backend/.venv/bin/python" "$RELEASE/backend/scripts/paper_component.py" \
    "$component" --env "/etc/trading-ninja/$component.env" --check
done
chown -R root:root "$RELEASE" /opt/trading-ninja/python
ln -sfn "$RELEASE" /opt/trading-ninja/current
install -m 0644 "$RELEASE/deploy/systemd/trading-ninja@.service" /etc/systemd/system/
install -m 0644 "$RELEASE/deploy/systemd/trading-ninja-watchdog.service" /etc/systemd/system/
install -m 0644 "$RELEASE/deploy/systemd/trading-ninja-watchdog.timer" /etc/systemd/system/
install -d -m 0755 /etc/systemd/system/trading-ninja@engine.service.d
install -m 0644 "$RELEASE/deploy/systemd/trading-ninja@engine.service.d/controlled-restart.conf" \
  /etc/systemd/system/trading-ninja@engine.service.d/
systemctl daemon-reload
[[ "$(systemctl show trading-ninja@engine -p Restart --value)" == no ]] || { echo 'Restart moteur incorrect.' >&2; exit 1; }
echo "Release PAPER préparée : $COMMIT. Aucun service démarré ni activé au boot."
echo 'Suivre deploy/README.md : probes, modèle, TLS/TOTP et alertes avant démarrage contrôlé.'
