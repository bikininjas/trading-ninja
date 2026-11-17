#!/usr/bin/env bash
# Installer une autorisation limitée aux commandes systemd de Trading Ninja.
set -euo pipefail

[[ $EUID == 0 && ${SUDO_USER:-} == seb ]] || {
  echo 'Exécuter depuis le compte seb : sudo bash deploy/install-manage-sudoers.sh' >&2
  exit 1
}

tmp="$(mktemp /etc/sudoers.d/.trading-ninja-manage.XXXXXX)"
trap 'rm -f "$tmp"' EXIT
cat >"$tmp" <<'EOF'
# Gestion des deux services PAPER ; aucune autre commande systemctl autorisée.
seb ALL=(root) NOPASSWD: /usr/bin/systemctl start trading-ninja@engine, /usr/bin/systemctl stop trading-ninja@engine, /usr/bin/systemctl start trading-ninja-market-intel.service, /usr/bin/systemctl stop trading-ninja-market-intel.service
EOF
chmod 0440 "$tmp"
chown root:root "$tmp"
visudo -cf "$tmp"
install -o root -g root -m 0440 "$tmp" /etc/sudoers.d/trading-ninja-manage
echo 'Autorisation installée. Vérifier : bash scripts/manage.sh status'
echo 'Redémarrer ensuite : bash scripts/manage.sh restart all'
