# Observation, pilotage et recherche

Les dix briefs I-01 à I-10 ont une première version utilisable. Ils ajoutent une
lecture des décisions et des outils papier. Ils ne démontrent aucune rentabilité
et ne lèvent aucun critère de passage au réel de [ROADMAP.md](ROADMAP.md).
Les comportements et limites précis de cette livraison sont décrits ici ; les
[briefs](IDEES_ET_BRIEFS_TECHNIQUES.md) conservent les objectifs et critères de
qualification à long terme.

## Installation et réglages

Appliquer les migrations sur la base de déploiement après sauvegarde, puis
redémarrer le moteur, l'API et le dashboard avec la même version du code :

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
`0010` et les migrations antérieures restent inchangées.

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
Le moteur relit la permission avant l'analyse puis avant l'envoi ; une suspension
pendant l'inférence interdit l'achat prévu. Une exécution déjà envoyée doit être
rapprochée ; cette commande n'annule pas un ordre exécuté par l'exchange.

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
est détecté avec `--expect-runtime`, en respectant la cadence H4/D1. Les incidents
sont persistés, dédupliqués et historisés ; l'acquittement ne vaut pas résolution.
Seule une observation complète sans l'écart peut le résoudre.

Le surveillant ne vend, n'annule et ne remplace aucun ordre. Sa seule commande est
HALT en base, consommée par le propriétaire du moteur ; le blocage est aussi posé
immédiatement pour empêcher une nouvelle entrée. Un HALT humain/surveillance sans
échéance n'est pas remplacé par le cooldown d'un HALT LLM. La fraîcheur devient
STALE après `2 × intervalle + 5` secondes : un surveillant mort n'est jamais vert.

La notification HTTPS ne suit pas les redirections et expire après cinq secondes.
La déduplication des envois est locale au processus ; un redémarrage peut renotifier
un incident persistant. Il reste à qualifier le canal réel et installer une sonde
externe de disponibilité du surveillant. Aucun service, clé ou alerte réelle n'a
été créé par cette livraison.

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
