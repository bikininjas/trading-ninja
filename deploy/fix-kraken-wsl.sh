#!/usr/bin/env bash
# Installe la correction testée et redémarre uniquement après un prévol PAPER.
set -euo pipefail
SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE=d516397bca17b0881c92e44a6f2c71f9b48c76cd
OLD="/opt/trading-ninja/releases/$BASE"
NEW="/opt/trading-ninja/releases/${BASE}-kraken-network-20261112"
[[ $EUID == 0 && ${SUDO_USER:-} == seb ]] || { echo 'Exécuter avec sudo depuis le compte seb.' >&2; exit 1; }
[[ $(readlink -f /opt/trading-ninja/current) == "$OLD" && ! -e "$NEW" ]] || { echo 'Release différente ou correction déjà présente : examen nécessaire.' >&2; exit 1; }
! systemctl is-active --quiet trading-ninja@engine || { echo 'Le moteur tourne déjà : arrêt contrôlé requis.' >&2; exit 1; }
[[ $(systemctl show trading-ninja@engine -p Restart --value) == no ]] || { echo 'Politique de reprise incorrecte.' >&2; exit 1; }
cd "$SOURCE"
sha256sum -c <<'EOF'
a799fcc51872b30548042c6c6a05d2d294758a2fef5e4a4d3d1f5f96d25bc608  backend/app/market/feed.py
cdefd57f9459b6f639a83cd6d1fdbe8e90e7d3b23a210e7c8636b4feb9b0682b  backend/app/runtime/orchestrator.py
a6514fe2f5861a6a2aa7e8c2bc39d3bc8aaf4cabc38e62414153fa6ccb09d1a1  backend/app/insights/replay.py
8d3c7f9321b52a5b72029e5bc34c23c4d8997c9f05b04ae815aa4f339de1dbaa  backend/scripts/paper_component.py
EOF
for file in backend/app/market/feed.py backend/app/runtime/orchestrator.py backend/app/insights/replay.py backend/scripts/paper_component.py; do
  EXPECTED=$(git -c safe.directory="$SOURCE" -C "$SOURCE" show "$BASE:$file" | sha256sum | cut -d' ' -f1)
  [[ $(sha256sum "$OLD/$file" | cut -d' ' -f1) == "$EXPECTED" ]] || { echo "Release source modifiée : $file" >&2; exit 1; }
done
cp -a "$OLD" "$NEW"
for file in backend/app/market/feed.py backend/app/runtime/orchestrator.py backend/app/insights/replay.py backend/scripts/paper_component.py; do
  install -m 0644 -o root -g root "$SOURCE/$file" "$NEW/$file"
done
echo 'Prévol PAPER de la nouvelle release :'
runuser -u trading-ninja -- "$NEW/backend/.venv/bin/python" "$NEW/backend/scripts/paper_component.py" engine --env /etc/trading-ninja/engine.env --probe
ln -sfn "$NEW" /opt/trading-ninja/current
systemctl start trading-ninja@engine
systemctl is-active --quiet trading-ninja@engine || { echo 'Démarrage en échec : consulter journalctl -u trading-ninja@engine.' >&2; exit 1; }
echo 'Moteur PAPER démarré ; suivre le premier cycle : journalctl -u trading-ninja@engine -f'
