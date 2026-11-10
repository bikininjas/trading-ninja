# Déploiement PAPER et recette de passage au réel

Ce dossier prépare le **WSL domestique de Seb**, avec systemd. Il n'installe rien
sur l'hôte actuel et ne permet pas d'activer LIVE. Les services utilisent le même
checkout figé ; le moteur refuse de reprendre un état bloqué ou des intentions
inconnues. La sonde doit aussi être contrôlée depuis une autre machine/service.

## Installation sur la machine retenue

Sur WSL2, vérifier d'abord `ps -p 1 -o comm=`. Si systemd n'est pas actif,
préparer `[boot]` puis `systemd=true` dans `/etc/wsl.conf` sans supprimer les autres
réglages. `wsl --shutdown` depuis Windows arrête toutes les distributions : réserver
une fenêtre sans exécution en cours, puis rouvrir le WSL et vérifier systemd.
Le PC doit rester allumé et WSL actif pendant la recette ; le sommeil, l'arrêt de
Windows et les mises à jour doivent être exercés comme pannes. Une sonde installée
uniquement dans ce WSL ne détecte pas sa propre disparition : contrôle externe requis.

Le script livré vérifie les dépendances puis prépare la release et les unités :

```bash
bash deploy/install-wsl-paper.sh --check
# Après configuration privée des cinq fichiers et go opérateur sur le WSL :
sudo bash deploy/install-wsl-paper.sh --install /chemin/vers/configurations-privees
```

Il refuse un checkout modifié, des configurations publiques, des services actifs
ou une substitution silencieuse de configuration. Il installe une release figée,
les dépendances verrouillées, les unités et le refus de redémarrage automatique du
moteur. Il ne démarre aucun service et ne les active pas au boot. Caddy et son nom
privé doivent être configurés séparément avec le fichier livré avant la recette.

1. Créer l'utilisateur système sans connexion `trading-ninja`, les répertoires
   `/opt/trading-ninja`, `/etc/trading-ninja` et `/var/lib/trading-ninja` privés.
   Installer Git, Node 24, uv, les clients PostgreSQL 17, Ollama et Caddy depuis
   leurs sources officielles. Aucun service n'est supposé déjà installé.
2. Cloner la version candidate dans `/opt/trading-ninja/releases/<commit>` et
   créer le lien `/opt/trading-ninja/current`. Installer Python hors de `/home`
   (`ProtectHome=true`) : `UV_PYTHON_INSTALL_DIR=/opt/trading-ninja/python uv python install 3.13.17`,
   puis créer `backend/.venv` avec cet interpréteur et synchroniser
   `backend/requirements.lock`. Vérifier Python 3.13.17 et Node 24.
3. Installer le modèle `qwen3.5:9b` dans Ollama. Ne pas substituer un modèle plus
   petit pour faire passer le délai : conserver son digest et mesurer l'inférence
   avec le prompt réel. Le délai configuré est de huit secondes.
4. Copier chaque `*.env.example` dans `/etc/trading-ninja/<composant>.env`, mode
   0600 et propriétaire `trading-ninja`. Renseigner les valeurs dans le stockage
   sécurisé, jamais dans Git ni dans une commande. Le dashboard ne reçoit aucun
   accès SQL. La sonde reçoit une identité SQL de lecture distincte permettant
   `bot_state`, `protection_observations`, `decision_cycles` et `schema_migrations` ; le surveillant
   reçoit les seuls droits nécessaires à ses observations et HALT. La migration
   `0014_paper_service_roles.sql` prépare les quatre groupes sans connexion ;
   leur matrice et la création privée des LOGIN sont décrites ci-dessous. Le dump
   initial reste optionnel selon D-084 ; ne pas le réintroduire comme porte.
5. Générer les secrets admin avec les outils existants `hash_password.py` et
   `totp_setup.py`. Conserver le TOTP opérateur hors machine, exercer login et
   réautorisation. Ne jamais afficher ces secrets dans le dossier de preuves.
6. Copier les fichiers systemd dans `/etc/systemd/system`, y compris le drop-in
   `trading-ninja@engine.service.d/controlled-restart.conf`, puis `systemctl daemon-reload`.
   `systemctl cat trading-ninja@engine` doit annoncer `Restart=no`. Ne pas activer
   le moteur au boot avant la recette de reprise.

Le lanceur `backend/scripts/paper_component.py` impose PAPER, désarme LIVE et vide
toutes les clés exchange, même si le fichier ou l'environnement demande le réel.
Il n'accepte pas de webhook dans argv. Les ports 8080/8081 restent sur loopback.

### Connexions SQL privées distinctes

La migration 0014 crée des groupes **NOLOGIN, NOBYPASSRLS**, sans mot de passe :

| Composant | Groupe à accorder au LOGIN dédié | Portée |
|---|---|---|
| Moteur | `tn_paper_engine` | Ledger/captures PAPER ; aucune observation saine écrivable |
| API | `tn_paper_api` | Administration PAPER et acquittement d'incident |
| Surveillant | `tn_paper_observer` | Lecture PAPER, observations/incidents, HALT sans reprise |
| Sonde | `tn_paper_probe` | Quatre tables en lecture, aucune donnée d'ordre |

Sur le stockage sécurisé du WSL, créer quatre LOGIN privés distincts, chacun
NOSUPERUSER, NOCREATEDB, NOCREATEROLE, NOREPLICATION, NOBYPASSRLS, avec un secret
aléatoire propre, et accorder uniquement le groupe correspondant. Utiliser une
connexion d'administration privée pour cette opération ; aucun secret dans une
migration, argv, le journal ou le chat. Reporter chaque URL TLS dans son seul
fichier privé. Ne pas donner de connexion SQL au dashboard. Les groupes ne sont
pas encore des identifiants de service utilisables : aucun LOGIN n'est créé ici.

`paper_component.py <composant> --env <fichier privé> --probe` refuse une identité
administrateur, une appartenance multiple, un groupe inadéquat, CREATE sur le
schéma, la maintenance privilégiée ou une lecture LIVE. Le moteur vérifie aussi
cela automatiquement au démarrage. Exercer les quatre probes avant d'activer
les services. La sonde conserve son comportement d'alerte en cas de panne SQL ;
son lancement n'est pas bloqué par un prévol qui supprimerait cette notification.

Les politiques RLS filtrent PAPER à la lecture et à l'écriture. Les partitions
ne sont accessibles qu'à travers leurs parents. Les artefacts sont immuables ;
les tables de suivi n'autorisent pas DELETE, sauf les sorties en attente après
comptabilisation. Le moteur ne peut ni écrire une observation saine ni modifier
un incident, et le surveillant ne peut ni falsifier le heartbeat ni reprendre.

## Vérification avant démarrage

Pour chaque composant, en utilisateur `trading-ninja`, depuis `backend` :

```bash
.venv/bin/python scripts/paper_component.py engine --env /etc/trading-ninja/engine.env --check
.venv/bin/python scripts/paper_component.py engine --env /etc/trading-ninja/engine.env --probe
.venv/bin/python scripts/paper_component.py api --env /etc/trading-ninja/api.env --probe
.venv/bin/python scripts/paper_component.py observer --env /etc/trading-ninja/observer.env --probe
.venv/bin/python scripts/paper_component.py watchdog --env /etc/trading-ninja/watchdog.env --probe
```

Les probes lisent SQL et l'identité du modèle sans ordre ni écriture. Elles ne
mesurent pas encore l'inférence, la réception d'alerte ou le TLS opérateur.
Lancer les contrôles du dépôt sur une base **jetable**, pas sur la base cible.
Le dump complet de la base initiale n'est pas bloquant selon la décision de Seb
du 10 novembre. Tester toute migration nouvelle sur une base séparée reste requis.

## Accès privé et démarrage contrôlé

Le Caddyfile proposé écoute en TLS sur `127.0.0.1:8443`, avec une CA interne et le
nom privé `TN_PRIVATE_HOST` choisi pour la cible. Déployer sa CA sur les seuls
postes opérateur et passer par VPN/tunnel privé ; vérifier le certificat depuis
le navigateur opérateur. Aucun port du dashboard ne doit être ouvert sur Internet.
Le cookie Secure est imposé, donc une connexion HTTP directe ne qualifie pas le login.

1. Démarrer API, dashboard et Caddy ; vérifier l'accès TLS et le TOTP opérateur.
2. Démarrer `trading-ninja-watchdog.timer`. Sans moteur/surveillant, une alerte de
   démarrage est attendue ; sa réception doit être réellement confirmée.
3. Démarrer manuellement `trading-ninja@engine`. Conserver premier cycle, digest
   modèle, version et configuration expurgée ; aucun repli LLM ne vaut inférence.
4. Après un premier cycle complet et un heartbeat courant, démarrer
   `trading-ninja@observer`. Vérifier le verdict et les deux files sous
   `/var/lib/trading-ninja`, leur persistance et leur sauvegarde.
5. Exercer arrêt du moteur, arrêt du surveillant, panne SQL et refus/timeout
   webhook. Horodater la panne et l'acceptation HTTPS réelle ; exiger au plus
   60 secondes pour les incidents qualifiés. Le timer seul n'est pas une mesure.
6. Après chaque panne, examiner intentions, positions et état avant reprise.
   Un HALT ne se lève pas automatiquement et aucun stop n'est retiré pour tester.

Un cycle RUNNING qui dépasse trente secondes, ou une décision absente trente
secondes après son échéance normale, passe en alerte même si le heartbeat est
vivant. La sonde relit directement les cycles ; elle ne dépend pas du verdict
précédent du surveillant. Mesurer les cycles normaux et les pannes sur WSL avant
qualification : un cycle légitime trop long impose de traiter la latence, sans
présenter automatiquement ce dépassement comme sain. Aucun ordre n'est annulé
pour fabriquer cette preuve ; le HALT bloque les nouvelles autorisations.

## Version, retour arrière et preuves

Conserver commit, versions Python/Node/PG, empreintes des migrations, digest
modèle, paramètres expurgés, horloges, résultats des pannes et reçus d'alertes.
Un rollback pointe vers une release conservée, après vérification de compatibilité
du schéma et des intentions. Il ne restaure pas aveuglément une ancienne base.
Après une modification financière, rouvrir la recette concernée.

R5 exige 14 jours consécutifs et 20 cycles expliqués, après R0–R4. Le canary réel
et sa poursuite restent des décisions explicites avec compte, budget et plafonds.

## Sauvegarde optionnelle et restauration séparée

Le script `backend/scripts/database_backup.py` exporte toutes les données et tous
les schémas de la base avec un snapshot PostgreSQL cohérent, puis les rôles sans
leurs mots de passe. Il conserve droits, propriétaires, RLS, options de vues,
extensions, effectifs et empreintes des lignes. Le dump et le manifest sont privés.
Les secrets de connexion restent dans les paramètres sécurisés, jamais dans argv.
Les fichiers partiels ne valent pas sauvegarde réussie.

```bash
backend/.venv/bin/python backend/scripts/database_backup.py create \
  --env /chemin/prive/backup.env --directory backups/instant-unique
backend/.venv/bin/python backend/scripts/database_backup.py restore-test \
  --env /chemin/prive/restore.env --directory backups/instant-unique
```

`backup.env` contient `DATABASE_URL`, `restore.env` contient `RESTORE_DATABASE_URL`.
Il faut les clients PostgreSQL 17 et les extensions compatibles avec la source.
La restauration accepte exclusivement un **cluster local neuf**, base
`trading_ninja_restore`, avec le seul rôle personnalisé bootstrap `tn_restore_admin`.
Elle ne nettoie ni ne remplace une base existante. Après restauration, données,
droits, rôles et extensions doivent correspondre ; `restore-verification.json`
est écrit seulement après succès. Les mots de passe des rôles se reconfigurent
séparément ; les configurations du fournisseur hors base ne sont pas exportées.
Conserver toute archive réelle hors machine dans un stockage privé et en contrôler
le téléchargement. La recette locale de l'outil ne vaut pas restauration de trninja.
