# Améliorations à prévoir — backend et frontend

**Date : 9 novembre 2026 (UTC)**  
**Référence :** PR [#1](https://github.com/bikininjas/trading-ninja/pull/1),
code relu au commit `7522325`.

Objectif : rendre le bot fiable et compréhensible avec une petite mise, une
réserve protégée et des augmentations graduelles du capital actif.

Ce document consigne la relecture et les propositions suivantes. Les constats
reposent sur le code ; ils ne constituent pas tous des incidents reproduits.
Les critères ci-dessous décrivent les preuves à obtenir lors de leur traitement.
**Mise à jour D-073 :** le tableau ci-dessous décrit les corrections livrées depuis
la revue ; les paragraphes suivants restent les constats d’origine et les critères
à satisfaire entièrement.

| Point | État après les dix briefs |
|---|---|
| B-01 | Corrigé : pool borné, connexion par requête, preuve de 24 lectures API concurrentes sur PostgreSQL 17 local |
| B-02 | Révisions demandée/appliquée, prévisualisation, refus et CAS livrés ; revue des autres handlers attachés à la configuration de démarrage encore nécessaire |
| B-03 | Écritures locales d’entrée regroupées en transaction ; intention d’achat durable et réconciliation des résultats inconnus encore bloquantes |
| B-04, B-05 | Restent bloquants avant réel : sémantique des urgences, exécutions partielles/incertaines |
| B-06 | Identification et couverture des stops renforcées pour le broker et l’observateur ; suivi complet des remplacements et historique des fills encore à qualifier |
| B-07 | Preuve de cycles et surveillant avec fraîcheur explicite livrés ; les autres sondes de télémétrie gardent leurs limites |
| F-01 | Parcours des opportunités, causes pré-LLM et codes de refus visibles ; aucune proposition automatique de financement |
| F-02 à F-06 | Restent à traiter intégralement ; I-02/I-06 fournissent une partie de l’analyse des coûts |
| V-01 | Portefeuilles indépendants et hypothèses reproductibles disponibles ; mesure multi-années positive non obtenue |
| V-02 | Preuves locales Python/SQL/Node/navigateur exécutées ; automatisation GitHub à installer |

Guide livré : [OBSERVATION_ET_RECHERCHE.md](docs/OBSERVATION_ET_RECHERCHE.md).
Le surveillant et le classement ne ferment pas les défauts d’exécution réelle.

La PR apporte déjà les trois enveloppes, les paliers bornés, la clôture comptable
atomique, le curseur papier et les tests associés. Les points ci-dessous complètent
ce travail. La fusion du code ne vaut ni déploiement de la migration 0010,
ni activation du réel, ni démonstration de rentabilité.

## 1. Backend

### B-01 — Connexions PostgreSQL de l'API

**Priorité : haute, dès le mode papier.**

L'API crée un seul repository avec une seule connexion asyncpg et le partage entre
les requêtes. Statut, statistiques, historique et flux temps réel peuvent utiliser
cette connexion simultanément. Asyncpg ne permet pas plusieurs opérations
concurrentes sur une même connexion.

Prévoir un pool avec une connexion réservée par opération et des limites explicites.
Conserver une connexion unique pendant chaque transaction et le verrou de cycle
sur sa connexion dédiée.

**Validation attendue :** requêtes concurrentes et plusieurs clients temps réel
sans erreur de connexion occupée, mélange de transactions ou fuite de connexions.

**Sources :** [API et lifespan](backend/app/api/main.py),
[repository](backend/app/db/repository.py).

### B-02 — Réglages enregistrés et réellement appliqués

**Priorité : haute, dès le mode papier.**

Le dashboard peut annoncer qu'un réglage est enregistré alors que le moteur le
refuse au prochain cycle. L'API conserve aussi une configuration de démarrage
pour certaines réponses et commandes, tandis que le moteur recharge ses réglages.
Les paramètres affichés peuvent donc différer des paramètres effectivement utilisés.

Ajouter une version des réglages demandés, une version appliquée par le moteur,
un horodatage d'application et un motif de refus. Valider les changements de profil
contre les contraintes du compte existant avant leur enregistrement. Les réglages
non appliqués doivent rester visibles comme tels.

**Validation attendue :** après une modification, les paramètres utilisés pour une
décision correspondent à la version affichée ; un changement incompatible avec
les enveloppes existantes est refusé ou explicitement signalé, sans faux succès.

**Sources :** [routes de réglages](backend/app/api/main.py),
[validation des réglages](backend/app/api/settings_store.py),
[rechargement du moteur](backend/app/runtime/orchestrator.py).

### B-03 — Entrées atomiques et reprise après interruption

**Priorité : haute.**

La clôture a été renforcée, mais l'achat précède encore plusieurs écritures
indépendantes : position, ordre d'entrée, ordre de protection et snapshot du solde.
Une interruption peut laisser une comptabilité incomplète.

Persister une intention d'achat et un identifiant stable avant l'envoi. Enregistrer
le résultat confirmé et regrouper les écritures comptables locales. Reprendre
l'intention par réconciliation sans renvoyer un achat dont l'exécution est incertaine.

**Validation attendue :** interruptions avant et après chaque étape sans double
achat, double débit ou position perdue dans le ledger. Un résultat externe inconnu
reste bloquant jusqu'à réconciliation.

**Source :** [entrée en position, `_enter()`](backend/app/runtime/orchestrator.py).

### B-04 — Sémantique et protection du kill-switch

**Priorité : haute, bloquante avant le réel.**

Le handler API du bouton kill-switch annule les ordres puis halte le bot, sans
nécessairement liquider les positions. Annuler un stop et arrêter la surveillance
peut laisser une position sans protection. Ce handler est distinct du script de
liquidation autonome.

Définir précisément trois actions : arrêter les nouveaux achats, conserver les
protections et liquider les positions. Une commande d'arrêt ne doit retirer une
protection qu'avec un traitement explicite de l'exposition restante.

**Validation attendue :** pour chaque commande et chaque panne réseau, le résultat
indique ce qui reste détenu et protégé. Aucun stop retiré sans liquidation confirmée
ou signalement bloquant de la position exposée.

**Sources :** [handler API `_default_kill_switch()`](backend/app/api/main.py),
[liquidation autonome](backend/scripts/kill_switch.py).

### B-05 — Exécutions partielles et réponses exchange incertaines

**Priorité : haute, bloquante avant le réel.**

Après un timeout, l'ordre peut être partiellement ou totalement exécuté alors que
le client ne connaît pas son résultat. Le blocage de certaines répétitions est
présent, mais la résolution et la comptabilité des exécutions partielles restent
à compléter. Réarmer une protection sur la quantité initiale peut être incorrect
si une partie a déjà été vendue.

Réconcilier l'identifiant d'ordre, les transactions, les quantités exécutées et
restantes, puis le solde réellement détenu avant toute nouvelle action.

**Validation attendue :** scénarios de remplissage partiel, réponse perdue,
annulation incertaine et lecture impossible. Ni seconde vente excessive, ni
position déclarée intégralement clôturée sans preuve.

**Sources :** [broker live](backend/app/execution/live.py),
[réconciliation](backend/app/runtime/reconciliation.py).

### B-06 — Identifiants réels des stops

**Priorité : moyenne ; à régler avant utilisation réelle du déplacement de stop.**

Un remplacement de stop peut renvoyer un nouvel identifiant que l'orchestrateur
ne persiste pas partout. Le getter de stop exposé au dashboard sélectionne un
identifiant interne d'ordre, pas nécessairement l'identifiant distant Kraken.

Persister le nouvel identifiant exchange après confirmation et distinguer clairement
identifiant interne, identifiant distant et protection simulée.

**Validation attendue :** après remplacement et redémarrage, la base et l'interface
référencent le stop réellement actif chez l'exchange.

**Sources :** [déplacement de stop](backend/app/runtime/orchestrator.py),
[`stop_order_id()`](backend/app/db/repository.py).

### B-07 — Télémétrie fidèle et état du moteur

**Priorité : haute pour la supervision.**

Une lecture du quota qui échoue peut laisser des valeurs par défaut affichées comme
« DB ok ». La sonde Ollama peut construire une mauvaise URL si la configuration
se termine par `/v1`. La prochaine exécution calculée sur l'horloge ne prouve pas
que le moteur tourne réellement.

Introduire un état « inconnu », normaliser les URL et exposer le dernier cycle
commencé, le dernier cycle réussi, la dernière erreur et l'âge du heartbeat.
Distinguer la disponibilité de l'API de celle du moteur de trading.

**Validation attendue :** panne de base, moteur arrêté et Ollama indisponible
produisent trois diagnostics exacts, sans état vert par défaut ni exécution
future présentée comme garantie.

**Sources :** [télémétrie API](backend/app/api/main.py),
[badges du dashboard](dashboard/public/app.js).

## 2. Frontend

### F-01 — Panneau « Pourquoi le bot ne trade pas ? »

**Priorité : haute pour une petite mise.**

Afficher la cause dominante : capital insuffisant, minimum exchange, frais
disproportionnés, absence de signal, réduction du risque après pertes ou panne.
Présenter les montants et les conditions qui manquent plutôt qu'un seul code technique.

**Validation attendue :** l'utilisateur comprend si le blocage vient du financement,
de la stratégie ou d'un incident. Le diagnostic ne propose pas automatiquement
une hausse du risque ou un apport d'argent pour forcer un achat.

### F-02 — Prochain palier et conditions restantes

**Priorité : haute pour comprendre la progression.**

Afficher le montant manquant dans la cagnotte, la première date possible et les
conditions restantes : récupération des pertes, clôture gagnante, plafond actif
ou cadence déjà consommée. Une date seule ne garantit pas un palier.

**Validation attendue :** chaque blocage a un motif lisible et le montant affiché
respecte les plafonds de 0,25 EUR, de pourcentage et de capital actif configurés.

### F-03 — Historique des mouvements entre enveloppes

**Priorité : moyenne.**

Pour chaque clôture, montrer le résultat net, la réserve ajoutée, la cagnotte
ajoutée et le montant éventuellement promu vers le capital actif. Relier les
mouvements aux positions et aux événements comptables.

**Validation attendue :** les soldes peuvent être reconstruits à partir du point
de départ et des mouvements ; un transfert interne n'apparaît jamais comme un gain.

### F-04 — Gains et seuil de rentabilité nets de coûts

**Priorité : haute pour des gains de quelques centimes.**

Le gain latent de position est calculé avant frais de sortie. Le drapeau de seuil
de rentabilité compare simplement le stop au prix d'entrée. Afficher séparément
le mouvement brut et le résultat estimé après frais et glissement de liquidation.

**Validation attendue :** un mouvement positif qui reste perdant après coûts
n'est pas présenté comme un gain net ; le seuil de rentabilité utilise le même
modèle de coûts que le moteur et précise qu'il s'agit d'une estimation.

**Sources :** [position API](backend/app/api/main.py),
[sérialisation de position](backend/app/api/service.py).

### F-05 — Données périmées et erreurs de flux

**Priorité : haute pour la supervision.**

Conserver les derniers montants connus avec leur heure et une mention « données
anciennes » en cas d'échec API. Valider les messages SSE avant de les traiter
comme des statuts ; une erreur API ne doit pas rafraîchir artificiellement
l'heure de validité des données.

**Validation attendue :** coupure réseau, erreur 500 et reconnexion n'effacent pas
silencieusement les montants et ne donnent pas l'impression d'un état fraîchement
confirmé. Éviter aussi le chevauchement des appels de polling.

**Sources :** [relais SSE](dashboard/server.js),
[rendu et rafraîchissement](dashboard/public/app.js).

### F-06 — Parcours guidé de réconciliation papier

**Priorité : moyenne.**

Expliquer les écarts de compte et les anciennes positions sans curseur. Présenter
un aperçu des corrections, leurs effets sur chaque enveloppe et une confirmation
explicite avant application, avec un journal de l'opération.

**Validation attendue :** réconcilier un compte papier sans SQL manuel, sans effacer
l'historique et sans transformer une correction ou un apport en bénéfice fictif.
Le mode PAPER doit être visible à chaque étape.

## 3. Mesure et validation

### V-01 — Mesurer la politique complète sur plusieurs années

**Priorité : haute avant toute conclusion de rentabilité.**

Rejouer la stratégie avec les trois enveloppes et les paliers sur plusieurs années
et régimes, dont une période hors calibration. Tester frais, spread et glissement
défavorables, puis comparer au cash et au buy & hold sur la même période.

Mesurer le patrimoine total, le capital actif, la réserve, la cagnotte, les paliers,
les refus, le nombre de trades, l'espérance nette et les drawdowns. Conserver les
configurations, données et périodes pour rendre les résultats reproductibles.

**Validation attendue :** aucun choix de paramètres retenu uniquement sur une courte
fenêtre favorable. Zéro trade et zéro perte ne valident jamais une stratégie.
Les résultats négatifs restent visibles.

### V-02 — Contrôles GitHub systématiques

**Priorité : haute pour maintenir la fiabilité.**

Exécuter automatiquement sur les PR : tests Python et Node, PostgreSQL réel jetable
avec migrations depuis zéro, concurrence API, interruptions aux étapes critiques,
golden et contrôle navigateur. Les contrats SQL et navigateur existants doivent
faire partie de cette exécution, avec leurs résultats visibles.

**Validation attendue :** une régression de conservation des fonds, de reprise,
de droits SQL ou d'initialisation du dashboard bloque l'intégration. Les tests
omis ou indisponibles ne sont pas assimilés à des tests réussis.

## 4. Ordre de traitement proposé

| Lot | Contenu | Résultat recherché |
|---|---|---|
| 1 | B-01, B-02, B-03 | API concurrente fiable, paramètres effectifs identifiés et entrées récupérables |
| 2 | B-07, F-01, F-02, F-05 | Comprendre si le moteur fonctionne, pourquoi il attend et pourquoi un palier reste bloqué |
| 3 | F-03, F-04, F-06 | Auditer les centimes, lire les résultats nets et réconcilier sans manipulations SQL |
| Avant tout réel | B-04, B-05, B-06 | Arrêts sûrs, quantités réconciliées et protections distantes correctement suivies |
| En parallèle | V-01, V-02 | Preuves reproductibles et contrôles automatiques |

Le premier lot améliore la fiabilité dès le papier. Les travaux de supervision
rendent ensuite les décisions compréhensibles avec une faible mise. Les critères
de passage au réel restent ceux des documents d'autorité du dépôt : cette liste
ne les remplace pas et n'autorise aucune activation.
