# Profils PAPER pour petits budgets

Sélection manuelle dans Réglages ou `RISK_PROFILE`. Aucun changement automatique
après perte, minimum refusé ou baisse de capital. Les tranches sont indicatives.

| Profil | Repère EUR | Risque prévu par achat | Exposition maximale | Arrêts jour / semaine / depuis le plus haut |
|---|---|---:|---:|---|
| `petit_budget` | 20–100 | 1,5 % | 50 % | 4 % / 8 % / 15 % |
| `micro_budget` | 8–20 | 3 % | 50 % | 6 % / 10 % / 20 % |
| `nano_budget` | Moins de 8 | 5 % | 100 % sous 50 EUR ; 50 % au-delà | 10 % / 15 % / 20 % |

Le nano concentre potentiellement presque tout le compte sur une position. Gap,
panne ou liquidité insuffisante peuvent dépasser la perte prévue au stop.
Réduire son exposition dans le dashboard réduit effectivement la taille permise.

Communs : une position, stop à 2,5 ATR, objectif fixe à 3,5 fois la distance au
stop, frais/glissement dans le risque, filtre gain net/perte prévue à 1,5,
confiance minimale 0,60, règle d'entrée maintenue, arrêt après deux pertes
consécutives et trois ordres maximum par heure. Les drawdowns déclenchent après
observation ; ce ne sont pas des garanties de perte maximale exacte. La confiance
n'est pas une probabilité calibrée. L'objectif 3,5 R reste une hypothèse à qualifier.

Ces profils sont réservés au PAPER pendant leur qualification. L'ancien `micro`
est conservé pour compatibilité. Changer de profil réinitialise les anciennes
surcharges runtime de risque/exposition/limites ; les changements explicites de
la même demande restent prioritaires. Le délai de reprise HALT est conservé.

## Mesures de financement

`audits/2026-11-11/budget-profiles.json` contient 162 combinaisons : trois paires,
trois profils, neuf budgets et deux niveaux de coûts. Source publique figée le
10 novembre à 15:24 UTC ; ce ne sont pas des cotations actuelles.

- À 25 EUR, petit budget finance BTC/ETH/SOL aux deux coûts.
- À 9,37 EUR, micro budget finance les trois paires, à 0,80 % + 5 pb comme
  à 1 % + 10 pb par côté. Risque prévu maximal par achat : 0,2811 EUR.
- À 5 EUR, nano finance les trois paires aux coûts de base. Sous stress, SOL
  est refusé au plafond de risque de 5 % ; BTC et ETH restent finançables.
- À 3 EUR, 1 EUR et 0,50 EUR, les trois paires sont refusées. Aucun capital
  supplémentaire ni quantité sous les minima n'est inventé.

Un financement favorable ne garantit pas un achat : tendance, fraîcheur, signal,
spread, verrous et position restent contrôlés. Les rejeux associés utilisent un
BUY mécanique, pas Ollama : aucune performance LLM ni validation indépendante.

Sur le rejeu 2026 conservé, micro à 9,37 EUR termine à 8,9212 EUR en base
(8,8671 sous stress), nano à 5 EUR à 4,5494 EUR (4,5169 sous stress).
Chacun effectue deux trades perdants puis s'arrête au coupe-circuit. Ces profils
rendent des ordres possibles ; ce résultat défavorable ne permet pas leur promotion.
