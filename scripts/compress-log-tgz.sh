#!/usr/bin/env bash
# logrotate envoie le journal à compresser sur stdin et attend l'archive sur stdout.
set -euo pipefail
umask 077
tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/trading-ninja-log.XXXXXXXX")"
trap 'rm -rf -- "$tmp_dir"' EXIT
cat > "$tmp_dir/journal.log"
tar -C "$tmp_dir" -czf - journal.log
