# Diversification PAPER — observation du 16 novembre 2026

Étude de candidates, sans prévision de rendement. Les marchés et le code ont été
contrôlés ; la configuration du service installé ne change qu'après déploiement.

## Pourquoi aucun achat aujourd'hui ?

À 10:32 CET, le moteur systemd a terminé un cycle sur BTC, ETH, ADA, DOGE,
PAXG, HBAR et BNB contre EUR. Les sept prix étaient sous EMA200 H4 + 0,5 %.
Le filtre déterministe s'arrête avant le LLM : zéro signal n'indique donc pas
une panne du modèle. Le heartbeat était courant et le modèle chargé.
Prochaine clôture annoncée : 12:00 UTC, soit 13:00 CET ; ensuite toutes les H4.
Ce rendez-vous est une évaluation, sans garantie d'achat.

Lecture SQL du même matin : PAPER IDLE, entrées autorisées, aucune position,
révision 7 `micro_budget` appliquée. Dernier solde enregistré : 9,37 EUR.
Risque par achat : 3 %, soit 0,2811 EUR ; exposition : 50 %.
Ces observations sont datées et ne remplacent pas le statut courant.

Le filtre exige également une pente EMA200 d'au moins 2 ATR sur dix jours,
une structure EMA50 haussière, un repli et un RSI compatible, puis un BUY du
modèle et l'approbation du pare-feu. Un rebond quotidien ne suffit pas.
Les comparatifs de 24 mois dans `BACKTEST.md` ne démontrent pas de rentabilité
aux coûts de 80 pb par côté. Réduire la période macro n'a pas résolu ce problème.

## Cinq candidates supplémentaires

Choix de recherche : SOL, LINK, AAVE, SUI, NEAR. Ce sont des possibilités
supplémentaires pour chercher des entrées ; ce n'est pas une allocation à parts
égales. Les actifs crypto restent corrélés, même si leurs usages diffèrent.

| Paire | Intérêt à étudier | Vigilance | Variation 7 j EUR* | Minimum d'achat observé | Finançable à 9,37 EUR** |
|---|---|---|---:|---:|---|
| LINK/EUR | Oracles et interopérabilité | L'usage du réseau ne garantit pas la hausse du jeton | −9,8 % | 2,91 EUR | Oui |
| AAVE/EUR | Prêts et emprunts DeFi | Risques de protocole, liquidations et gouvernance | −12,2 % | 2,98 EUR | Oui |
| SOL/EUR | Applications, paiements et liquidité | Forte sensibilité au cycle spéculatif | −14,9 % | 3,24 EUR | Non, minimum d'équité calculé 10,71 EUR |
| SUI/EUR | Applications Move et infrastructure | Dilution et concurrence entre réseaux | −9,0 % | 3,29 EUR | Non, minimum d'équité calculé 11,20 EUR |
| NEAR/EUR | Infrastructure et exécution entre réseaux | Adoption et conversion de l'usage en demande de jetons à qualifier | −9,1 % | 2,90 EUR | Non, minimum d'équité calculé 10,84 EUR |

\* Calcul sur les clôtures D1 Kraken en EUR, dernière journée clôturée, pas un
rendement glissant de cotation en USD. Les pourcentages web peuvent donc différer.
\** Calcul pur `affordability.assess()` du dépôt avec `micro_budget`, stop ATR,
frais de 0,8 % et glissement de 5 pb par côté. Financement seulement : toutes
ces candidates échouent actuellement au filtre de tendance. Les montants requis
sont des diagnostics, pas une recommandation d'ajouter du capital.

Les treize paires supplémentaires contrôlées (dont HYPE, TAO, AVAX, UNI, AKT,
QNT, XRP et ZEC) étaient sous leur EMA200 avec marge. LINK et AAVE présentent
ici une faisabilité financière supérieure ; cela ne prouve aucun avantage économique.

Pour la demande de cryptos « qui montent », KITE ressortait à environ +24,5 %
sur sept jours sur CoinGecko pendant la consultation. Mais aucun marché KITE
n'était présent dans `ccxt.kraken.load_markets()` : ce n'est pas une candidate
compatible avec ce bot aujourd'hui. Les plus fortes hausses de petites valeurs
ne constituent pas à elles seules un critère de sélection.

Sources consultées le 16 novembre 2026 :

- [Chainlink : projet](https://chain.link/) et [marché](https://www.coingecko.com/en/coins/chainlink).
- [Aave : projet](https://aave.com/) et [marché](https://www.coingecko.com/en/coins/aave).
- [Solana : projet](https://solana.com/) et [marché](https://www.coingecko.com/en/coins/solana).
- [Sui : projet](https://www.sui.io/) et [marché](https://www.coingecko.com/en/coins/sui).
- [NEAR : projet](https://www.near.org/) et [marché](https://www.coingecko.com/en/coins/near).
- [KITE : marché](https://www.coingecko.com/en/coins/kite-ai),
  [indisponibilité Kraken](https://www.kraken.com/be/prices/kite).
- [Kraken OHLC](https://api.kraken.com/0/public/OHLC),
  [marchés](https://api.kraken.com/0/public/AssetPairs) et
  [tickers](https://api.kraken.com/0/public/Ticker), lus sans clé via CCXT.

Les descriptions des projets indiquent leur positionnement, pas une validation
indépendante de leurs promesses. Mesures structurées :
`audits/2026-11-16/crypto-screen.json` et `crypto-affordability.json`.

## Positions multiples

Le moteur doit surveiller chaque position avant de chercher un nouvel achat.
Un cycle peut fermer plusieurs positions et ouvrir au plus une nouvelle ligne.
Chaque paire garde sa politique, son stop, son TP et son curseur de surveillance.
Les entrées sont réévaluées avec le cash réellement restant et tous les symboles
ouverts ; aucune paire ne peut avoir deux positions ouvertes dans un même mode.

`MAX_OPEN_POSITIONS` est réglable de 1 à 5, uniquement en PAPER au-delà de 1.
Le défaut historique reste 1 ; la préparation de déploiement demande 5.
En mode multiple, les plafonds de risque et d'exposition sont partagés par le
portefeuille : le risque prévu cumulé ne dépasse pas le pourcentage du profil
appliqué au capital actif courant. Ce plafond inclut frais et glissement prévus,
mais les gaps et pannes peuvent toujours produire une perte supérieure.
L'étranglement après pertes réduit également ce plafond. L'admission utilise une
valorisation fraîche du portefeuille et les drawdowns recalculés en base ; le
prix du candidat est rafraîchi après les lectures des positions existantes.

Avec le profil courant, cinq places ne signifient donc pas cinq fois 0,2811 EUR
risqués. Une première position peut consommer le budget disponible et empêcher
les suivantes. À 9,37 EUR, les minima Kraken et la volatilité peuvent même
empêcher toute coexistence : le bot ne relève aucun plafond pour la financer.

Après une clôture, le cooldown bloque les achats et laisse surveiller les autres
positions. En arrêt PAPER, les stops/TP continuent d'être traités. Une panne de
cours sur une paire suspend les achats sans empêcher une autre sortie connue.
Une valorisation manquante est signalée et utilise un repli conservateur au plus
bas de l'entrée et du stop, sans être présentée comme un cours courant.

Les snapshots de portefeuille multiple ne portent pas un symbole individuel :
un diagnostic ne doit pas attribuer tout le portefeuille à une seule position.
Le dashboard affiche chaque ligne ; le champ API historique `position` reste
la première ligne pour compatibilité, tandis que `positions` expose la liste entière.
