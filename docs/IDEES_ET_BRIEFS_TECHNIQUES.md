# Idées et briefs techniques — Trading Ninja

Date : 9 novembre 2026. **Statut : première version des dix briefs implémentée
(D-073), vérification locale ; activation réelle et qualification économique séparées.**

**Relecture après livraison :** l’[audit pré go live](AUDIT_PRE_GO_LIVE_2026-11-09.md)
a reproduit des défauts du runtime et de plusieurs outils. Les fonctionnalités
restent livrées en première version ; leurs tests actuels ne suffisent pas à les
qualifier pour le réel ou pour une conclusion économique.

Le [guide d’utilisation](OBSERVATION_ET_RECHERCHE.md) décrit le comportement livré,
les formats, commandes et limites. Les sections détaillées ci-dessous conservent
le problème initial, la conception et les critères de qualification ; leurs noms
de contrats proposés ne remplacent pas le schéma et les routes effectivement livrés.
Le socle part de `master` au commit `96b3a9d`, avec capital progressif conservé.

| Brief | Version livrée | Qualification restante |
|---|---|---|
| I-01 | Classement `lexicographic-v1`, observation par défaut, sélection PAPER explicite et revalidation finale | Comparaison hors échantillon avant toute décision de déploiement |
| I-02 | Diagnostics de positions/fills partiels et rapports H4/swing/meanrev, couverture explicite | Historique complet à accumuler ; aucun extrême intrabougie prétendu |
| I-03 | Capture typée, artefacts dédupliqués, rejeu du véritable runtime sans I/O réelle | Rétention, charge et reprise d’un processus interrompu à qualifier en exploitation |
| I-04 | Suspension persistante des achats, révision, TOTP à la réautorisation | Recette administrative sur le déploiement cible |
| I-05 | Jusqu’à trois portefeuilles indépendants, données causales, actions mécaniques/enregistrées | Données multi-années, actions LLM figées, contrôle hors calibration |
| I-06 | Coût de profondeur daté, spread/impact/frais et cas inconnus | Mesure du carnet réel et confrontation aux fills |
| I-07 | Politique immuable par position, versions demandées/appliquées, adoption historique PAPER explicite | Recette de migration des anciennes positions et de changement de profil |
| I-08 | Parcours complet avant/après LLM, opportunités/tentatives/achats séparés | Observation continue et rétention à dimensionner |
| I-09 | Processus indépendant, clé lecture seule, incidents, fraîcheur, webhook, commandes HALT uniquement | Installer le processus, son canal réel et une sonde externe de disponibilité |
| I-10 | Cohortes poids/prompt/leçons/politique, effectifs et biais de sélection | Accumuler des labels clos ; aucune modification automatique du seuil |

Les critères de passage avant argent réel restent dans [ROADMAP.md](ROADMAP.md).
Le profil papier et ses limites sont dans [PROGRESSION.md](PROGRESSION.md).
L’[audit actif](../AMELIORATIONS_2026-11-09.md) distingue les défauts corrigés et
les blocages live encore ouverts. Implémentation ne signifie pas validation de
rentabilité ni autorisation d’activation.

## 1. Vue d'ensemble et ordre conseillé

| ID | Proposition | Bénéfice recherché | Priorité | Taille indicative | Dépendances |
|---|---|---|---|---|---|
| I-01 | Classer les opportunités entre les paires | Choisir selon une règle explicite, sans biais de l'ordre des paires | P2 | M | I-03, I-06 ; évaluation dans I-05 |
| I-02 | Analyser le déroulement de chaque trade | Distinguer problème d'entrée, de sortie et de coûts | P1 | M | Historique disponible ; I-03 pour le suivi complet |
| I-03 | Boîte noire et rejeu d'une décision | Reproduire une décision et expliquer ses écarts | P1 | L | Aucune dépendance fonctionnelle nouvelle |
| I-04 | Terminer la position puis suspendre les achats | Préparer une absence ou une maintenance sans liquidation forcée | P1 | M | Contrôle administratif et verrou existants |
| I-05 | Comparer plusieurs versions en observation | Mesurer l'apport du LLM, des sorties et des leçons | P1 après le socle | L | I-03 ; I-07 pour les versions de gestion |
| I-06 | Budget de coûts avant achat | Mesurer la faisabilité économique et le coût d'exécution | P2 | M à L | Données de marché datées ; I-03 |
| I-07 | Règles de gestion attachées aux positions | Rendre les changements de paramètres prévisibles et auditables | P1 | M | Capture de version commune avec I-03 |
| I-08 | Expliquer pourquoi le bot ne trade pas | Identifier précisément où les opportunités disparaissent | P1 | M | Événements I-03, pouvant commencer par un sous-ensemble |
| I-09 | Surveillant indépendant des protections | Détecter une protection dégradée entre deux décisions H4/D1 | P1 exploitation | L | Lecture exchange indépendante, commandes sérialisées |
| I-10 | Mesurer la fiabilité de la confiance du LLM | Vérifier ce que les scores prédisent réellement | P2 recherche | M | I-03 et I-05, observations résolues suffisantes |

P1 signifie « à traiter en premier parmi ces propositions ». P2 signifie « après
instrumentation ou qualification ». S = local, M = plusieurs couches, L = chantier
transversal. Ce sont des tailles de périmètre, pas des engagements de délai.

Ordre recommandé : I-03 minimal + I-02 + I-08 pour mesurer ; I-04 + I-07 pour
piloter ; I-05 pour comparer ; puis I-06, I-01 et I-10 selon les preuves obtenues.
I-09 avance en parallèle pour l'exploitation : son utilité ne dépend pas d'une
amélioration de la rentabilité.

## 2. Contrat technique commun

- Conserver le pare-feu déterministe. Aucun classement, modèle, rejeu ou diagnostic
  ne peut fabriquer une approbation ou dimensionner un ordre hors de `evaluate()`.
- Réutiliser et compléter le verrou de cycle, les intentions durables de sortie
  et les transactions du ledger pour toute mutation financière. L'audit existant
  demande encore une coordination commune aux urgences et un traitement complet
  des achats/ordres incertains. Les prérequis manquants doivent être traités avant
  de brancher une nouvelle action ; aucun outil ne crée un chemin d'envoi parallèle.
- Argent et quantités en `Decimal` ; chaînes décimales dans les contrats JSON.
  Enregistrer la devise, la précision et les règles d'arrondi. Les algorithmes
  statistiques peuvent utiliser des flottants contrôlés sur des copies analytiques,
  sans les réinjecter comme montants d'exécution.
- Distinguer heure de l'événement, heure de réception et heure de décision, toutes
  en UTC. Aucune décision historique ne consulte des données devenues connues après
  son instant de simulation.
- Versionner les contrats. Les noms de tables, champs et routes du brief restent des contrats de
  conception ; le guide d’utilisation et la migration 0011 donnent les noms livrés.
- Toute persistance nouvelle exige une nouvelle migration ; ne pas modifier 0010
  ni une autre migration déjà appliquée. Tables privées, RLS et autorisations du
  rôle backend à vérifier ; aucune donnée sensible exposée au navigateur.
- Préserver le budget de stockage : résumés compacts en base, volumes/rétention
  mesurés avant d'enregistrer du carnet ou des événements fréquents. Ne pas copier
  les séries OHLC dans chaque événement. Aucun secret dans un paquet de rejeu.
- Pour une modification de `backend/app/`, exécuter les tests utiles et le golden.
  Un résultat économique différent doit être expliqué ; ne jamais le bénir pour
  faire disparaître une régression.

## I-01 — Classement des opportunités entre les paires

### Problème et objectif

`Orchestrator._evaluate_cycle()` retenait à l’origine le premier candidat approuvé
rencontré dans `allowed_symbols`. Les candidats suivants peuvent être évalués,
mais ne remplacent pas ce choix. L'ordre de configuration influence donc la paire
achetée. L'objectif est de rendre cet arbitrage explicite et mesurable en restant
monoposition.

### Périmètre de la première version

Collecter tous les candidats approuvés d'un cycle, les classer avec une règle
versionnée et sélectionner au plus un achat. Enregistrer aussi les candidats
approuvés non sélectionnés. Commencer en observation : calculer le classement
sans changer l'achat, puis comparer cette politique dans I-05.

### Conception et branchement

- Point d'intégration : `backend/app/runtime/orchestrator.py`, après l'évaluation
  des candidats et avant la transition EXECUTING. Garder `_enter()` et son dernier
  passage dans le pare-feu.
- Ajouter un composant pur de classement, séparé de `risk_gate.py`. Entrée proposée :
  `Candidate(cycle_id, symbol, decision_id, observed_at, setup_features,
  cost_estimate, eligibility, policy_version)`.
- Définir le sens et les unités de chaque variable : coût aller-retour rapporté au
  notionnel, marge de liquidité, structure du setup normalisée par ATR. Une distance
  au stop plus faible n'est pas automatiquement une meilleure opportunité.
- La première règle peut être lexicographique : admissibilité, coût estimé, critère
  structurel préétabli, puis symbole pour départager. Fixer la règle avant mesure ;
  éviter une somme de poids ajustée sur les derniers gagnants.
- Retourner classement, composantes, motif du choix et motifs d'exclusion. La
  confiance déclarée du LLM ne représente pas une espérance de rendement.
- Présenter le classement dans l'activité du cycle. Un candidat approuvé mais non
  retenu porte le motif `NOT_SELECTED`, distinct d'un rejet de risque.

### Invariants et cas limites

Comparer des observations suffisamment contemporaines ; un candidat périmé doit
être rafraîchi puis réévalué, ou exclu. Définir une limite de durée du cycle avant
d'élargir l'univers. Une donnée de coût manquante n'est pas un coût nul. Si le
premier candidat échoue à la revalidation, ne pas acheter automatiquement le second
avec son ancienne approbation : le revalider dans le même cadre de risque.

### Critères de validation

- Permuter `allowed_symbols` conserve le choix et les composantes de classement,
  à observations identiques ; les égalités ont un départage déterministe.
- Zéro candidat admissible produit zéro achat ; aucun cycle n'ouvre deux positions.
- Un candidat périmé ou une halte arrivée avant envoi interdit l'achat.
- Le rejeu en portefeuille mesure le coût d'opportunité, les frais et le drawdown.
  Déployer la sélection seulement si la comparaison hors échantillon la justifie.

## I-02 — Analyse du déroulement de chaque trade

### Problème et objectif

Les rapports actuels décrivent surtout l'entrée, la sortie et le résultat final.
La valorisation pendant la détention existe, mais il manque une lecture par trade
qui explique combien du mouvement a été capturé et ce qu'ont coûté les décisions.

### Périmètre de la première version

Ajouter une fiche par position : excursion favorable et défavorable observées,
temps jusqu'au premier gain latent net, durée, rendement réalisé en R, frais,
slippage mesuré et recul depuis le meilleur gain latent. Produire une analyse
purement descriptive avant de proposer des modifications de sortie.

### Conception et branchement

- Réutiliser les fills du ledger et l'historique disponible. Ajouter des calculs
  purs dans une couche analytique ; points de restitution :
  `backend/app/api/stats.py`, `backend/app/backtest/engine.py`, `swing.py`,
  `mean_reversion.py` et `dashboard/public/app.js`.
- Proposer un résumé `TradeDiagnostics(position_id, policy_version, resolution,
  coverage, mfe_quote, mae_quote, first_net_positive_at, peak_net_pnl_quote,
  realized_net_pnl_quote, fees_quote, slippage_quote, realized_r)`.
- MFE/MAE désignent les meilleures/pires excursions observées pendant la détention.
  Afficher explicitement la méthode : clôtures, OHLC avec incertitude ou ticks.
  Une clôture donne une excursion observée, pas un extrême intrabougie exact.
- Pour une quantité constante : PnL latent net estimé = valeur de liquidation
  estimée − coût d'entrée − frais d'entrée − frais de sortie estimés. Pour les
  ventes partielles, additionner réalisé cumulé et latent sur la quantité restante.
- Définir R sur le risque initial enregistré, sans le recalculer après une remontée
  de stop. Afficher séparément risque de distance et risque net de coûts estimé.
- La restitution peut utiliser `GET /api/positions/{id}/diagnostics` : valeurs,
  méthode, couverture, version de calcul et série utile au graphe. Route privée
  paginée/bornée ; éviter un recalcul de toutes les positions à chaque rafraîchissement.

### Invariants et cas limites

Ne pas utiliser des prix postérieurs à la sortie pour gonfler MFE/MAE. Une bougie
contenant entrée et sortie ne permet pas d'ordonner exactement ses extrêmes :
indiquer l'incertitude. Le prix de décision doit être connu pour qualifier un
slippage ; sinon afficher « indisponible ». Le ratio de capture reste indéfini si
le meilleur gain net observé n'est pas positif ; il n'est pas forcé entre 0 et 100 %.

### Critères de validation

Trade gagnant après une forte excursion défavorable, sortie en perte après un
pic favorable, gap, vente partielle et frais dans plusieurs devises sont couverts.
La somme réalisé + latent rejoint le ledger à la clôture. Historique incomplet =
couverture partielle explicite. Les anciens trades sans données suffisantes ne
reçoivent pas de métriques inventées.

## I-03 — Boîte noire et rejeu d'une décision

### Problème et objectif

Le prompt, sa réponse et les reçus d'ordres sont déjà audités. Ils ne constituent
pas encore un paquet autonome permettant de rejouer tout un cycle avec ses données,
sa configuration effective et sa version de gestion. L'objectif est un bouton
« Rejouer cette décision » utilisable pour expliquer un achat ou un incident.

### Périmètre de la première version

Construire d'abord un paquet exportable et un rejeu CLI strictement hors réseau.
Ajouter ensuite un historique de cycle dans le dashboard. Le premier rejeu réutilise
les décisions LLM enregistrées ; réinterroger un modèle constitue une expérience
différente, jamais une reproduction présentée comme exacte.

### Conception et branchement

- Introduire `cycle_id`, `decision_id` et un identifiant de corrélation commun aux
  événements de données, signal, risque, sélection, exécution et comptabilisation.
  Ne pas remplacer `pending_exits`, qui garde son rôle transactionnel de sortie.
  Le journal de diagnostic ne résout pas à lui seul les intentions d’achat manquantes.
- Manifeste proposé : `schema_version`, empreinte du code, état dirty si applicable,
  empreinte des sources effectives, paramètres sans secrets, versions de CCXT et
  du moteur d'indicateurs, modèle et digest de ses poids, prompt, version/texte des
  leçons, horodatages, état du compte avant décision et références des événements.
- Stocker les observations réellement utilisées, avec provenance et précision.
  Référencer des lots de bougies immuables par empreinte ; un simple lien vers une
  table révisable ne suffit pas pour une reproduction durable.
- Ajouter un journal d'événements ordonné par séquence par cycle. Chaque événement
  précise heure source, réception, type, résultat et lien vers son artefact. Une
  empreinte détecte une altération ; elle ne garantit pas seule la non-répudiation.
- Injecter horloge, source de données, réponses LLM et adaptateur exchange de rejeu.
  Brancher autour de `runtime/orchestrator.py`, `market/feed.py`, `llm/client.py`
  et `backtest/golden.py`, en réutilisant les calculs métier existants.
- Sortie : chronologie et premier point de divergence entre résultat enregistré et
  résultat rejoué. Une future route de rejeu crée une tâche bornée sans credentials ;
  le navigateur consulte son résultat, il ne lance pas un exécuteur live.

### Invariants et cas limites

Le rejeu ne possède ni clé privée ni accès au réseau. Il ne modifie pas le ledger
source. Un artefact manquant, un schéma inconnu ou une version incompatible doit
produire `UNREPLAYABLE` avec motif. Les résultats anciens incomplets restent
consultables mais ne sont pas présentés comme reproductibles. Budget et rétention
séparés pour événements compacts et artefacts volumineux ; aucune suppression des
preuves liées à une exécution financière non résolue.

### Critères de validation

Un paquet complet reproduit décisions, montants, états et comptabilisation avec
les mêmes entrées. Couper le réseau ne change pas son résultat. Modifier un
paramètre ou une bougie produit un écart localisé ; modifier un artefact sans
mettre à jour son empreinte invalide le paquet. Vérifier absence de secrets à
l'export et comportement après interruption puis reprise d'un export.

## I-04 — Terminer la position puis suspendre les achats

### Problème et objectif

`api/service.py::pause()` refuse actuellement une pause avec position ouverte.
L'utilisateur doit attendre ou liquider. Ajouter une suspension des nouvelles
entrées qui laisse fonctionner toute la gestion de l'exposition existante.

### Périmètre de la première version

Deux intentions lisibles dans le dashboard : « Suspendre les nouveaux achats » et
« Réautoriser les nouveaux achats ». Une position ouverte continue à être suivie ;
après sa clôture, aucune nouvelle entrée ne part avant réautorisation. Ne pas
ajouter de reprise automatique à une halte d'urgence.

### Conception et branchement

- Persister un contrôle distinct de la machine financière : par exemple
  `entry_permission = ENABLED | DISABLED`, motif, auteur, date et révision.
  La machine à états continue à décrire exécution, position, cooldown et urgence.
- Points d'intégration : `api/service.py`, routes de contrôle dans `api/main.py`,
  `_evaluate_cycle()` et `_enter()` dans `runtime/orchestrator.py`, lecture dans
  `api/stats.py` et affichage dans `dashboard/public/app.js`.
- Contrat proposé : commande authentifiée avec action et révision attendue ;
  réponse avec permission persistée, exposition suivie et éventuel ordre déjà parti.
  Ne pas détourner silencieusement la sémantique actuelle de `/pause` et `/resume`.
- Vérifier la permission avant évaluation d'achat et juste avant envoi, sous le
  mécanisme de propriété financière existant. Définir l'instant de prise d'effet :
  une commande ne prétend pas annuler rétroactivement un envoi déjà engagé.
- Autoriser la désactivation des entrées dans une transaction courte, même si un
  cycle détient le verrou ; sérialiser la réactivation et refuser celle-ci en HALT,
  risque verrouillé ou présence de reçu non rapproché.

### Invariants et cas limites

Stop, trailing, SELL, HALT, liquidation et rapprochement restent actifs. Suspendre
les entrées ne clôture rien et ne remet pas les compteurs de risque à zéro. Une
réautorisation des entrées ne lève pas une urgence ; les deux contrôles restent
visibles séparément. Le contrôle persiste après redémarrage.

### Critères de validation

Tester commande à plat, en position, en cooldown, pendant l'inférence et à la
frontière d'envoi. Si un achat était déjà parti, le signaler et le gérer normalement.
Un stop touché pendant la suspension clôture la position ; le cycle suivant n'achète
pas. Répéter une commande ne change pas son effet et une révision obsolète est refusée.

## I-05 — Comparer plusieurs versions du bot en observation

### Problème et objectif

L'évaluation actuelle du LLM peut mesurer son filtrage sur des trades mécaniques.
Cela ne capture pas tous les effets sur le portefeuille : refuser un achat libère
du capital et peut faire apparaître une autre entrée. Il faut comparer des politiques
entières sur le même flux, chacune avec son propre compte simulé.

### Périmètre de la première version

Trois variantes au maximum : mécanique seule, politique LLM actuelle, et une variante
figée de prompt, de leçons ou de sortie. Aucun ordre réel et aucune promotion
automatique. Chaque variante possède cash, position, pertes, cooldown et frais ; les variantes
progressives conservent aussi capital actif, réserve, cagnotte et paliers indépendants.

### Conception et branchement

- Ajouter un manifeste d'expérience immuable : `experiment_id`, hypothèse,
  périodes de conception/validation, versions de code/données/modèle/prompt/leçons,
  configuration et budget initial identiques lorsque la comparaison le demande.
- Créer un moteur de portefeuille réutilisant les règles existantes, avec source
  d'événements commune fournie par I-03. Les variantes avancent selon la même horloge
  logique ; aucune ne reçoit une observation du futur ou un résultat d'une autre.
- Identifiants et ledger simulé isolés du mode LIVE et de ses intentions financières durables.
  Les namespaces d'expérience empêchent toute confusion avec les ordres du bot.
- Pour les comparaisons sans/après LLM, figer les décisions enregistrées. Une
  campagne de nouvelles inférences porte sa propre version et son coût de calcul.
- Intégration : moteurs de `backtest/`, interfaces de signaux de `llm/client.py`,
  `runtime/shadow.py` pour l'analyse des refus, rapports dans `api/stats.py` puis
  un écran « Expériences ». Ne pas sommer les PnL de shadows indépendants pour
  les présenter comme une courbe de portefeuille.
- Restituer N, rendement net, exposition, drawdown, frais, refus, coûts d'opportunité
  et différences sur les mêmes périodes. Estimer l'incertitude par blocs temporels,
  sans traiter des trades corrélés comme des observations indépendantes.

### Invariants et cas limites

Les expériences ne chargent aucune clé de trading et ne changent pas les paramètres
actifs. Les leçons d'une variante ne traversent pas vers les autres. Enregistrer les
variantes testées, y compris négatives, pour rendre visible la multiplicité des essais.
Une période de validation déjà consultée ne redevient pas « hors échantillon » après
ajustement. Les preuves d'amélioration sont distinctes de la réussite des tests logiciels.

### Critères de validation

Deux variantes identiques ont les mêmes résultats à données/décisions identiques.
Un achat refusé dans A mais pris dans B provoque les différences attendues de cash
et d'opportunités suivantes. Frais, minima, cooldown et coupes-circuits s'appliquent
à chaque portefeuille. Une perte du runner d'expérience ne perturbe pas le runtime.
Aucune promotion sans revue humaine et mesure sur une période réellement réservée.

## I-06 — Budget de coûts avant chaque achat

### Problème et objectif

`execution/paper.py` applique actuellement un slippage fixe. Le profil progressif
intègre déjà les frais et le glissement configurés au sizing, avec les fonctions
pures de `engine/costs.py` et un scénario net de coûts dans le pare-feu. Il manque
une estimation fondée sur le spread et la profondeur observés, puis son rapprochement
avec les fills. L'objectif est d'expliquer si un ordre est techniquement exécutable
et économiquement cohérent avec ses frais et ses conditions de marché.

### Périmètre de la première version

Produire une estimation en observation avant toute décision bloquante. Afficher
frais, spread, slippage estimé, prix d'équilibre et coût rapporté au risque initial.
Comparer ensuite estimations et fills pour qualifier le modèle.

### Conception et branchement

- Calcul pur proposé : `estimate_costs(order_intent, market_observation, fee_schedule)`.
  Retour : `CostEstimate(as_of, quote_currency, reference_price, entry_vwap,
  estimated_exit_cost, fees_quote, spread_quote, impact_quote, assumptions,
  coverage, model_version)`.
- Marché : best bid/ask, profondeur utile à la taille considérée, dates source et
  réception, précision, minima et statut du marché. Refuser les carnets incohérents,
  périmés ou insuffisants au lieu d'estimer un impact nul.
- Pour un achat, estimer le prix moyen en consommant les asks disponibles. Pour
  une vente, utiliser les bids. Les frais restent séparés. Si le VWAP est déjà
  comparé au midpoint, ne pas ajouter une deuxième fois spread et impact.
- Les coûts de sortie futurs sont un scénario, pas le carnet futur connu : tarif
  conservateur et hypothèses de liquidité versionnés. Un stop marché ne garantit
  ni son prix exact ni une perte maximale absolue en présence d'un gap.
- Points d'intégration : `market/feed.py` et adaptateur exchange pour la capture,
  `engine/costs.py` pour conserver une définition unique des coûts,
  `execution/paper.py` pour une simulation optionnelle, `engine/affordability.py`
  pour les contraintes nettes et I-02 pour la comparaison estimation/réalisation.
- Ajouter au diagnostic prétrade un seuil de rentabilité net et, pour un TP fixé,
  sa marge après coûts. Sans TP, ne pas inventer une espérance de rendement : utiliser
  seulement le budget de coûts et des scénarios de risque.

### Invariants et cas limites

Aucun coût retourné par le LLM. Si un futur filtre doit bloquer une entrée ou
modifier sa taille, l'intégrer explicitement au pare-feu et à son contrat SQL,
plutôt que créer un filtre caché dans le broker. Une meilleure estimation ne permet
ni d'augmenter le budget de risque ni de contourner les minima d'un petit compte.
Les frais dans une devise non valorisable restent signalés avec hypothèse explicite.

### Critères de validation

Carnet large/fin, taille dépassant le premier niveau, données périmées, carnet vide,
arrondi et frais dans plusieurs devises sont couverts. Pas de double comptage du
spread. Les estimations sont rapprochées des coûts réalisés par taille/liquidité ;
les données historiques sans carnet sont marquées « scénario », pas « exécution
reconstituée ». Exemple de contrôle : cible brute 1 %, coût estimé 1,2 % → marge nette
négative, sans que cet exemple constitue un réglage réel.

## I-07 — Règles de gestion attachées à chaque position

### Problème et objectif

`Orchestrator._refresh_settings()` recharge les réglages de risque actifs. Certaines
règles de gestion d'une position existante peuvent ainsi changer pendant sa vie.
L'objectif est de pouvoir expliquer quelle politique gère chaque position et de
prévisualiser les effets d'un changement administratif.

### Périmètre de la première version

Enregistrer une version de gestion à l'entrée ; utiliser cette version pour les
règles stratégiques de sortie. Les nouveaux réglages s'appliquent par défaut aux
prochaines positions. Ajouter une prévisualisation des changements avant application.

### Conception et branchement

- Définir `PositionPolicy` : version du contrat, stratégie, timeframe, paramètres
  de breakeven/trailing/sortie, versions de calcul, empreinte et configuration
  normalisée. Capturer valeurs effectives, pas seulement le nom d'un preset.
- Ajouter une référence privée depuis la position vers cette politique immuable.
  Partager la même définition et la même empreinte avec I-03 et I-05.
- Séparer explicitement deux ensembles : règles stratégiques figées par position,
  et limites de sécurité globales actuelles. HALT, permission d'entrée, plafonds durs
  et protections de l'exécution ne sont jamais gelés dans une ancienne politique.
- Points d'intégration : `api/settings_store.py`, `_refresh_settings()`, `_monitor()`
  et `_enter()` dans `runtime/orchestrator.py`, persistance des positions et moteurs
  de backtest. Éviter un second moteur de sortie qui divergerait du runtime.
- Contrat de prévisualisation proposé : changement demandé + révision attendue ;
  réponse avec positions affectées, valeurs avant/après, restrictions et date de
  prise d'effet. Une application ultérieure revalide la révision et l'état courant.
- Première version : ne pas migrer automatiquement une position ouverte vers une
  nouvelle politique. Une telle commande future nécessiterait journal, verrou et
  vérification des stops distants concernés.

### Invariants et cas limites

Un ancien contrat ne peut pas desserrer le stop déjà atteint ni neutraliser une
nouvelle urgence. Conserver une politique par position ne permet pas de prolonger
une version de code connue dangereuse. Pour une position historique sans politique
fiable, indiquer la provenance et imposer une décision de migration ; ne pas lui
attribuer rétroactivement un preset comme s'il était prouvé.

### Critères de validation

Un changement de profil n'altère pas discrètement les règles de la position en
cours. Une nouvelle position reçoit la nouvelle politique. Une urgence et une limite
dure restent effectives sur toutes les positions. Redémarrage et rejeu retrouvent
la même version. Une prévisualisation devenue obsolète ne peut pas être appliquée.

## I-08 — Expliquer pourquoi le bot ne trade pas

### Problème et objectif

Le dashboard expose les signaux et les rejets, mais une grande partie du parcours
précède le LLM : données manquantes, absence de setup, bougie déjà traitée. Ces cas
restent surtout des messages du cycle. L'objectif est un diagnostic complet de
l'inactivité et des opportunités écartées.

### Périmètre de la première version

Afficher par période et paire : observations attendues/reçues, données utilisables,
setups, avis du modèle, approbations de risque, candidats retenus et ordres exécutés.
Ajouter la dernière raison explicite d'absence d'achat et sa date.

### Conception et branchement

- Émettre des événements structurés aux embranchements de `_evaluate_cycle()` et
  `_enter()`. Réutiliser les identifiants I-03 ; un premier sous-ensemble du journal
  peut suffire, sans attendre tout le moteur de rejeu.
- Une unité d'opportunité est définie par paire + bougie de décision + version de
  politique. Plusieurs appels du scheduler ne doivent pas multiplier le dénominateur.
  Chaque tentative garde néanmoins son ID pour diagnostiquer les reprises.
- Codes proposés : `DATA_UNAVAILABLE`, `ALREADY_EVALUATED`, `NO_SETUP`, `LLM_HOLD`,
  `LLM_UNAVAILABLE`, `RISK_REJECTED`, `APPROVED`, `NOT_SELECTED`, `ENTRY_DISABLED`,
  `REVALIDATION_REJECTED`, `SUBMITTED`, `EXECUTION_UNCERTAIN`, `FILLED`.
  Ce sont des motifs de parcours, distincts des enums SQL de rejet du pare-feu.
- Définir un motif terminal par tentative et des événements intermédiaires non
  exclusifs. Conserver motif de sélection et résultat d'exécution séparément : une
  approbation n'est pas un achat.
- Proposer `GET /api/diagnostics/opportunities?from=...&to=...&symbol=...` avec
  bornes, couverture, dénominateurs et ventilation. Calculer des agrégats pour le
  dashboard, sans relire des logs textuels ni tout l'historique à chaque appel.
- Afficher « données insuffisantes » quand le processus n'a pas produit de preuve.
  Séparer arrêt volontaire, panne et absence normale de setup.

### Invariants et cas limites

Un compteur de tentatives, un nombre de bougies et un nombre de fills ne sont pas
interchangeables. La somme des motifs terminaux doit correspondre aux tentatives
terminées ; les étapes traversées ne s'additionnent pas entre elles. Ne pas faire
exécuter au diagnostic une action financière ni un nouvel appel LLM.

### Critères de validation

Même bougie vue plusieurs fois, crash avant signal, refus au dernier passage du
pare-feu, approbation non sélectionnée, suspension d'entrée et réponse exchange
perdue sont distingués. Les pourcentages exposent leur dénominateur. Les agrégats
sont reproductibles depuis les événements et leur couverture est visible.

## I-09 — Surveillant indépendant des protections

### Problème et objectif

La boucle principale aligne ses décisions sur H4/D1 et le stop distant apporte la
protection pendant son sommeil. Il manque un surveillant spécialisé capable de
signaler rapidement un écart de protection ou de processus entre deux décisions.

### Périmètre de la première version

Un processus de lecture vérifie périodiquement : quantité détenue, couverture par
un vrai stop, dernier rapprochement, reçus non résolus et activité du runtime. Il
alerte et peut demander un HALT ; il ne crée, modifie ou annule aucun ordre lui-même.

### Conception et branchement

- Réutiliser les contrôles purs de `runtime/reconciliation.py` et de couverture de
  `execution/live.py`. Exposer des observations datées, sans importer une logique
  de liquidation dans le surveillant.
- Définir `ProtectionObservation(position_id, observed_at, exchange_at,
  base_total, stop_remaining, stop_price, status, evidence)` et une durée de validité.
  Un timeout donne `UNKNOWN`, jamais `PROTECTED` ou « position plate ».
- Utiliser une clé de lecture dédiée quand Kraken le permet. Partager une clé privée
  entre processus peut provoquer des collisions de nonces ; à défaut, centraliser
  la signature et les appels privés. Ne pas contourner le contrôle des permissions
  de la clé de trading pour ce lecteur.
- Commencer en REST avec intervalle configurable et respect du rate limit. Le
  WebSocket privé peut accélérer la détection ultérieurement, mais exige gestion
  des déconnexions, des trous de séquence et une resynchronisation REST.
- Séparer heartbeat du processus, dernier cycle commencé/terminé et échéance de
  décision. Un bot D1 qui dort normalement ne doit pas être déclaré mort, et une API
  joignable ne prouve pas que le bot tourne.
- Dédupliquer les incidents et conserver ouverture, dernière preuve, acquittement
  et résolution. Une notification acquittée ne signifie pas que la protection est
  rétablie. Afficher dernière observation et âge dans la santé du dashboard.
- Toute demande d'action passe par une commande persistée identifiée. L'exécuteur
  unique acquiert le verrou, relit le compte et applique les contrôles normaux.
  L'absence d'exécuteur déclenche l'alerte et la procédure de secours prévue.

### Invariants et cas limites

Le surveillant ne devient pas un broker parallèle. Deux instances ne doivent pas
multiplier les actions d'urgence. Préserver les protections sur simple défaut de
lecture ; éviter d'annuler un stop pour diagnostiquer sa présence. Une perte de
base ou du surveillant doit être visible par une sonde externe, avec accès de
secours indépendant : le processus ne peut pas signaler sa propre extinction.

### Critères de validation

Stop absent, TP pris pour un stop, quantité partiellement couverte, timeout de
lecture, runtime bloqué, sommeil D1 normal et données périmées sont distingués.
Détection dans l'intervalle configuré + timeout de lecture ; réception des alertes
éprouvée sur staging. Deux surveillants et un panic produisent au plus une action
financière après revalidation, sans collision de nonces ni double vente.

## I-10 — Fiabilité de la confiance du LLM

### Problème et objectif

Le modèle émet une confiance dans [0,1], et des seuils influencent ses intentions.
Ce score ne constitue pas une probabilité démontrée de gain. Les métriques actuelles
de rétention/filtration ne suffisent pas à établir sa calibration ou son utilité.

### Périmètre de la première version

Un rapport hors exécution répond à trois questions : le score sépare-t-il les
situations utiles, reste-t-il stable selon les régimes, et apporte-t-il plus qu'une
règle simple ? Ne pas adapter automatiquement le risque ou les seuils.

### Conception et branchement

- Définir d'abord ce que le score prétend prédire. La confiance dans une action ne
  doit pas être assimilée après coup à « probabilité de trade gagnant ». Pour une
  calibration probabiliste future, créer un contrat de prédiction distinct et figé.
- Pour BUY, définir un label reproductible, par exemple résultat net positif sous
  une politique de sortie et de coûts versionnée. Lier observation, échéance de
  résolution, score, version du modèle/prompt/leçons et résultat. SELL, HOLD et HALT
  ont des objectifs différents : ne pas les mélanger dans une seule courbe.
- Utiliser les portefeuilles I-05 pour évaluer les conséquences de la politique et
  des labels contrefactuels clairement signalés pour les refus. La seule population
  des trades acceptés crée un biais de sélection.
- Intégration : `backtest/llm_eval.py`, signaux de `llm/client.py`, résultats de I-02
  et manifestes I-03. Ajouter un rapport de recherche puis une vue dashboard avec
  période, version, effectif et couverture.
- Première analyse : effectifs et résultats par tranche de score, comparaison à un
  modèle constant, stabilité temporelle et fréquence d'abstention/repli. Calculer
  Brier ou log-loss uniquement si la signification probabiliste du score a été
  définie ; ne pas donner un vernis probabiliste à une simple note qualitative.
- Réserver une fenêtre chronologique pour la validation. Ajuster une transformation
  de calibration éventuelle sur la seule période d'apprentissage et figer sa version.
  Attendre la résolution des résultats avant de les utiliser ; agréger l'incertitude
  par blocs pour tenir compte de l'autocorrélation et des positions qui se chevauchent.

### Invariants et cas limites

Petits effectifs, périodes sans perte/gain et observations non résolues restent
signalés « insuffisant ». Ne pas mettre un timeout HOLD de repli dans les convictions
volontaires du modèle. Ne pas mélanger deux digests de poids sous le même nom de
modèle. Une bonne calibration n'établit ni une espérance positive après frais ni la
permission d'augmenter la taille des positions.

### Critères de validation

Données synthétiques avec scores utiles, inversés et constants donnent les conclusions
attendues. Aucun résultat futur ne fuit dans la transformation de calibration. Les
comptes par tranche et labels sont retraçables aux sources. Un changement de prompt
ou de leçons sépare les cohortes. Aucun endpoint analytique ne change les seuils ou
le sizing du runtime.

## 3. Découpage en livraisons vérifiables

1. **Mesure et explication** : schéma minimal I-03, diagnostics I-02, événements de
   parcours I-08. Livrable : expliquer un cycle et un trade sans interpréter des logs.
2. **Pilotage prévisible** : permission d'entrée I-04 et contrats de position I-07.
   Livrable : suspendre les achats et prévisualiser les réglages sans perdre la gestion.
3. **Comparaison contrôlée** : runner I-05 et rapport initial I-10. Livrable : comparer
   une politique mécanique, le LLM et une variante avec comptes simulés indépendants.
4. **Choix et exécution mesurés** : estimation I-06, classement I-01 d'abord en
   observation, puis mesure hors échantillon. Livrable : expliquer pourquoi cette
   paire a été retenue et comparer coûts estimés/réalisés.
5. **Exploitation indépendante** : I-09, parallèlement aux autres lots. Livrable :
   détecter une protection inconnue/dégradée et éprouver le circuit d'alerte sur staging.

Pour démarrer un lot, créer sa décision et son étape dans la roadmap, détailler
les migrations/contrats nécessaires et figer ses critères avant l'implémentation.
La réussite d'un test logiciel ne vaut pas validation économique. Aucun de ces lots
ne supprime les critères de [ROADMAP.md](ROADMAP.md) ni les blocages de l’audit existant.
