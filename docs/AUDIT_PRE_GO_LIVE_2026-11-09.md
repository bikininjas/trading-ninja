# Audit technique et décision pré go live — 9 novembre 2026

**Verdict : NO-GO pour l’argent réel, y compris un premier canary.** La suite de
régression passe, mais plusieurs scénarios adverses reproduisent des violations
de sécurité ou de risque. Le mode PAPER doit aussi être corrigé avant utilisation
du kill-switch API avec une clé Kraken capable d’annuler des ordres.

Base auditée : **`master` `39db4479191ad100d63c6a3b5c25d88e3e5d6843`**, identique à
`origin/master` après fetch. Revue du runtime, exécution, reprise, API, contrôles,
recherche, migrations et droits. Les vérifications Supabase sont des lectures de
catalogues et de l’état, sans DDL/DML. Aucun ordre exchange ni armement réel.

Le checkout `/workspace/trading-ninja`, branche `work` à `7efd4ef`, contient encore
une autre série de correctifs non commités. Ils ne font pas partie du master
publié. Ses fichiers et secrets ont été préservés ; leur présence locale ne ferme
pas un défaut du master. Leurs migrations 0010 divergent : `execution_safety` d’un
côté, `capital_ledger` de l’autre. Un portage doit être sélectif, avec nouvelles
migrations et nouveaux tests, sans copier une ancienne 0010 sur une base appliquée.

## 1. Preuves réellement exécutées

| Vérification | Résultat | Ce qu’elle établit |
|---|---|---|
| Suite Python sans base | 671 passed, 24 skipped | Contrats unitaires et golden ; SQL explicitement non testé dans ce passage |
| PostgreSQL 17 jetable, onze migrations depuis zéro, suite entière | **695 passed** | Comprend les 24 contrats SQL, concurrence API et rollback comptable |
| `db_migrate.py verify` sur cette base locale | Conforme : 43 relations, 6 vues, 16 partitions | Écritures annulées, droits des nouvelles tables et structure attendue |
| `node --test test/*.test.js` | **31 passed** | Proxy privé, origine, session et commandes couvertes |
| Playwright/Chromium | Réussi | Capital, suspension/TOTP, rejeu, diagnostics, desktop/mobile sans erreur ni débordement |
| `scripts/backtest.py replay` | Golden identique, sans bless | Régression de la fenêtre historique figée |
| Reproductions adverses en mémoire | **8 comportements problématiques confirmés** | Doubles exchange/repository ; aucune connexion réseau autorisée par le script |
| Reproductions SQL supplémentaires | **2 confirmées**, transactions annulées | Exécution privilégiée par `anon` et omission d’un fill partiel annulé |
| Supabase `trninja`, lecture seule | Migrations **0001–0009**, empreintes identiques au dépôt | **0010 et 0011 absentes de la cible** à l’instant de l’audit |

Un premier passage SQL sur le port 55433 a rencontré six refus de fixture : les
anciens tests imposent 55432. La base jetable a été recréée sur 55432 et la suite
entière a ensuite passé. Aucun test n’a été modifié pour contourner cette garde.
Un avertissement Starlette/httpx de dépréciation reste présent, sans échec.

Les reproductions sont des **constats du défaut actuel** : leurs assertions exigent
le comportement problématique et devront être inversées lors de la correction.
Elles ne sont pas des tests de sécurité réussis à ajouter au total de 695.

Fichiers reproductibles :

- [Script mémoire](audits/2026-11-09/reproduce_memory.py) et [résultats](audits/2026-11-09/memory_results.json).
- [Script SQL](audits/2026-11-09/reproduce_sql.py) et [résultats](audits/2026-11-09/sql_results.json).
- [Snapshot Supabase de schéma/droits](audits/2026-11-09/supabase_snapshot.json), sans contenu financier ni secret.

```bash
# Depuis la racine, avec les dépendances backend et de développement installées.
backend/.venv/bin/python docs/audits/2026-11-09/reproduce_memory.py
# Base PostgreSQL locale JETABLE nommée trading_ninja_review, onze migrations appliquées.
backend/.venv/bin/python docs/audits/2026-11-09/reproduce_sql.py \
  --postgres-test-url "$TN_REVIEW_DSN"
```

## 2. Défauts bloquants dans le code

### A02 — P0 : le kill-switch API traverse l’isolation PAPER/LIVE

**Source** : [api/main.py:122](../backend/app/api/main.py#L122).
`_default_kill_switch()` construit un client Kraken avec la clé de kill ou la clé
principale puis appelle `fetch_open_orders()` et `cancel_order()`, indépendamment
de `config.mode`. Il ne passe ni par le choix du broker papier, ni par les trois
verrous live.

**Reproduction** : avec `TRADING_MODE=paper`, un faux exchange reçoit l’annulation
de `protective-stop`. La position d’une unité reste détenue et l’état devient
EMERGENCY_HALT. Avec une vraie clé autorisée, ce même chemin vise les vrais ordres
Kraken, même si la position du ledger PAPER n’est que virtuelle.

**Impact** : une administration supposée papier peut modifier le compte réel.
En LIVE, le même handler peut retirer le stop sans liquider le solde, puis arrêter
sa surveillance. Le TOTP ne rend pas ce comportement compatible avec le mode.

**Correction attendue** : branche PAPER strictement locale ; sémantique distincte
entre suspendre les entrées, conserver les protections et liquider. Faire passer
les urgences financières par le même coordinateur que le moteur. Test obligatoire :
les doubles exchange de mutation doivent lever si un handler PAPER les appelle.

### A01 — P1 : la revalidation finale perd le solde live observé

**Sources** : [orchestrator.py:564](../backend/app/runtime/orchestrator.py#L564),
[orchestrator.py:1242](../backend/app/runtime/orchestrator.py#L1242).
Le début du cycle remplace le compte du ledger par le solde exchange. `_enter()`
relit ensuite uniquement `repo.account()` et recalcule la quantité sur cet ancien
snapshot, sans refaire cette synchronisation live.

**Reproduction** : solde live simulé de **600 EUR**, ancien snapshot de **1 000 EUR**.
Le premier filtre approuve **1,194190** unités et **4,50 EUR** de risque. La
revalidation approuve **1,990317** unités et **7,50 EUR** de risque sur le ledger.
La quantité augmente de **66,7 %**, sans hausse du solde réellement observé.

**Impact** : dépassement du budget proportionnel de risque, voire de l’exposition
sur le vrai compte. Un refus exchange pour manque de fonds ne remplace pas le
respect du budget de risque.

**Correction attendue** : une seule construction du compte courant réutilisée avant
chaque `evaluate()` : solde exchange, réserves/cagnotte, valorisation et drawdowns
cohérents. Tester soldes réels inférieurs et supérieurs au snapshot, y compris un
changement pendant l’inférence. L’évaluation persistée doit être celle envoyée.

### A03 — P1 : un timeout après vente partielle peut laisser un reliquat nu

**Sources** : [live.py:173](../backend/app/execution/live.py#L173),
[live.py:630](../backend/app/execution/live.py#L630),
[orchestrator.py:937](../backend/app/runtime/orchestrator.py#L937).
`exit()` annule les stops, attend la vente et réarme la quantité initiale en cas
d’échec. `_wait_for_fill()` ne restitue pas le cumul partiel après annulation ou
timeout. Le runtime refuse ensuite un fill différent de la position entière,
sans parcours complet de comptabilisation et de protection du reliquat.

**Reproduction** : vente demandée de **1**, exécution réelle simulée de **0,4**,
reliquat **0,6**. Après timeout, le réarmement tente **1**. Le faux exchange refuse
pour solde insuffisant : **0,6 reste sans stop**.

**Correction attendue** : intention durable, identifiant stable, état UNKNOWN
explicite, relecture de l’ordre après annulation, cumul des fills dédupliqué,
comptabilité partielle et stop sur la quantité réellement restante. Aucun renvoi
ni réarmement aveugle de l’ancienne quantité. Tester annulation elle-même ambiguë,
fill entre deux lectures et réponse perdue après exécution.

### A08 — P1 : des données anciennes sont réétiquetées fraîches

**Sources** : [feed.py:398](../backend/app/market/feed.py#L398),
[orchestrator.py:1136](../backend/app/runtime/orchestrator.py#L1136),
[orchestrator.py:1238](../backend/app/runtime/orchestrator.py#L1238).
`last_price()` conserve seulement le prix du ticker. Les évaluations remplacent
`tick_at` par l’heure locale courante. La fraîcheur des clôtures H4/H1/D1 attendues
n’est pas contrôlée à cet endroit.

**Reproduction** : réponses marché figées **20 jours** avant la décision, ticker
compris. Une nouvelle position est tout de même ouverte en mémoire. Le filtre
STALE_MARKET_DATA voit l’heure nouvellement attribuée, pas l’âge de la source.

**Correction attendue** : conserver et valider l’heure source du prix ; distinguer
réception et événement. Contrôler la dernière clôture attendue et la continuité
requise pour les indicateurs. Une heure source inconnue doit être traitée selon
un contrat explicite, pas transformée en preuve de fraîcheur.

### D01 — P1 : les intentions d’achat et la coordination des urgences restent incomplètes

**Sources** : [orchestrator.py:1285](../backend/app/runtime/orchestrator.py#L1285),
[api/main.py:571](../backend/app/api/main.py#L571),
[repository.py:959](../backend/app/db/repository.py#L959).
Le broker est appelé avant création durable d’une intention d’achat et avant
fabrication des identifiants locaux d’ordre. La transaction regroupant position,
ordres et snapshot améliore le ledger, mais ne rend pas atomiques exchange et SQL.
Une exception rattrapée force HALT en LIVE ; un arrêt brutal du processus ne passe
pas par ce `except`.

Le verrou empêche deux cycles concurrents du moteur. Les chemins API panic,
kill-switch et reprise ne prennent pas tous ce verrou. Deux transactions SQL
indépendantes ne sérialisent pas leurs appels exchange. La reprise administrative
ne vérifie pas l’ensemble des intentions et divergences avant retour à IDLE.

**Correction attendue** : porter les mécanismes locaux utiles sur le master courant,
unifier les verrous et intentions pour tous les points d’entrée, puis tests
multi-connexions/processus aux frontières avant/après envoi. Ne pas présenter
`pending_exits` comme une gestion complète des achats inconnus ou des paniques.
Ce point est établi par relecture ; les interruptions système exhaustives n’ont
pas été simulées pendant cet audit.

### A07 — P1 : la sonde réelle peut annoncer un succès après un résultat inconnu

**Source** : [live_probe.py:112](../backend/scripts/live_probe.py#L112).
La sonde appelle `create_order(...,{})`, sans mode de validation exchange. Tout
échec est capturé ; la reconnaissance d’un rejet n’affecte pas le code de sortie.
Une liste d’ordres ouverts vide est ensuite assimilée à une absence d’exécution.

**Reproduction** : le faux `create_order` lève un timeout, les ordres ouverts sont
vides, et le script retourne **0** en annonçant « aucun ordre n’a été créé ».
Un ordre totalement rempli peut aussi ne laisser aucun ordre ouvert.

**Correction attendue** : distinguer rejet métier confirmé, erreur de lecture et
résultat UNKNOWN ; valider à blanc avec le mécanisme exchange vérifié. Une sonde
ne doit pas vendre de compensation sans identification certaine du fill. Ne pas
utiliser son succès actuel comme autorisation de canary.

## 3. Supabase : constat de la cible, sans la modifier

Projet identifié : `trninja`, PostgreSQL 17, région eu-west-1. Lecture de
`public.schema_migrations`, catalogues de droits, fonctions, vues et `bot_state`
le 9 novembre 2026 vers **19:24–19:27 UTC**.

### D02 — P1 : le schéma déployé ne contient pas les nouveautés

Les neuf empreintes 0001–0009 concordent avec les fichiers du master. **0010 et
0011 ne sont pas appliquées.** Les tables de capital/intentions de sortie,
politiques et observations requises par cette version n’existent donc pas dans
ce schéma versionné. Le succès des migrations locales ne vaut pas déploiement.

Les trois lignes PAPER/LIVE/TESTNET sont IDLE, avec `last_heartbeat_at = NULL`.
Cela ne prouve ni qu’un moteur tourne, ni son absence absolue : aucune preuve de
vie exploitable n’est fournie par ce champ. La version des processus, le runner
Ollama, le TLS, les clés et les alertes de la machine cible restent à qualifier.

### A09 — P1 : EXECUTE public sur une fonction SECURITY DEFINER

**Sources** : [0001_init.sql:418](../backend/db/migrations/0001_init.sql#L418),
[0002_lock_public_api.sql:32](../backend/db/migrations/0002_lock_public_api.sql#L32).
Les catalogues de la cible confirment `security_definer=true` et EXECUTE pour
`anon` et `authenticated` sur `ensure_partitions(integer)`. Le paramètre
`months_ahead` n’est pas borné dans le corps.

La migration 0002 révoque les droits des rôles nommés mais laisse les droits
hérités de **PUBLIC** sur les fonctions. Le même défaut subsiste dans les
privilèges par défaut. La fonction peut faire du DDL et déplacer des lignes de
partition avec les privilèges de son propriétaire.

**Reproduction locale uniquement** : `SET LOCAL ROLE anon`, puis
`ensure_partitions(7)` crée **deux partitions** ; transaction entièrement annulée.
Aucune invocation de cette fonction/RPC n’a été faite sur la cible.

**Correction attendue** : nouvelle migration retirant EXECUTE à PUBLIC et aux
rôles exposés, droit réservé à la maintenance, borne stricte des mois, audit des
privilèges par défaut et tests négatifs de toutes les fonctions privilégiées.
Les droits Data API exposés doivent aussi être vérifiés avant remise en service.

L’advisor signale également `rls_auto_enable()`. Sa signature retourne
`event_trigger` : le droit observé ne prouve pas une invocation RPC ordinaire
exploitable. Cette nuance ne s’applique pas à `ensure_partitions(integer)`.

Références des advisors :
[anon SECURITY DEFINER](https://supabase.com/docs/guides/database/database-linter?lint=0028_anon_security_definer_function_executable),
[authenticated SECURITY DEFINER](https://supabase.com/docs/guides/database/database-linter?lint=0029_authenticated_security_definer_function_executable).

**Ce qui est correct sur la cible** : les tables inspectées ont la RLS activée et
aucun droit SELECT/INSERT/UPDATE/DELETE pour anon/authenticated. L’absence de
politique sur ces tables privées est intentionnelle, pas une invitation à ouvrir
une policy publique. Toutes les vues inspectées refusent la lecture publique ;
cinq ont `security_invoker=true`, `v_system_health` garde les options par défaut.
Cette dernière est un durcissement à compléter, sans fuite publique démontrée ici.

## 4. Défauts de recherche et de diagnostic à corriger avant qualification économique

| Référence | Constat reproduit | Effet et correction attendue |
|---|---|---|
| A04 — P2, [experiments.py:67](../backend/app/insights/experiments.py#L67) | Le ledger mémoire oublie la raison du HALT et retourne `LLM`, alors que le runtime attend un préfixe `LLM HALT`. Après expiration, l’expérience reste HALT ; avec la raison correcte, elle revient IDLE. | Les variantes avec HALT ne suivent pas le même parcours que la production. Conserver raison et source ; tests de conformité du ledger mémoire face au repository SQL. |
| A05 — P2, [ranking.py:11](../backend/app/insights/ranking.py#L11) | Un coût calculé sur un carnet vieux de 60 s reste admissible ; le modèle de coût limite pourtant le carnet à 5 s à son calcul. | Réévaluer l’âge de `cost.as_of` au choix, pas seulement l’âge du candidat (120 s). Conserver le défaut observe ; aucune conclusion de supériorité de `paper_select` en l’état. |
| A06 — P2, [orchestrator.py:1282](../backend/app/runtime/orchestrator.py#L1282) | Suspension pendant l’écriture SUBMITTED, après le dernier contrôle de permission, puis ouverture en mémoire avec `enabled=false`. | Définir le point d’autorisation irrévocable et le sérialiser avec la suspension. Une annulation d’achat déjà autorisé n’a pas la même sémantique qu’interdire de nouvelles autorisations ; l’interface doit le dire. |
| A10 — P2, [store.py:279](../backend/app/insights/store.py#L279) | Un ordre CANCELED avec `filled_amount=0.4` existe en SQL, mais `diagnostics_input()` renvoie zéro ordre. | La requête doit retenir tous les cumuls exécutés, y compris l’annulation du reliquat ; sinon cashflows, frais et excursions partielles sont faux. Tester FILLED, PARTIALLY_FILLED, CANCELED partiel et absence de doublons. |

Autres points établis par relecture, sans incident exchange réel reproduit :

- `_move_stop()` ignore l’identifiant retourné par le remplacement distant et ne
  journalise pas le nouvel ordre. Plusieurs déplacements dans un cycle peuvent
  réutiliser l’ancien ID. Persister et transmettre l’identifiant courant ; tester
  breakeven puis Chandelier dans le même cycle.
- Le health API peut annoncer toute la capacité SQL libre si la lecture du quota
  échoue ; une date théorique du scheduler ne prouve pas un cycle exécuté. Le
  surveillant indépendant améliore la lecture mais n’a pas encore de déploiement,
  canal d’alerte et disponibilité externe qualifiés.
- Les dépendances Python sont exprimées en plages et il n’y a pas de workflow
  GitHub de validation dans ce master. Figer l’environnement qualifié et automatiser
  Python/Node/SQL/golden/navigateur avant exploitation durable.

## 5. Ce qui a tenu, et ce que les tests ne garantissent pas

Le pare-feu reste déterministe ; les trois verrous de configuration live et la
restriction PAPER du capital progressif sont présents. La vérification des
permissions Kraken **est déjà appelée au démarrage** (`app/main.py:113`) et lève
sur un refus : le contraire indiqué dans certains paragraphes de roadmap est
périmé. La recette de la clé cible reste à faire.

Les tests démontrent les transactions de clôture, le rollback, la séparation des
modes dans les lectures, les révisions de réglages, les politiques immuables,
la capture/rejeu hors I/O réelles et les routes privées. Les nouveaux outils de
recherche ne promeuvent pas une stratégie et ne modifient pas les seuils seuls.

Ils n’établissent pas une sûreté exhaustive face aux réponses exchange ambiguës,
aux arrêts de processus et aux appels d’urgence concurrents. Ils ne testent pas
encore les dix contre-exemples de ce rapport comme invariants à faire respecter.
Une couverture large en nombre ne remplace pas ces contrats financiers précis.

## 6. Analyse économique pré go live

Le golden rejoué sur sa fenêtre **10 octobre–4 novembre 2026** comporte **deux
trades, deux pertes, PF 0, PnL -34,8828 EUR sur 1 000 EUR (-3,49 %)**, drawdown
3,49 %. Ce jeu sert à détecter une dérive du code ; il n’est ni une mesure live,
ni un nouvel essai de rentabilité multi-années, ni une preuve hors échantillon.

Les critères existants restent non démontrés : au moins 100 trades simulés,
espérance nette positive, drawdown sous le plafond choisi, plusieurs régimes et
période jamais utilisée pour choisir les paramètres. Les simulations doivent
d’abord être corrigées, puis inclure la politique entière, les frais réels,
spread/glissement défavorables, refus, réserve et cagnotte, cash et buy & hold.
Aucun budget ne doit être augmenté pour forcer le minimum d’ordre Kraken.

**Verdict économique : NO-GO indépendant du verdict logiciel.** Même tous les
défauts techniques corrigés, la preuve de rentabilité ne serait pas acquise.

## 7. Séquence de sortie du NO-GO

**Plan actuel détaillé : [GO_LIVE.md](GO_LIVE.md), R0–R7, jusqu’à la stabilisation
réelle et à la décision finale.** Le tableau G0–G6 ci-dessous conserve la synthèse
initiale de l’audit ; il ne remplace pas les critères et responsabilités du plan.

| Porte | Travail concret | Preuve permettant de fermer |
|---|---|---|
| G0 — Isolation et accès | A02 et A09 : PAPER sans mutation exchange, droits des fonctions privilégiées fermés | Aucun appel create/cancel/withdraw en PAPER ; anon/auth refusés ; advisors relus, sans rendre publiques les tables |
| G1 — Exécution et risque | A01/A03/A08/D01 : compte courant unique, fraîcheur source, intentions, fills partiels, coordination moteur/urgences/reprise | Les contre-exemples deviennent des tests de non-régression ; arrêts et timeouts à chaque frontière sans double ordre, reliquat non protégé ou ledger incohérent |
| G2 — Recherche fidèle | A04/A05/A06/A10 et suivi de stop : contrats identiques mémoire/SQL, coûts frais, comptabilité des fills et autorisation sérialisée | Comparaisons reproductibles du vrai comportement ; aucune promotion automatique |
| G3 — Déploiement PAPER | Figer une version, sauvegarde/restauration testée, nouvelles migrations validées sur copie puis sur cible, services homogènes | Checksums conformes ; révisions réellement appliquées ; heartbeat et données à jour ; export/rejeu et scénarios de panne sur la cible |
| G4 — Exploitation | Surveillant séparé, clé lecture seule distincte, alerte réelle, sonde externe, TLS/accès privé, rétention et quota, dépendances figées/CI | Stop manquant, lecture inconnue, arrêt moteur/surveillant et base indisponible détectés ; procédures de récupération exercées |
| G5 — Économie | Politique corrigée testée hors calibration, coûts défavorables, effectif et plafonds respectés | Critères chiffrés existants effectivement atteints et rapport reproductible, y compris résultats négatifs |
| G6 — Canary autorisé | Seulement après les portes précédentes : sous-compte, clés contrôlées, budget et autorisation explicites de Seb | Un aller-retour minuscule, stop distant vérifié, ledger/fills réconciliés, reprise après coupure qualifiée et décision explicite sur la suite |

Le prochain travail utile est **G0 puis G1**, avant d’ajouter des features ou de
considérer la commande d’activation. Cet audit n’a appliqué aucun correctif métier,
aucune migration cible et aucun changement de mode. Les docs et reproductions
consignent les constats ; la clôture d’un défaut exige son correctif et sa preuve.
