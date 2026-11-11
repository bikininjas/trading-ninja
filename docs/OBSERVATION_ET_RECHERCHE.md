# Observation, pilotage et recherche

Les dix briefs I-01 à I-10 ont une première version utilisable. Ils ajoutent une
lecture des décisions et des outils papier. Ils ne démontrent aucune rentabilité
et ne lèvent aucun critère de passage au réel de [ROADMAP.md](ROADMAP.md).
Les comportements et limites précis de cette livraison sont décrits ici ; les
[briefs](ROADMAP.md#ameliorations) conservent les objectifs et critères de
qualification à long terme.

**État au 10 novembre 2026 : NO-GO pour le réel.** Les défauts reproduits sur
39db447 ont reçu les [correctifs publiés](JOURNAL.md#correctifs-pre-go-live). Supabase est
en 0013, [droits et intégrité vérifiés](JOURNAL.md#deploiement-supabase). La recette
de déploiement, la surveillance et la preuve économique restent à qualifier.

## Installation et réglages

Sur une nouvelle base, sauvegarder et vérifier les migrations avant de les
appliquer. Sur trninja, 0001–0013 sont déjà appliquées ; ne pas recréer les tables.
Le moteur, l’API et le dashboard doivent utiliser la même version du code :

```bash
cd backend
.venv/bin/python scripts/db_migrate.py status
.venv/bin/python scripts/db_migrate.py apply
.venv/bin/python scripts/db_migrate.py verify
```

La migration `0011_insights_and_controls.sql` ajoute les politiques, révisions,
cycles, artefacts dédupliqués, événements, expériences, incidents et commandes de
surveillance. Les dix nouvelles tables ont la RLS activée ; `anon` et
`authenticated` n'ont aucun droit. Le backend utilise sa connexion privée.
`0012` ferme la maintenance privilégiée et `0013` ajoute les reçus d’exécution
durables. Les fichiers de migrations déjà appliquées restent immuables.

Variables dans `backend/.env.example` :

| Variable | Défaut | Effet |
|---|---|---|
| `INSIGHTS_ENABLED` | `true` | Capture des cycles et des opportunités ; désactivation explicite possible |
| `RANKING_MODE` | `observe` | Calcule le classement sans changer la paire choisie |
| `RANKING_MODE=paper_select` | facultatif | Sélection selon le classement, refusée hors PAPER |
| `OBSERVER_API_KEY` / `OBSERVER_SECRET` | vides | Clé de lecture indépendante, jamais remplacée par la clé de trading |
| `OBSERVER_INTERVAL_SECONDS` | `15` | Entre 5 et 300 secondes |

Aucune variable d'armement live n'est changée par ces fonctionnalités.

## Suspendre les nouveaux achats (I-04)

Le panneau « Comprendre les décisions » permet de suspendre les achats sans
vendre. La permission est persistée par mode et survit au redémarrage. Les
positions existantes suivent encore leurs stops, sorties et règles de gestion.
Le moteur contrôle la permission avant l’analyse et l’autorisation durable.
L’autorisation PREPARED est sérialisée en SQL avec la permission et l’état : une
suspension antérieure bloque l’achat ; une intention déjà autorisée peut finir.
Elle est rapprochée avant tout nouvel envoi. La suspension n’annule pas un ordre.

La réautorisation exige une révision courante et un **nouveau code TOTP**. Elle
prend le verrou du moteur et refuse HALT, circuit breaker, EXECUTING ou une sortie
à rapprocher. Elle ne réarme jamais une urgence et ne vend jamais la position.
Un conflit de révision renvoie 409 ; relire l'état avant de recommander l'action.

## Réglages et politiques de position (I-07)

Les réglages ont une révision demandée, une révision appliquée, une date et un
motif de refus. Le dashboard prévisualise les différences et envoie la révision
lue ; un client de production sans révision est refusé (428). Le moteur accuse
réception de la version exacte qu'il a chargée, même si une autre édition arrive
entre-temps. Un changement de cadence requiert un redémarrage ; désactiver le
contrôleur de capital avec des enveloppes existantes est refusé.

Chaque nouvelle position référence une politique immuable identifiée par SHA-256.
Les règles de stop, trailing, sortie et cadence restent celles de cette politique.
Les plafonds de risque et permissions administratives restent courants. L'aperçu
indique `NEXT_POSITION` ou `GLOBAL_SAFETY` pour chaque champ concerné.

Une position historique sans politique conserve son stop et son TP mais ne reçoit
pas de nouvelles règles de trailing ni de décision LLM de gestion. Son adoption
est explicite, PAPER uniquement, avec TOTP et empreinte de prévisualisation exacte.
Elle ne remplace jamais une politique déjà attachée. Vérifier auparavant le curseur
de surveillance des anciennes positions : l'adoption ne réconcilie pas un historique
manquant.

## Parcours des opportunités et classement (I-01, I-06, I-08)

Les événements distinguent : observation, données absentes, absence de setup,
bougie déjà traitée, signal, HOLD/repli, refus de risque, approbation, classement,
candidat non retenu, revalidation, soumission et remplissage. Les erreurs de cycle
ou d'exécution incertaine gardent un motif distinct. L'identité d'une opportunité
combine symbole, clôture et politique ; un cycle reste une tentative distincte.

Le résumé sépare opportunités uniques, tentatives terminées, approbations et achats
remplis. Les fenêtres attendues utilisent la cadence et l'univers **actuels** : elles
ne constituent pas une preuve de présence passée du moteur. Les cycles commencés,
terminés et en échec sont comptés séparément. La fenêtre API est limitée à 93 jours
et 5 000 événements, avec indication de troncature.

Le classement `lexicographic-v1` compare admissibilité, coût aller-retour rapporté
au notionnel, distance au setup normalisée par ATR, puis symbole. Il ne réinterprète
pas la confiance comme une espérance. Un candidat de plus de 120 secondes ou sans
coût disponible est exclu de `paper_select`. En observation, le choix historique du
premier candidat approuvé est conservé. Toute sélection passe une nouvelle fois
par `evaluate()` avec un prix et un compte relus avant achat.

Le calcul `book-v1` consomme les niveaux du carnet pour la quantité approuvée :
spread, impact de profondeur et frais sont séparés sans compter deux fois le spread.
L'horodatage source est obligatoire ; carnet de plus de 5 secondes, futur, croisé,
invalide ou trop peu profond = coût indisponible. Le coût de sortie est un scénario
sur le carnet courant, pas une prévision. La réception locale ne remplace pas une
heure source manquante. Kraken peut donc produire de nombreux coûts inconnus.
Ces estimations ne remplacent ni le sizing ni les frais réellement exécutés.

## Déroulement d'une position (I-02)

La fiche de diagnostic prend un UUID de position. Elle utilise les ordres remplis,
les clôtures archivées et les snapshots pendant la détention, jusqu'à la sortie. Chaque source est bornée à 5 000 lignes ; toute troncature est
signalée et interdit de conclure à une couverture complète. Elle reconstruit réalisé cumulé et latent restant lors des ventes partielles.
Le PnL réalisé du ledger fait autorité ; R utilise le risque initial enregistré.

MFE, MAE, délai avant gain net, recul depuis le pic et ratio capturé sont des
**observations**, pas des extrêmes intrabougie exacts. La couverture compare les
clôtures archivées aux clôtures attendues ; des snapshots supplémentaires ne
transforment pas un historique lacunaire en historique complet. Frais dans une
autre devise sans conversion = frais et excursions nettes inconnus. Slippage absent
= inconnu, jamais zéro par défaut. Le ratio capturé est absent sans pic positif.

Les rapports H4, swing et mean reversion contiennent aussi `diagnostics` par trade,
sans modification des décisions du golden. Leur excursion utilise les clôtures
et les frais aller-retour enregistrés comme hypothèse conservatrice constante.

## Boîte noire et rejeu exact (I-03)

Chaque cycle capture la configuration expurgée, son état de chargement des réglages,
les identités du code/Python/CCXT/contexte Decimal, les données réellement lues,
les sorties LLM observées et les résultats des frontières repository/exchange/broker.
Les grands blocs deviennent des artefacts adressés par leur empreinte. Les heures
logiques, identifiants et chronomètre sont injectés pour reproduire le même rapport.

```bash
cd backend
.venv/bin/python scripts/insights.py export UUID_CYCLE --out /tmp/cycle.json
.venv/bin/python scripts/insights.py replay /tmp/cycle.json --out /tmp/rejeu.json
```

`replay` exécute le **véritable Orchestrator** avec les I/O enregistrées ; il n'ouvre
aucune connexion SQL, exchange ou LLM. Il contrôle chaque appel, ses arguments,
son résultat et le rapport final. Les mêmes entrées peuvent être rejouées depuis
le dashboard. Le modèle n'est pas réinterrogé : la température zéro ne suffit pas
à garantir une nouvelle inférence identique.

Un paquet incomplet, altéré, d'un autre code ou environnement de calcul est refusé
comme `UNREPLAYABLE` ; une divergence indique le premier appel concerné. Un cycle
interrompu demeure RUNNING sans fin confirmée, et n'est pas déclaré reproductible.
La version des poids reste « inconnue » si le service local ne donne pas de digest.
Les paquets contiennent des informations financières et prompts : les exports
restent privés même après suppression des secrets.

Limites : 5 000 appels, budget de charge utile de 7 Mio et paquet de 8 Mio.
Une erreur de capture ne doit pas interrompre une protection ou un fill déjà
exécuté. La capture indisponible bloque les nouveaux achats et laisse gérer les
positions existantes ; une trace tronquée devient INCOMPLETE. Le contrôle de quota
réserve 100 Mio au ledger et jusqu'à 8 Mio à un paquet, avec une seconde mesure
avant persistance. Il ne garantit pas le quota face à d'autres écrivains externes.
Aucune purge automatique d'une preuve financière n'est installée : surveiller la
taille, exporter les paquets et définir la rétention avant exploitation longue.

## Comparaisons de portefeuilles (I-05)

Un fichier JSON fige données, période, capital et jusqu'à trois variantes. Chaque
variante utilise son propre ledger, ses frais, stops, cooldowns, pertes, réserves
et paliers. Les variantes suivent la même grille causale ; chaque bougie doit être
clôturée à l'heure simulée. Le moteur utilisé est l'Orchestrator et le PaperBroker
courants, sans service externe.

```bash
cd backend
.venv/bin/python scripts/insights.py experiment /tmp/experience.json --out /tmp/resultats.json
```

Contrat de fichier (l'extrait de données ci-dessous illustre le format ; fournir
l'échauffement réel, au moins les 200 clôtures nécessaires aux indicateurs) :

```json
{
  "schema_version": 1,
  "hypothesis": "Sortie fixe contre trailing, mêmes coûts et même univers",
  "start_at": "2026-01-01T00:00:00+00:00",
  "end_at": "2026-03-01T00:00:00+00:00",
  "equity": "1000",
  "validation_window": {
    "start_at": "2026-02-01T00:00:00+00:00",
    "end_at": "2026-03-01T00:00:00+00:00"
  },
  "variants": [
    {"name": "mecanique", "kind": "mechanical"},
    {"name": "trailing", "kind": "mechanical", "risk_overrides": {
      "take_profit_r_multiple": "0", "profit_trailing_enabled": true,
      "profit_trailing_start_r": "2", "profit_trailing_distance_r": "1"
    }}
  ],
  "data": {"BTC/EUR": {
    "market": {"amount_step": "0.000001", "price_tick": "0.1",
      "min_amount": "0.0001", "min_notional": "5", "taker_fee": "0.004"},
    "candles": {"4h": [], "1h": []},
    "books": []
  }}
}
```

Les bougies sont `[open_ms, open, high, low, close, volume]`, triées, uniques, finies,
OHLC cohérents et sans trou. Une couverture de décision différente entre les
paires fait échouer la comparaison plutôt que réutiliser un prix ancien. Une entrée `books` est un carnet daté au format CCXT ; aucun carnet
historique n'est inventé. La simulation d'exécution utilise les frais/glissement
configurés, et le résultat conserve la configuration effective et les empreintes
des données/code. Les montants sont des chaînes décimales. Les variantes peuvent
modifier uniquement la liste stratégique de `app/insights/policy.py`.

`kind=mechanical` propose BUY à plat et HOLD en position ; setup, filtre et sorties
déterministes restent actifs. `kind=recorded` exige un tableau `signals` complet :
`symbol`, `at` (ISO UTC exact de décision), `has_position`, `action`, `confidence`,
`market_regime`, et éventuellement `directional_bias`, `thesis`, `degraded`.
Une action manquante fait échouer l'expérience ; elle ne devient pas un HOLD.
Pour comparer LLM, sans LLM et leçons, fournir des variantes et actions historiques
figées correspondantes : le moteur de recherche n'invente aucune décision LLM.

La CLI emploie par défaut le profil prudent PAPER, sans charger `.env.local`.
L'API utilise les réglages de sa configuration et le payload validé/appliqué ; elle
refuse un payload encore en attente. Le JSON de résultat en conserve les valeurs.
Maximum : dix paires, trois variantes, 10 000 cycles ; HTTP limité à 1 Mio et
90 jours, CLI à 8 Mio et dix ans. Un seul calcul API à la fois ; un autre reçoit
429 afin de laisser les connexions d'administration disponibles. Les essais HTTP
ont un manifeste immuable et un état RUNNING puis COMPLETE ou FAILED.

Les courbes, frais, trades clos, réserve, cagnotte, drawdown et différences sont
restitués. Les positions encore ouvertes sont valorisées après frais/glissement
estimés de liquidation. L'intervalle par blocs hebdomadaires n'apparaît qu'à partir
de 20 blocs et reste descriptif ; il n'est pas une preuve statistique de supériorité.
Déclarer une fenêtre de validation ne prouve pas qu'elle était inconnue lors du
choix des paramètres. Aucun résultat ne promeut une politique automatiquement.

## Surveillance indépendante (I-09)

```bash
cd backend
.venv/bin/python scripts/insights.py watch --serve --expect-runtime
# Facultatif : --webhook https://votre-service/incident
```

Exécuter ce processus séparément du moteur avec sa clé propre. En LIVE, il vérifie
`GetApiKeyInfo` puis lit uniquement soldes et ordres ouverts. Les permissions de
création, modification, annulation, transfert ou retrait sont refusées. La clé doit
lire fonds et ordres ouverts et différer de la clé de trading. En PAPER, il n'utilise
pas les soldes réels pour juger une position virtuelle.

Les écarts de quantité, les actifs configurés non suivis et un stop de type,
quantité restante, statut ou niveau insuffisants ouvrent un incident. Un timeout
reste inconnu et ne clôt pas les incidents précédents. Un cycle bloqué ou absent
est détecté avec `--expect-runtime`, en respectant la cadence H4/D1. Un dernier
cycle échoué ou incomplet n'est jamais considéré sain. Les incidents
sont persistés, dédupliqués et historisés ; l'acquittement ne vaut pas résolution.
Seule une observation complète sans l'écart peut le résoudre.

Le surveillant ne vend, n'annule et ne remplace aucun ordre. Sa seule commande est
HALT en base, consommée par le propriétaire du moteur ; le blocage est aussi posé
immédiatement pour empêcher une nouvelle entrée. Un HALT humain/surveillance sans
échéance n'est pas remplacé par le cooldown d'un HALT LLM. La fraîcheur devient
STALE après `2 × intervalle` secondes : un surveillant mort n'est jamais vert.

La notification HTTPS ne suit pas les redirections et expire après cinq secondes.
Un échec est réessayé à chaque observation, même si l'incident a disparu entre-temps ;
le message conserve les faits initiaux. La file et la déduplication sont conservées
en SQLite, par source et par mode, dans `state/observer-paper.sqlite3` à la racine
(ou `--notification-state /volume-prive/observer-paper.sqlite3`). Le répertoire
doit être persistant, privé, surveillé en capacité et sauvegardé ; ne pas effacer
la file au redémarrage. Un processus concurrent ou un fichier corrompu échoue
explicitement. Chaque récidive reçoit un `event_id` stable : la réponse HTTPS
acquitte l'événement, une réponse perdue peut le renvoyer avec le même identifiant.
L'observation SQL conserve `notification_pending` avant livraison et ne l'efface
qu'après acquittement de la file ; une réponse tardive ne modifie pas une observation
plus récente. Sonde, API et dashboard restent en alerte tant que la file du
surveillant attend, même après résolution. Une observation héritée sans ce champ
reste inconnue jusqu'au prochain passage du surveillant à jour. L'absence d'alerte
en attente ne prouve pas la disponibilité du canal réel : sa recette reste requise.
Le destinataire doit dédupliquer ces identifiants. Une panne SQL produit une alerte
locale durable et une tentative de reconnexion, sans modifier d'ordre. Il reste à
qualifier le canal réel et installer une sonde
externe de disponibilité du surveillant. Aucun service, clé ou alerte réelle n'a
été créé par cette livraison.

### Vie continue du moteur

`python -m app.main --serve` renouvelle `bot_state.last_heartbeat_at` toutes les
dix secondes depuis le même event loop, sur une connexion SQL distincte avec
timeout de cinq secondes. Les snapshots d'équité ne renouvellent plus cette
preuve. Sans premier heartbeat réussi, le travail ne commence pas ; une erreur
ultérieure arrête le travail du moteur. La sortie normale efface uniquement son
propre heartbeat ; après arrêt brutal ou event loop bloqué, il expire après
trente secondes. L'arrêt ne retire aucun stop exchange. La reprise conserve les
contrôles de réconciliation des intentions, sans relance aveugle d'un ordre.

Le surveillant avec `--expect-runtime`, la sonde et l'API exigent tous une preuve
de vie récente. La sonde relit directement cette colonne : une observation encore
fraîche du surveillant ne masque pas la mort du moteur. Le dashboard expire son
état sain à la date source fournie par l'API et l'invalide après un échec de lecture,
sans effacer les derniers faits affichés. Aucun rafraîchissement ne rajeunit la
preuve. Les horloges des services doivent être synchronisées.

Le heartbeat prouve l'avancement de l'event loop. La progression est contrôlée
séparément : trente secondes maximum pour un cycle RUNNING, ou trente secondes
après l'échéance normale H4/D1 sans nouveau cycle complet. La sonde relit directement
le dernier cycle, même si le surveillant avait annoncé zéro incident. Un cycle
annulé conserve un statut FAILED, avec incertitude d'exécution si une soumission
avait commencé. La charge, la contention SQL et les délais bout en bout restent
à qualifier sur la cible. Un cron `--once` ne fournit
pas une présence continue : utiliser le moteur `--serve` pour cette supervision.
Déployer la même version partout, démarrer le moteur puis le surveillant après
un premier cycle vérifié ; ne pas masquer un démarrage impossible par une période
verte artificielle. Un HALT de surveillance exige toujours une reprise contrôlée.

### Sonde séparée du surveillant

`backend/scripts/watchdog.py` lit la dernière observation et alerte par HTTPS si
elle est absente, périmée, incomplète, invalide, contient un incident ou si la base
est illisible. Il n'envoie aucune commande financière. Sur une autre machine ou un
autre service, injecter `WATCHDOG_WEBHOOK_URL` dans l'environnement sécurisé et
planifier par exemple toutes les dix secondes, comme le timer livré :

```bash
cd backend
.venv/bin/python scripts/watchdog.py --mode PAPER --interval 15
```

`--interval` doit correspondre à `OBSERVER_INTERVAL_SECONDS` du surveillant ; la
fraîcheur maximale est `2 × intervalle` secondes. L'intervalle en argument décrit
le surveillant, pas la cadence d'appel de la sonde. Code 0 : observation du
surveillant courante et complète ; 1 : alerte ou état inconnu ; 2 : configuration
invalide. La sonde conserve ses alertes dans `state/watchdog-paper.sqlite3`
(`--notification-state` configurable), avec les mêmes garanties et contraintes
de stockage que le surveillant. Une notification échouée reste réessayable même
après résolution ; tant qu'elle attend, le code de sortie reste 1. Une récidive
est une nouvelle notification, un incident inchangé déjà livré est dédupliqué.
Elle exige `--expect-runtime`, une lecture directe récente du heartbeat et un
dernier cycle connu avec progression dans son budget. L'API expire aussi son état
sain au plus tôt de ces trois échéances ; un rafraîchissement ne les repousse pas.
`--allow-idle` est réservé au
PAPER. L'absence d'appel de la sonde elle-même exige un contrôle externe distinct.
Le webhook, le planning, les délais et l'indépendance des services doivent être
exercés sur la cible avant R3 ; aucune installation n'est faite automatiquement.

## Confiance du LLM (I-10)

Le rapport privé regroupe achats exécutés et clos avant la date de coupure par
modèle, digest des poids, gabarit du prompt, digest des leçons et politique de
position. Les replis et issues non résolues sont exclus. Les cohortes héritées ou
sans version des poids/prompt/politique vérifiable restent insuffisantes.

Par tranche : effectif, score moyen et proportion de résultats nets positifs,
avec ventilation par régime. Le seuil par défaut est 20 observations ; même au-delà,
la lecture est descriptive et le biais de sélection des seuls achats exécutés est
explicite. Le prompt complet varie avec le marché ; son hash ne sert donc pas à
mélanger ou fragmenter abusivement les versions de gabarit.

La confiance actuelle n'est pas une probabilité contractuelle. Brier et référence
constante restent absents sauf `confidence --probabilistic-contract` sur un jeu
fourni par CLI avec cette sémantique explicitement acceptée. Aucun seuil ou sizing
ne change à partir de ce rapport. Les politiques alternatives et achats refusés
s'évaluent dans les expériences de portefeuille.

## Routes privées

Toutes exigent la session administrateur habituelle. Le proxy Node conserve le
JWT côté serveur, vérifie l'origine des POST, borne les corps et n'autorise que
les chemins exacts et UUID attendus.

| Méthode et route | Usage |
|---|---|
| GET `/api/insights/overview?days=7` | Synthèse, contrôles, couverture et révisions |
| GET `/api/diagnostics/opportunities?since=…&until=…` | Événements bornés, filtre `symbol` facultatif |
| GET `/api/positions/{id}/diagnostics` | Fiche de position du mode courant |
| GET `/api/insights/cycles?limit=20` | Derniers cycles |
| GET `/api/insights/cycles/{id}` | Paquet typé exportable |
| GET `/api/insights/cycles/{id}/events` | Explication humaine du parcours |
| POST `/api/insights/cycles/{id}/replay` | Rejeu d'un cycle enregistré |
| POST `/api/insights/replay` | Rejeu d'un paquet JSON fourni |
| POST `/api/control/entries` | `enabled`, `expected_revision`, `reason`, TOTP à la reprise |
| GET `/api/insights/policy` | Politiques proposées et positions actuelles |
| POST `/api/control/positions/{id}/adopt-policy` | `expected_policy_id`, `totp`, PAPER uniquement |
| POST `/api/settings/preview` | `update` et `expected_revision`, sans écriture |
| GET / POST `/api/insights/experiments` | Résultats / lancement d'une comparaison |
| GET `/api/insights/confidence?since=…&until=…` | Cohortes et effectifs, fenêtre UTC bornée |
| GET `/api/insights/protection` | Dernière observation et incidents |
| POST `/api/insights/incidents/{id}/acknowledge` | Acquittement sans reprise ni résolution |

Les commandes `scripts/insights.py diagnose UUID --out …` et `confidence fichier
--until DATE --out …` complètent l'interface. Les fichiers de confiance contiennent
les champs du rapport (`closed_at` ISO, score, action, résultat net, identités,
`policy_id`). Les exports et expériences restent locaux à leur mode ; le rejeu
fourni par fichier est purement hors ligne, même si son manifeste décrit LIVE.


## Capital progressif (PAPER)

Objectif : préserver le capital et rechercher une progression modeste, nette de
frais, sur plusieurs mois. Aucun objectif de gain quotidien, aucun nombre minimal
d'ordres. Rester en EUR est normal mais ne prouve aucune rentabilité. Le profil
`progressif` reste exclusivement une expérience papier.

### Règles initiales

| Paramètre | Valeur |
|---|---|
| Budget de l'exemple | 9,37 EUR virtuels |
| Perte prévue par trade | 0,5 % de l'équité, frais et glissement compris |
| Exposition maximale | 50 %, sans allocation complète ni relèvement automatique du risque |
| Arrêts | 2 % quotidien, 5 % hebdomadaire, 10 % depuis le sommet, 4 pertes consécutives |
| Entrées | règle EMA50 H4 existante, tendance et LLM, une position au maximum |
| Take-profit fixe | aucun |
| Protection des gains | stop suiveur après +2 R net, à 1 R sous la clôture |

`R` est la distance initiale entre prix et stop, avant frais. Le dimensionnement
utilise la **perte totale prévue**, frais des deux côtés et glissement compris.
Les colonnes historiques `effective_risk_quote` et `risk_amount_quote` gardent leur
sens de risque de prix ; `RiskDecision.estimated_total_loss_quote` expose le budget
frais compris. Un gap peut dépasser la perte prévue : ce n'est pas une garantie.

Avant l'achat, un scénario à +4 R doit offrir un gain net au moins égal à 1,5 fois
la perte prévue au stop. **4 R est une hypothèse pour comparer les coûts, pas une
prévision ni un objectif garanti.** Ce filtre ne prouve aucun avantage statistique.
Un refus utilise `RISK_LIMIT_EXCEEDED` avec le motif « coûts disproportionnés ».
Aucun changement de schéma n'est nécessaire.

Le stop suiveur couvre au minimum les frais et le glissement de sortie prévus.
Il ne peut que monter, se calcule sur la clôture disponible et ne s'applique
qu'aux bougies suivantes. Le moteur papier rejoue les bougies non traitées
et persiste ensemble le niveau du stop et sa dernière clôture traitée. La même
fonction de calcul sert au moteur et au rejeu H4. Une interruption avant le
signal LLM ne permet plus de tester un nouveau stop sur sa bougie de calcul.
Un trou dans les bougies ou une position historique sans curseur bloque la
surveillance jusqu'à réconciliation ; aucune absence de déclenchement n'est inventée.

Le LLM conserve sa capacité de vendre ou d'arrêter ; son score minimal de 0,60
n'est pas une probabilité de réussite. Deux pertes consécutives divisent le risque
par deux. La décision est revalidée au prix utilisé pour le fill papier.

Avec 9,37 EUR, le budget prévu est environ 4,7 centimes par trade. Les minimums
Kraken peuvent empêcher **tous** les achats. Le profil accepte ce refus sans
passer automatiquement en `micro`. Cela protège le budget mais ne prouve pas
qu'une stratégie sera exploitable à cette taille.

### Capital protégé et petits paliers — D-070

Le profil `progressif` utilise trois enveloppes virtuelles : capital actif,
réserve protégée et cagnotte de croissance. Seul le capital actif dimensionne
les achats. Les enveloppes ne sont pas des retraits chez Kraken.

À chaque clôture, seules les nouvelles plus-values **globales réalisées** au-delà
du plus haut déjà distribué sont réparties : 30 % en réserve, 70 % en cagnotte.
Une perte réduit le capital actif. Le retour au précédent sommet ne distribue
pas une seconde fois les mêmes gains. Les centimes sont affectés immédiatement,
sans attendre le seuil historique de transfert de 1 EUR des autres profils.

| Paramètre | Exemple par défaut |
|---|---|
| Cagnotte requise | 1 EUR |
| Hausse maximale par palier | 0,25 EUR et au plus 5 % du capital actif |
| Cadence maximale | un palier tous les 7 jours, à une clôture gagnante |
| Plafond de capital actif | 20 EUR ; une mise initiale supérieure n'est pas réduite |

Aucun palier sur une perte, un drawdown réalisé non récupéré ou un panic-sell.
Le premier palier attend aussi 7 jours après l'initialisation. Aucun rattrapage
multiple si le bot a été arrêté plusieurs semaines. Le plafond et les paramètres
sont configurables via `CAPITAL_*` dans `.env.example`. Le risque en pourcentage
n'augmente pas et aucun minimum exchange n'est financé en forçant le risque.

Exemple comptable : 9,37 EUR initiaux et 2 EUR de nouveaux gains nets donnent
0,60 EUR protégés et 1,40 EUR en cagnotte. Après un palier autorisé de 0,25 EUR :
9,62 EUR actifs, 0,60 EUR protégés, 1,15 EUR en attente, soit 11,37 EUR au total.
Cet exemple suppose les gains déjà réalisés ; le profil ne sait pas actuellement
les démontrer avec 9,37 EUR sur la fenêtre mesurée.

Les tables `capital_accounts` et `capital_events` (migration 0010) conservent les
soldes et un événement unique par position clôturée. Clôture, événement, solde
et état du bot sont écrits dans une transaction. Le rejeu H4 appelle exactement
le même contrôleur de capital ; le JSON du comparatif expose les trois enveloppes
et le nombre de paliers. Le dashboard les affiche aussi.

Le tarif du broker, résolu pour l'univers configuré, est partagé avec le pare-feu
et le trailing. Le verrou de cycle utilise une transaction sur une connexion
dédiée pour fonctionner derrière le pooler en mode transaction. Cette seconde
connexion ne contient aucun autre travail : les intentions de vente réelle sur
la connexion principale restent persistées avant l'envoi. Une sortie réelle au
résultat inconnu exige une réconciliation et ne déclenche aucune seconde vente.
Ce durcissement ne valide pas l'ensemble du chemin live : les paliers restent
refusés en live et les autres réserves de l'audit live restent à traiter.

#### Mise en service papier

Sur une nouvelle base, appliquer les migrations manquantes après sauvegarde et
vérification. Sur trninja, 0001–0013 sont appliquées et vérifiées depuis le
10 novembre 2026 : [preuve et portée](JOURNAL.md#deploiement-supabase).
Cela ne prouve pas l’installation des services ni la recette PAPER.

Les comptes papier existants démarrent le contrôleur **à plat** : l'équité et la
réserve déjà constatées deviennent la nouvelle référence, sans redistribuer
les gains historiques. Une position papier ancienne sans curseur doit d'abord
être liquidée virtuellement via le panic-sell / kill-switch, après vérification
du mode PAPER, puis le bot doit être repris manuellement. Un capital déjà géré
par paliers ne peut pas être désactivé par un simple changement de profil.
Apports, retraits et changement de devise ne sont pas automatisés : ils exigent
une réconciliation comptable explicite ; changer `PAPER_START_EQUITY` ne recharge
pas un compte existant.

### Utilisation

`backend/.env.example` propose le profil en papier et un univers EUR de candidats.
Ce n'est pas une sélection validée pour le compte : minima, liquidité et frais
doivent être réévalués. Les réglages locaux existants et la base restent intacts.

```bash
cd backend
# Même historique et contraintes, différents budgets, achats mécaniques sans LLM.
.venv/bin/python scripts/compare_budgets.py
.venv/bin/python scripts/compare_budgets.py --budgets 9.37,25,50,1000 --json
# Régression indépendante des fichiers .env et de l'environnement du processus.
.venv/bin/python scripts/backtest.py replay
# Un cycle papier après configuration de PostgreSQL et d'Ollama.
# --config conserve la priorité habituelle des variables d'environnement.
.venv/bin/python -m app.main --config .env.example --once
```

Le comparatif utilise BTC/EUR du 10 octobre au 4 novembre 2026 : fenêtre courte,
mono-paire, sans LLM, avec les frais et minima historiques de la fixture. Il teste
la mécanique, **pas la rentabilité**. Le JSON détaille les refus. Les courbes H4
valorisent chaque clôture pendant la détention, coûts de liquidation compris ;
elles ne mesurent pas les creux tick par tick.

Mesure de cette commande le 2026-11-09, frais taker 0,80 % par côté :

| Budget | Micro : trades / PnL | Progressif : trades / PnL |
|---|---|---|
| 9,37 EUR | 1 / −0,1780 EUR | 0 / 0 EUR |
| 25 EUR | 1 / −0,4748 EUR | 0 / 0 EUR |
| 50 EUR | 1 / −0,9497 EUR | 1 / −0,2500 EUR |
| 1 000 EUR | 1 / −18,9937 EUR | 1 / −5,0000 EUR |

Cette fenêtre vérifie que le budget de perte prévu est respecté dans le scénario
observé ; elle ne prouve ni rentabilité ni amélioration statistique. Le nombre de
trades est insuffisant pour une décision d'argent réel.

Avant toute conclusion : plusieurs années et régimes, période hors calibration,
frais effectifs, minimums respectés, comparaison au cash et au buy & hold. Exiger
une espérance nette positive et une baisse maximale compatible avec le profil.
Zéro trade et zéro perte ne suffisent jamais à valider une stratégie.


<a id="memoire-paper"></a>

## Mémoire d’expérience automatique en simulation

Le moteur continue à produire les situations : achat refusé par le pare-feu ou
attente du modèle sur une entrée détectée. Un plan reste soumis aux mêmes minima
et règles : sans plan finançable, `NO_PLAN` n’est pas transformé en achat fictif.
Les ordres PAPER exécutés continuent à vivre dans `positions`/`orders`. Les plans
non pris restent dans `shadow_trades`, sans débit du portefeuille.

Les nouvelles lignes portent `simulation_context.version=forward-v1` : date
observée, période, horizon de 42 bougies, frais et glissement hypothétiques, identité
de politique et modèle de sortie fixe. À la différence du rapport historique, le
worker ne clôture pas à la simple fin d’un téléchargement. Il attend un stop,
un objectif ou l’horizon défini à la décision. Les bougies futures/non clôturées
sont exclues. Un trou d’historique ou une sortie impossible à dater dans la bougie
d’entrée partielle produit un cas non exploitable, sans PnL inventé. Les deux
niveaux touchés dans une bougie donnent priorité au stop. Ces plans fixes ne
reproduisent pas le trailing ni toutes les sorties du moteur.

`backend/scripts/learning.py --serve` fait un lot horaire, dans un processus séparé.
Il relit au plus 30 transactions PAPER closes et 30 simulations éligibles, avec
20 résultats minimum. Après une première version, il faut 5 nouveaux identifiants
et 24 h avant une autre relecture. Chaque passage est journalisé dans `learning_runs`.
Une panne de modèle, une annulation ou une mémoire modifiée en parallèle ne remplace
pas l’ancienne version. Le changement de version et le succès du passage sont
atomiques. Une mémoire expire après 30 jours. Ces effectifs organisent la cadence ;
ils ne prouvent ni indépendance des observations ni fiabilité statistique.

Le worker refuse LIVE avant accès à la base. Son groupe `tn_paper_learner` n’a
aucun droit d’ordre, de changement d’état ou de réglage financier. Le constructeur
exchange reçoit des clés vides et ne sert qu’aux OHLC publics. Les leçons PAPER
ne sont pas promues vers LIVE. Les anciennes simulations sans provenance restent
consultables mais sont exclues de la boucle automatique.

Le dashboard distingue service non lancé, attente, passage en cours, leçons
publiées, échec et preuve trop ancienne. `GET /api/insights/learning` est privé,
PAPER uniquement et sans commande associée. L’historique conserve cinq versions.
Pour mesurer l’effet de la mémoire, utiliser les expériences ou les mesures A/B
existantes, sur un jeu distinct des résultats ayant servi aux leçons : aucune
amélioration automatique de rentabilité n’est revendiquée.
