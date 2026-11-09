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
qu'aux bougies suivantes dans le rejeu H4. La même fonction calcule le niveau
pour le moteur et le rejeu, mais la reprise du moteur présente une limite connue :
si le processus s'interrompt après la persistance du stop et avant celle du signal
LLM, le cycle suivant peut tester le nouveau stop sur la bougie qui l'a produit et
clôturer rétroactivement la position papier. Défaut reproduit lors de la revue du
2026-11-09, encore à corriger ; la protection contre ce rejeu n'est donc pas
atomique au redémarrage.

Le LLM conserve sa capacité de vendre ou d'arrêter ; son score minimal de 0,60
n'est pas une probabilité de réussite. Deux pertes consécutives divisent le risque
par deux. La décision est revalidée au prix utilisé pour le fill papier.

Avec 9,37 EUR, le budget prévu est environ 4,7 centimes par trade. Les minimums
Kraken peuvent empêcher **tous** les achats. Le profil accepte ce refus sans
passer automatiquement en `micro`. Cela protège le budget mais ne prouve pas
qu'une stratégie sera exploitable à cette taille.

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
