#!/usr/bin/env bash
# Reprise PAPER : release D-099 inchangée + migration 0018 déjà appliquée.
# --check ne modifie ni la cible ni les services. --install exige root depuis seb.
set -euo pipefail
umask 077
SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE=/opt/trading-ninja/releases/paper-news9b-27dc68524124
OLD_MANIFEST_SHA=27dc685241244a473df1eb9a0e547608111d4241ecb6405b64733dbd6c92c9a7
MIGRATION_SHA=6e5484ed5911114c4a0b055103a7e1e8b882719c114bd07e6cd1b4446e0249d7
NEW=/opt/trading-ninja/releases/paper-news9b-schema0018-6e5484ed5911

[[ $# == 1 && ( $1 == --check || $1 == --install ) ]] || {
  echo 'Usage : bash deploy/restore-engine-0018.sh --check | sudo bash deploy/restore-engine-0018.sh --install' >&2; exit 1;
}
[[ $(readlink -f /opt/trading-ninja/current) == "$BASE" ]] || {
  echo 'Release courante différente : réexaminer avant la reprise.' >&2; exit 1;
}
[[ $(stat -c '%u:%g:%a' "$BASE") == 0:0:755 ]] || { echo 'Release de base non protégée.' >&2; exit 1; }
[[ $(sha256sum "$BASE/deploy/multiple-positions.sha256" | cut -d' ' -f1) == "$OLD_MANIFEST_SHA" ]] || exit 1
(cd "$BASE" && sha256sum --quiet -c deploy/multiple-positions.sha256)
[[ $(sha256sum "$SOURCE/backend/db/migrations/0018_storage_budget.sql" | cut -d' ' -f1) == "$MIGRATION_SHA" ]] || {
  echo 'Migration 0018 différente de celle qualifiée.' >&2; exit 1;
}
shopt -s nullglob
migrations=("$BASE"/backend/db/migrations/*.sql)
[[ ${#migrations[@]} == 17 ]] || { echo 'Migrations de la release de base inattendues.' >&2; exit 1; }
for file in "${migrations[@]}"; do
  cmp -s "$file" "$SOURCE/backend/db/migrations/${file##*/}" || {
    echo "Migration de base divergente : ${file##*/}" >&2; exit 1;
  }
done
[[ $(systemctl show trading-ninja@engine -p Restart --value) == no ]] || exit 1
[[ $(systemctl show trading-ninja@engine -p MainPID --value) == 0 ]] || {
  echo 'Un moteur tourne déjà : aucune bascule effectuée.' >&2; exit 1;
}
if [[ $1 == --check ]]; then
  status="$(cd "$SOURCE/backend" && .venv/bin/python scripts/db_migrate.py status)"
  [[ $status != *'EN ATTENTE'* && $status != *'MODIFIÉE'* && $status != *'ORPHELINE'* ]] || {
    echo 'Migrations de la cible non conformes.' >&2; exit 1;
  }
  echo 'Release D-099 et 18 migrations conformes. Aucun service modifié.'
  exit 0
fi
[[ $EUID == 0 && ${SUDO_USER:-} == seb ]] || { echo 'Installation root depuis le compte seb requise.' >&2; exit 1; }
[[ ! -e "$NEW" && ! -L "$NEW" ]] || { echo 'Release cible déjà présente : examen requis.' >&2; exit 1; }
cp -a "$BASE" "$NEW"
install -m 0644 -o root -g root "$SOURCE/backend/db/migrations/0018_storage_budget.sql" "$NEW/backend/db/migrations/0018_storage_budget.sql"
(cd "$NEW" && sha256sum --quiet -c deploy/multiple-positions.sha256)
[[ $(sha256sum "$NEW/backend/db/migrations/0018_storage_budget.sql" | cut -d' ' -f1) == "$MIGRATION_SHA" ]] || exit 1
# Vérifie 18 empreintes, rôle SQL isolé, absence d'intentions incertaines et modèle.
runuser -u trading-ninja -- "$NEW/backend/.venv/bin/python" "$NEW/backend/scripts/paper_component.py" engine --env /etc/trading-ninja/engine.env --probe
link="/opt/trading-ninja/.current-recovery-$$"
trap 'rm -f "$link"' EXIT
ln -s "$NEW" "$link"
mv -Tf "$link" /opt/trading-ninja/current
if ! systemctl start trading-ninja@engine || ! systemctl is-active --quiet trading-ninja@engine; then
  ln -s "$BASE" "$link"
  mv -Tf "$link" /opt/trading-ninja/current
  echo 'Démarrage échoué : lien précédent restauré ; vérifier journalctl.' >&2
  exit 1
fi
echo "Release compatible installée : $NEW"
echo 'Moteur PAPER démarré : vérifier son premier cycle et le journal systemd.'
echo "La candidate d'optimisation locale n'est pas incluse ; paramètres et risque inchangés."
