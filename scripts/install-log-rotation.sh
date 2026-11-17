#!/usr/bin/env bash
# Installe les deux entrées cron de l'utilisateur courant, sans toucher aux autres.
set -euo pipefail
umask 077
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
case "$root" in *[[:space:]]*|*'%'*|*"'"*|*'"'*|*'\\'*) echo 'Chemin incompatible avec cron' >&2; exit 2;; esac
command -v crontab >/dev/null
command -v logrotate >/dev/null || [[ -x /usr/sbin/logrotate ]]
command -v logger >/dev/null
tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/tn-rotation-install.XXXXXXXX")"
trap 'rm -rf -- "$tmp_dir"' EXIT
if ! crontab -l >"$tmp_dir/current" 2>"$tmp_dir/error"; then
  if ! grep -qi 'no crontab for' "$tmp_dir/error"; then
    cat "$tmp_dir/error" >&2
    exit 1
  fi
fi
grep -vF '# trading-ninja-log-rotation' "$tmp_dir/current" > "$tmp_dir/new" || true
printf '%s\n' \
  "5 0 * * * /usr/bin/bash '$root/scripts/rotate-logs.sh' 2>&1 | /usr/bin/logger -t trading-ninja-log-rotation # trading-ninja-log-rotation" \
  "@reboot /usr/bin/bash '$root/scripts/rotate-logs.sh' 2>&1 | /usr/bin/logger -t trading-ninja-log-rotation # trading-ninja-log-rotation" \
  >> "$tmp_dir/new"
crontab "$tmp_dir/new"
echo 'Rotation quotidienne installée pour l’utilisateur courant (00:05 et au démarrage).'
