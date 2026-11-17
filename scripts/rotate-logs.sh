#!/usr/bin/env bash
# Rotation des fichiers gérés par manage.sh. L'état et les archives restent privés.
set -euo pipefail
umask 077
PATH="/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"
export PATH

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
log_dir="${TN_LOG_DIR:-$root/scripts/.run}"
case "$log_dir$root" in *[[:space:]]*|*'"'*|*'\\'*) echo 'Chemin incompatible avec logrotate' >&2; exit 2;; esac
mkdir -p "$log_dir"
log_dir="$(cd "$log_dir" && pwd)"
archive_dir="$log_dir/.log-archives"
[[ ! -L "$archive_dir" ]] || { echo 'Répertoire d’archives symbolique refusé' >&2; exit 1; }
mkdir -p -m 700 "$archive_dir"
chmod 700 "$archive_dir"
exec 9>"$log_dir/.logrotate.lock"
flock -n 9 || { echo 'Rotation déjà en cours' >&2; exit 1; }
config="$(mktemp "$log_dir/.logrotate-config.XXXXXXXX")"
partial=''
trap 'rm -f -- "$config" ${partial:+"$partial"}' EXIT

# Après un échec de compression, logrotate conserve le fichier brut. Le
# reprendre avant toute nouvelle rotation et ne l'effacer qu'après vérification.
shopt -s nullglob
for raw in "$archive_dir"/{api,dashboard,engine}.log-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]; do
  [[ -f "$raw" && ! -L "$raw" ]] || continue
  [[ ! -e "$raw.tgz" ]] || { echo "Archive et journal brut en conflit : $raw" >&2; exit 1; }
  partial="$(mktemp "$archive_dir/.archive.XXXXXXXX")"
  "$root/scripts/compress-log-tgz.sh" < "$raw" > "$partial"
  tar -tzf "$partial" >/dev/null
  mv -- "$partial" "$raw.tgz"
  partial=''
  rm -- "$raw"
done

cat > "$config" <<EOF
"$log_dir/api.log" "$log_dir/dashboard.log" "$log_dir/engine.log" {
    daily
    missingok
    notifempty
    copytruncate
    rotate 3
    maxage 3
    olddir $archive_dir
    dateext
    dateformat -%Y%m%d
    compress
    compresscmd $root/scripts/compress-log-tgz.sh
    compressext .tgz
    compressoptions -6
    su $(id -un) $(id -gn)
}
EOF

case "${1:-}" in
  '') logrotate --state "$log_dir/.logrotate.status" "$config" ;;
  --force) logrotate --force --state "$log_dir/.logrotate.status" "$config" ;;
  *) echo 'Usage : rotate-logs.sh [--force]' >&2; exit 2 ;;
esac

# maxage ne s'applique qu'aux fichiers qui tournent ; purger aussi les anciens
# journaux des composants arrêtés. Ne toucher qu'aux archives nommées par ce script.
find "$archive_dir" -maxdepth 1 -type f \
  -regextype posix-extended \
  -regex '.*/(api|dashboard|engine)\.log-[0-9]{8}\.tgz' \
  -mmin +4320 -delete
