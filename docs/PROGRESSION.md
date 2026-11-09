# Progression à petit budget — D-069

Objectif : préserver le capital et rechercher une progression modeste, nette de
frais, sur plusieurs mois. Aucun objectif de gain quotidien, aucun nombre minimal
d'ordres. Rester en EUR est normal mais ne prouve aucune rentabilité. Le profil
`progressif` reste exclusivement une expérience papier.

## Règles initiales

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

## Capital protégé et petits paliers — D-070

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

### Mise en service papier

Appliquer la nouvelle migration avec le coureur du dépôt sur la base choisie
avant de lancer le nouveau moteur. La migration déployée n'a pas été appliquée
pendant ce travail : seuls deux PostgreSQL locaux jetables ont servi aux tests.

Les comptes papier existants démarrent le contrôleur **à plat** : l'équité et la
réserve déjà constatées deviennent la nouvelle référence, sans redistribuer
les gains historiques. Une position papier ancienne sans curseur doit d'abord
être liquidée virtuellement via le panic-sell / kill-switch, après vérification
du mode PAPER, puis le bot doit être repris manuellement. Un capital déjà géré
par paliers ne peut pas être désactivé par un simple changement de profil.
Apports, retraits et changement de devise ne sont pas automatisés : ils exigent
une réconciliation comptable explicite ; changer `PAPER_START_EQUITY` ne recharge
pas un compte existant.

## Utilisation

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
