# Roadmap vers le go live définitif

**Version mise à jour le 10 novembre 2026. Statut actuel : NO-GO pour le réel.**

Les lots de correction sont suivis dans [CORRECTIONS_PRE_GO_LIVE.md](CORRECTIONS_PRE_GO_LIVE.md).
Les migrations 0010–0013 sont désormais appliquées et vérifiées sur Supabase :
[preuve de déploiement](DEPLOIEMENT_SUPABASE_2026-11-10.md). La recette continue,
la sauvegarde complète, la preuve économique et l’activation réelle restent distinctes.

Ce document devient le plan de référence pour le passage en réel. La
[roadmap générale](ROADMAP.md) donne l’avancement ; les [preuves d’audit historiques](audits/2026-11-09/README.md)
portent sur `39db447` et le [suivi des corrections](CORRECTIONS_PRE_GO_LIVE.md)
décrit les correctifs livrés. Les anciennes checklists ne permettent pas de sauter une porte.

Objectif final : un bot déployé sur une version figée, dont les ordres, protections,
comptabilité, limites et récupération sont qualifiés sur le compte dédié, puis
observés en réel à budget constant. Le go live ne garantit ni rentabilité future,
ni absence totale de panne.

## Ordre et responsabilités

**R0 → R1 → R2 → R3 → R4 → R5 → R6 → R7.**

R3 et la préparation des mesures R4 peuvent avancer en parallèle après R2, mais
R5 exige la validation de toutes les étapes précédentes. Toute mise en réel reste
soumise à une autorisation explicite de Seb ; demander une roadmap ne l’autorise pas.

| Étape | Résultat à obtenir | État actuel |
|---|---|---|
| R0 | Une version candidate unique, intégrée et reproductible | Portage de sécurité intégré ; environnement qualifié et première CI réussie ; vérifier la CI de chaque candidat |
| R1 | Isolation PAPER et accès SQL fermés | Isolation PAPER corrigée et testée ; droits SQL cible fermés et vérifiés le 10 novembre |
| R2 | Exécution, risque et simulations fiables | Correctifs et contrats locaux livrés ; matrice complète multi-processus à qualifier |
| R3 | Déploiement PAPER supervisé et récupérable | Schéma cible en 0013 ; sauvegarde complète, services et exploitation à qualifier |
| R4 | Politique économique qualifiée et taille d’ordre finançable | Bloqué : preuve hors calibration insuffisante |
| R5 | Recette PAPER de la version candidate sur la cible | À faire après R0–R4 |
| R6 | Canary réel explicitement autorisé et réconcilié | Non autorisé à ce stade |
| R7 | Exploitation réelle stabilisée, décision finale | Non commencée |

Responsabilité technique : préparation du code, migrations, tests, déploiement
PAPER et dossier de preuves. Responsabilité de Seb : validation du mandat de risque,
compte/budget, accès opérateur et décisions d’activation ou de poursuite. Les secrets
se configurent dans leur stockage prévu, jamais dans le chat ou le dépôt.

Chaque étape a un dossier daté avec commit, configuration expurgée, versions des
dépendances, schéma/checksums, commandes, résultats et anomalies. Elle ne devient
validée qu’après exécution des preuves. Une dérogation éventuelle doit être écrite
avant la mesure concernée ; aucun seuil n’est abaissé automatiquement après échec.

## R0 — Réunir et figer une seule version candidate

**Travail**

- Inventorier les correctifs non intégrés du checkout `work` et les porter
  sélectivement sur le master courant, avec conservation du capital et des dix briefs.
- Résoudre le conflit entre `0010_execution_safety.sql` local et
  `0010_capital_ledger.sql` publié. Ne pas écraser une migration appliquée : créer
  les migrations suivantes selon l’ordre réel de la base cible.
- Figer les dépendances d’exécution et le modèle/prompt/configuration retenus.
- Installer une CI obligatoire : Python, Node, PostgreSQL 17 neuf et migrations,
  golden sans bless automatique, contrôle navigateur et secrets.

**Passage** : clone neuf installé avec les versions figées ; mêmes résultats en CI
et local ; migrations depuis zéro et depuis une copie du schéma cible ; aucun
correctif nécessaire uniquement présent dans un checkout non publié. Tag ou commit
candidat unique. Les tests existants constituent une base, pas un quota suffisant.

**R1 : code du premier lot vérifié le 9 novembre 2026** (698 Python, 31 Node).
Isolation du kill-switch et migration 0012 livrées ; durcissement appliqué sur
Supabase le 10 novembre après autorisation explicite. Droits et tests négatifs
validés sur la cible ; preuve dans DEPLOIEMENT_SUPABASE_2026-11-10.md.

## R1 — Fermer immédiatement les deux frontières de sécurité

**Travail**

- Rendre toute commande PAPER strictement locale : aucun create/cancel/withdraw
  exchange, y compris kill-switch, panic, réconciliation et scripts administratifs.
- Séparer les commandes : suspendre les achats, arrêter avec protections maintenues,
  liquider avec confirmation des fills. Une erreur ne doit pas retirer un stop
  sans traitement explicite de l’exposition restante.
- Révoquer EXECUTE hérité de PUBLIC sur les fonctions privilégiées, notamment
  `ensure_partitions(integer)` ; réserver la maintenance au rôle prévu, borner le
  paramètre, corriger les privilèges par défaut. Nouvelle migration uniquement.
- Vérifier tables, partitions, vues et fonctions : droits minimaux, RLS, vues
  `security_invoker` et absence de secrets exposés au client.

**Passage** : tests négatifs de tous les points d’entrée PAPER avec doubles de
mutation qui lèvent ; anon/authenticated refusés sur les fonctions privilégiées ;
advisors de sécurité relus et constats traités ou justifiés précisément. Validation
sur base jetable puis application contrôlée du durcissement sur cible. Aucun
ordre réel nécessaire pour cette preuve. Ferme A02 et A09 de l’audit.

## R2 — Qualifier les contrats financiers et la fidélité des outils

**Travail**

1. **Compte courant unique** : relire le solde exchange avant l’approbation finale,
   conserver réserves/cagnotte et recalculer valorisation/drawdowns cohérents.
   L’évaluation persistée est exactement celle envoyée ; aucune remontée de taille
   fondée sur un ancien snapshot SQL. Ferme A01.
2. **Fraîcheur** : préserver heure source, réception et décision ; vérifier ticker,
   dernière clôture attendue et continuité des séries. Donnée inconnue ou trop
   ancienne = refus d’entrée explicite. Ferme A08.
3. **Intentions durables** : persister identifiant stable et intention avant envoi ;
   gérer UNKNOWN par réconciliation, sans renvoi aveugle après timeout/redémarrage.
4. **Fills partiels** : cumuls dédupliqués, frais, quantité restante et ledger exacts ;
   après annulation, relire l’état final et protéger uniquement le reliquat confirmé.
   Aucun reliquat non protégé laissé sans traitement bloquant. Ferme A03/D01.
5. **Coordination commune** : moteur, panic, kill, reprise et rapprochement partagent
   un protocole de sérialisation. Définir le point d’autorisation d’un achat face
   à une suspension ; bloquer la reprise avec intention inconnue ou écart non résolu.
6. **Stops** : persister le nouvel ID après remplacement, vérifier sa couverture,
   réutiliser cet ID pour un second déplacement et vérifier les annulations ambiguës.
7. **Sonde de validation** : distinguer rejet confirmé et résultat inconnu. Un timeout
   ne vaut jamais succès ; aucune sonde ne doit envoyer un ordre réel en prétendant
   être une validation sans exécution. Ferme A07.
8. **Recherche/diagnostics** : conserver la raison des HALT dans le ledger mémoire,
   revalider l’âge des coûts au choix, compter les fills des ordres partiellement
   exécutés puis annulés et vérifier les contrats mémoire/SQL. Ferme A04/A05/A06/A10.

**Passage** : les dix contre-exemples de l’audit deviennent des tests imposant le
comportement corrigé. Ajouter une matrice de pannes : avant/après envoi, réponse
perdue, fill partiel, annulation ambiguë, arrêt brutal, commit échoué, perte du
verrou et concurrence moteur/urgence. Aucun double ordre ou débit, quantité ou
protection inventée ; UNKNOWN reste bloquant et récupérable. Suite complète,
SQL multi-connexions, golden et navigateur réussis, avec revue du diff financier.

## R3 — Préparer la cible et l’exploitation

**Travail**

- Sauvegarder la base et tester sa restauration sur une base séparée. Rejouer les
  migrations manquantes et nouvelles sur cette copie avant application cible.
- Déployer la même version pour moteur, API, dashboard et surveillant ; identités
  et configuration appliquée visibles. Réconcilier les positions historiques.
- Installer les services avec redémarrage contrôlé. Avant chaque reprise, rapprocher
  les intentions ; ne pas relancer une mutation parce qu’un processus redémarre.
- Installer un surveillant indépendant : clé lecture seule distincte, contrôle des
  soldes/stops, HALT sans annulation, webhook testé et contrôle externe de sa vie.
- Exposer le dashboard uniquement via accès privé/TLS ; protéger sessions et TOTP.
  Préparer les clés dédiées de trading et d’urgence, sans retraits et avec restrictions
  IP applicables ; vérifier le comportement de nonce avec plusieurs processus.
- Mesurer quota/rétention des enregistrements. Définir sauvegarde, export, archivage
  et suppression contrôlée ; conserver les preuves financières non résolues.
- Écrire les procédures de stop absent, résultat inconnu, base/Ollama/réseau en panne,
  rotation de clé et retour à une version compatible du schéma.

**Passage** : checksums cible conformes et restauration réussie ; accès privés et
TOTP exercés ; redémarrages et pannes testés. Cible d’alerte proposée à qualifier :
incident critique détecté et notifié en **60 secondes maximum**, avec horodatages
source/réception. Surveillant ou moteur indisponible = état inconnu/alerte, jamais vert.
Le silence d’un webhook ne vaut pas absence d’incident.

## R4 — Figer le mandat et qualifier la stratégie

**Mandat proposé à valider avant les mesures finales**

Spot long, une position maximum, profil réellement supporté en LIVE ; capital
progressif et sélection de classement restent PAPER tant qu’ils n’ont pas leur
qualification propre. Risque prévu maximal proposé : **0,5 % de l’équité active par
trade, frais/glissement compris** ; limites journalière/hebdomadaire/depuis le plus
haut et budget de perte absolu sont fixés dans le dossier, sans seuil laissé implicite.
Aucune hausse automatique du capital, du risque ou de la fréquence.

Le budget de perte totale déjà documenté est **9,37 EUR**. Calculer la faisabilité
avec les minima/arrondis/frais Kraken actuels et le risque retenu. Si aucun ordre
sûr n’est finançable, le passage réel reste bloqué : aucun assouplissement automatique
pour fabriquer un canary. Tout changement de budget ou mandat relève d’une décision
explicite de Seb et impose une nouvelle vérification de faisabilité.

**Mesure**

- Figer stratégie, modèle/poids, prompt, leçons et hypothèses avant d’ouvrir la
  période finale hors calibration. Séparer clairement apprentissage et validation.
- Rejouer la politique entière, avec enveloppes, refus, frais, spread, glissement,
  minima et dates. Couvrir hausse, baisse et marché latéral ; comparer cash et
  buy & hold sur le même univers et les mêmes dates.
- Obtenir **au moins 100 trades clos simulés** sur le protocole préenregistré,
  avec résultats de la période hors calibration séparés. Ne pas additionner des
  variantes corrélées pour gonfler artificiellement l’effectif.
- Exiger espérance nette positive et drawdown maximal inférieur au plafond déclaré,
  y compris sous coûts défavorables documentés. Examiner l’incertitude par blocs
  temporels et la concentration des gains ; un intervalle compatible avec une
  perte importante maintient un verdict économique insuffisant.

**Passage** : rapport reproductible, données/configurations/versions archivées,
critères chiffrés atteints hors calibration et faisabilité du compte cible démontrée.
Les 100 trades sont un minimum de mesure, pas une garantie statistique. Zéro trade,
une courte fenêtre gagnante ou un golden identique ne suffisent pas. Toute retouche
après examen de la validation consomme cette période et exige une nouvelle validation.

## R5 — Recette PAPER sur la cible, version gelée

**Durée minimale proposée : 14 jours consécutifs ET 20 cycles programmés** ; la
cadence D1 peut donc imposer plus de 14 jours. Cette durée valide l’exploitation,
pas à elle seule les 100 trades ou la rentabilité de R4.

**Travail** : tourner avec données réelles, modèle prévu, code et règles candidats,
enveloppes propres et aucun ordre réel. Exercer suspension pendant inférence,
réautorisation TOTP, changement de réglages, redémarrage, indisponibilité réseau/SQL/
LLM, surveillance et export/rejeu. Les urgences restent testables sans argent réel.

**Passage** : tous les cycles attendus sont expliqués ; données/révisions effectives
visibles ; aucun incident financier ou de sécurité non résolu ; alertes reçues dans
le délai qualifié ; états rapprochables après panne. Les écarts de coût et les refus
sont présentés sans forcer les achats. Une correction touchant les contrats financiers
rouvre R2 et la recette concernée ; la fenêtre stable ne couvre pas un mélange de versions.

## R6 — Canary réel sous autorisation explicite

**Prérequis** : R0–R5 validées ; sous-compte dédié sans avoir manuel ; clé vérifiée,
restrictions et retraits bloqués ; budget/règles approuvés ; opérateur disponible et
procédure d’urgence prête. Kraken spot n’offre pas le testnet supposé par certains
anciens documents : ce canary engage bien de l’argent réel.

**Parcours** : autoriser explicitement les verrous live pour la version et le compte
retenus ; rapprochement propre ; effectuer **au moins trois allers-retours de taille
minimale compatible avec le mandat**, sans dépasser le budget global accepté.
Couvrir entrée avec stop visible, sortie normale, sortie par stop et récupération
après redémarrage d’un processus sous supervision. Ne jamais retirer la protection
pour fabriquer une panne ; les cas dangereux se qualifient par injection en R2.

**Passage** : pour chaque ordre, intention, ID exchange, quantité/frais/fills et
position du ledger concordent aux précisions du marché ; aucune exécution inconnue,
position non suivie ou protection absente non traitée. Les écarts de coût restent
dans les hypothèses qualifiées. Aucun critère ne peut être remplacé par « pas d’erreur
apparente ». Seb valide explicitement la poursuite.

## R7 — Stabilisation réelle et go live définitif

**Durée minimale proposée : 30 jours consécutifs** au budget canary approuvé et
**10 trades clos réels minimum**. Si la stratégie trade peu, prolonger la période ;
ne pas augmenter la fréquence pour atteindre un compteur. Cet effectif valide
principalement l’exécution et les coûts, pas une nouvelle preuve de rentabilité.

Revue quotidienne initiale : rapprochement compte/ledger, stops, intentions,
limites de risque, réserve, frais, logs et alertes. Aucune promotion de stratégie
ou augmentation de budget pendant cette qualification.

**Go live définitif** seulement si :

- R0–R6 sont prouvées sur la version exploitée et leurs preuves sont archivées ;
- aucun incident critique ni résultat UNKNOWN ne reste ouvert ;
- rapprochement quotidien sans écart inexpliqué ; respect du mandat et du budget ;
- coûts observés compatibles avec le protocole économique ; aucun signal nouveau
  invalidant cette qualification, sinon retour à R4 ;
- sauvegarde/restauration, alertes et procédures d’urgence ont été exercées ;
- Seb approuve la fiche finale : commit, schéma, compte, budget, plafonds, procédures,
  responsable des alertes et date de revue suivante.

Le go live définitif signifie autorisation d’exploitation durable **dans ce mandat
et ce budget**. Toute augmentation de capital ou changement stratégique est une
nouvelle décision, avec analyse d’impact et requalification proportionnée.

## Arrêt, récupération et changements de version

Résultat d’ordre inconnu, quantité divergente, données périmées, limite franchie
ou perte de surveillance : interdire les nouvelles entrées, conserver les stops,
rapprocher les faits et notifier. Une exposition non protégée relève de la procédure
d’urgence qualifiée ; ne pas attendre la prochaine clôture H4/D1. Une perte de
connexion ne doit jamais déclencher une vente ou annulation aveugle.

Pas de reprise automatique d’un incident financier non résolu. Retour logiciel
seulement après vérification de compatibilité du schéma et des intentions en vol ;
aucune restauration aveugle de base pendant qu’un compte exchange évolue.

**Planification** : R0–R3 sont un chantier logiciel/infrastructure estimable après
inventaire du portage. R4 dépend de l’historique et du résultat ; R5/R7 comportent
des durées minimales et R6 dépend des occasions finançables. Il n’existe pas de date
honnête de go live définitif tant que la stratégie et le budget n’ont pas passé R4.
Les durées et effectifs proposés ici constituent le protocole par défaut à valider
avant activation, et non des preuves déjà obtenues.
