# Backtest — mesurer les décisions, sans argent

Le profil papier `progressif`, le comparatif hors ligne des petits budgets et ses
limites sont décrits dans [Capital progressif](OBSERVATION_ET_RECHERCHE.md#capital-progressif-paper). Le rejeu H4 valorise
désormais chaque clôture pendant la détention, coûts de liquidation compris.
`replay` utilise la configuration versionnée `backtest_settings.json` lorsqu'elle
est présente à côté du golden ; les décisions et métriques du golden restent intactes.

## Pourquoi

Le moteur papier exécute une décision toutes les quatre heures sur des données
réelles. C'est utile pour vérifier la plomberie, pas pour juger le modèle : un mois
de marché ne donne que quelques dizaines de décisions, et aucune preuve.

Le backtest rejoue une fenêtre historique **sans lookahead** : à chaque instant T,
le modèle ne voit que les bougies clôturées à T. Il réutilise le vrai code :

| Élément | Rôle |
|---|---|
| `app/market/feed.py` | indicateurs sur bougies clôturées |
| `app/llm/client.py` | prompt et validation de la sortie |
| `app/engine/risk_gate.py` | `evaluate()` : dimensionnement et refus |
| `app/execution/paper.py` | fills simulés (slippage, frais, stop prioritaire) |

Aucun ordre, aucune écriture en base, aucun argent.

## Commandes

```bash
cd backend

# 1. Enregistrer un golden : interroge le vrai modèle (~2 min pour 45 décisions)
.venv/bin/python scripts/backtest.py record

# 2. Rejouer les décisions enregistrées : déterministe, hors ligne (< 1 s)
.venv/bin/python scripts/backtest.py replay

# 3. Scénario : mesurer un autre seuil de confiance, sans toucher à .env
.venv/bin/python scripts/backtest.py replay --min-confidence 0.70

# 4. Veille : ne parle qu'en cas de dérive (prompt, modèle, risque)
.venv/bin/python scripts/backtest.py watch

# 5. Après avoir relu le diff : figer le nouveau comportement
.venv/bin/python scripts/backtest.py replay --bless

# 6. Mesure multi-paires de la politique règle + LLM (réseau + LLM, pas de golden)
.venv/bin/python scripts/backtest.py measure
.venv/bin/python scripts/backtest.py measure --symbols BTC/EUR,ETH/EUR --max-candles 80

# 7. Laboratoire de sortie : rejouer le golden avec une autre géométrie, sans le modifier
.venv/bin/python scripts/backtest.py scenario --stop-atr 3.0 --take-profit-r 2.0
.venv/bin/python scripts/backtest.py scenario --require-positive-h1

# 8. A/B de la mémoire de leçons : même fenêtre, prompt avec ou sans leçons
.venv/bin/python scripts/backtest.py measure --lessons-file /tmp/lessons.txt
.venv/bin/python scripts/backtest.py measure

# 9. Historique multi-années : archives publiques Kraken → PostgreSQL (jalon 1)
.venv/bin/python scripts/ingest_history.py --months 36 --dry-run
.venv/bin/python scripts/ingest_history.py --months 36
.venv/bin/python scripts/ingest_history.py --report

# 10. Balayage statistique du filtre de pente, 24 mois, sans LLM (jalon 2)
.venv/bin/python scripts/backtest.py sweep --months 24
.venv/bin/python scripts/backtest.py sweep --months 12 --slopes 0,2.0 --fee-bps 80 --json
```

## Comment le rejeu fonctionne

1. Kraken fournit les 720 dernières bougies H4 (120 jours) et H1 (30 jours). La
   fenêtre évaluable commence quand l'EMA200 et 120 H1 existent, soit ~25 jours.
2. À chaque bougie H4, `build_snapshot` ne reçoit que les bougies clôturées à cet
   instant. Aucune donnée future ne peut entrer dans le prompt.
3. Le modèle est interrogé **uniquement dans la zone où un achat est possible** :
   prix au-dessus de l'EMA200 + marge, RSI < 70, volatilité dans les bornes.
   Ailleurs, un BUY serait refusé par les garde-fous de toute façon.
4. Une intention BUY passe par `evaluate()`. Si elle est refusée, aucun trade.
5. Si elle est approuvée, `PaperBroker` remplit l'entrée (slippage + frais), puis la
   première bougie suivante qui touche le stop ou le TP ferme la position. Si les
   deux sont touchés, le stop gagne. Une position encore ouverte en fin de fenêtre
   est soldée au dernier cours.
6. À chaque HOLD, le backtest calcule aussi le **contrefactuel** : que se serait-il
   passé si on avait acheté quand même (confiance 1.0, mêmes règles) ? Cela sépare
   « le modèle a évité un mauvais trade » de « le modèle a raté un bon trade ».

Deux contreparties à connaître : le carnet d'ordres n'existe pas dans l'historique
(le modèle le voit à zéro), et l'API REST de Kraken ne rend que 720 bougies (30 jours
de H1, 120 jours de H4). Le balayage `sweep`, lui, lit `market_candles_history`
(migration 0008) : la fenêtre peut alors couvrir plusieurs années — voir
« Historique multi-années » plus bas.

## Historique multi-années (jalon 1)

L'API REST plafonne à 720 bougies et ignore un `since` plus ancien. Les archives
OHLCVT publiques de Kraken remontent à la première transaction de chaque marché.
Une archive trimestrielle pèse ~575 Mo et l'archive complète 9,5 Go, mais
`scripts/ingest_history.py` ne lit que les membres utiles :

1. `app/market/kraken_archive.py` lit le répertoire central du ZIP par requête
   HTTP Range (gestion ZIP64), puis chaque membre par un second Range.
2. `app/market/history.py` analyse le CSV (`ts,open,high,low,close,volume,trades`),
   détecte les trous, agrège H1 → H4 et compare l'agrégation au H4 publié.
3. Les lignes partent dans `market_candles_history` par lots de 5 000, avec
   `ON CONFLICT DO NOTHING` : l'ingestion est rejouable sans doublon.

Mesuré le 2026-11-04 (`--source full`) :

| paire | H1 | depuis | H4 | trous H4 | manquantes H1 |
|---|---|---|---|---|---|
| BTC/EUR | 113 899 | 2013-09-10 | 28 637 | 62 | 1,18 % |
| ETH/EUR | 97 407 | 2015-08-07 | 24 597 | 37 | 1,18 % |
| SOL/EUR | 47 041 | 2021-06-17 | 11 793 | 3 | 0,30 % |

Cohérence H1 → H4 : **aucune divergence** de prix sur les trois paires. Les trous
restants sont concentrés en 2013-2014 (marché illiquide) et sur trois coupures
communes aux trois paires (2024-01-20, 2024-04-14, 2025-11-01), plus la soudure
01→05/10/2026 entre la fin d'archive et la fenêtre REST de 30 jours.

Ce dernier trou n'est **pas** interpolé : fabriquer une bougie créerait un prix qui
n'a jamais existé, susceptible de déclencher un stop simulé. Il est signalé par
`--report` et par `v_history_coverage`. Un `--rest-only` quotidien l'empêche de
réapparaître.

## Balayage 24 mois (jalon 2)

`backtest.py sweep` remplace le LLM par une réponse BUY constante : la règle
d'entrée, `evaluate()`, le sizing et `PaperBroker` restent ceux de la production.
Le but n'est pas de juger le modèle mais le **filtre**, sur un échantillon long.

Fenêtre 2024-11-04 → 2026-11-04, équité virtuelle 1 000 EUR, 4 378 bougies H4 par
paire, frais 80 pb/côté (taker réel) et 100 pb (stress), sorties stop/TP seulement.
Les **coupe-circuits permanents sont levés** (`--max-consecutive-losses 9999
--max-hwm-drawdown 1.0`, D-059) : sans cela la mesure s'arrête au premier mauvais mois
et `N` ne décrit plus la fenêtre.

Deux modes d'entrée (D-058) : `EMA50_H4` (setup historique, repli sur l'EMA50 H4) et
`PULLBACK_H1` (repli fin : le bas de la dernière H1 touche l'EMA50 H1, la clôture
referme au-dessus, extension ≤ 1 ATR H1, RSI H1 ≤ 55). Mêmes garde-fous, même sizing.

| paire | mode | pente | trades | réussite | PF | espérance | net | DD max |
|---|---|---|---|---|---|---|---|---|
| BTC/EUR | EMA50_H4 | 0 | 39 | 25,6 % | 0,28 | −7,91 | −308,47 | 31,2 % |
| BTC/EUR | EMA50_H4 | 1,0 | 32 | 25,0 % | 0,24 | −8,47 | −271,16 | 27,1 % |
| BTC/EUR | EMA50_H4 | 1,5 | 28 | 28,6 % | 0,29 | −7,82 | −219,06 | 21,9 % |
| BTC/EUR | EMA50_H4 | 2,0 | 21 | 28,6 % | 0,27 | −8,75 | −183,80 | 18,4 % |
| BTC/EUR | PULLBACK_H1 | 0 | 36 | 27,8 % | 0,30 | −8,43 | −303,30 | 30,6 % |
| BTC/EUR | PULLBACK_H1 | 1,0 | 27 | 29,6 % | 0,32 | −8,34 | −225,12 | 22,5 % |
| BTC/EUR | PULLBACK_H1 | 1,5 | 20 | 35,0 % | 0,46 | −6,59 | −131,78 | 13,2 % |
| BTC/EUR | PULLBACK_H1 | 2,0 | 18 | 33,3 % | 0,43 | −6,99 | −125,81 | 14,1 % |
| ETH/EUR | EMA50_H4 | 0 | 29 | 37,9 % | 0,55 | −5,27 | −152,96 | 15,7 % |
| ETH/EUR | EMA50_H4 | 1,0 | 26 | 38,5 % | 0,51 | −5,97 | −155,26 | 15,9 % |
| ETH/EUR | EMA50_H4 | 1,5 | 23 | 34,8 % | 0,50 | −6,11 | −140,46 | 14,4 % |
| ETH/EUR | EMA50_H4 | 2,0 | 18 | 44,4 % | 0,62 | −4,43 | −79,78 | 9,3 % |
| ETH/EUR | PULLBACK_H1 | 0 | 28 | 35,7 % | 0,45 | −6,64 | −185,94 | 21,3 % |
| ETH/EUR | PULLBACK_H1 | 1,0 | 23 | 34,8 % | 0,45 | −6,70 | −154,20 | 17,6 % |
| ETH/EUR | PULLBACK_H1 | 1,5 | 20 | 40,0 % | 0,50 | −6,33 | −126,56 | 14,9 % |
| ETH/EUR | PULLBACK_H1 | 2,0 | 17 | 35,3 % | 0,42 | −7,83 | −133,15 | 13,6 % |
| SOL/EUR | EMA50_H4 | 0 | 27 | 25,9 % | 0,45 | −6,70 | −180,89 | 18,1 % |
| SOL/EUR | EMA50_H4 | 1,0 | 20 | 20,0 % | 0,28 | −9,07 | −181,50 | 18,1 % |
| SOL/EUR | EMA50_H4 | 1,5 | 17 | 23,5 % | 0,31 | −8,89 | −151,14 | 15,1 % |
| SOL/EUR | EMA50_H4 | 2,0 | 14 | 21,4 % | 0,20 | −11,27 | −157,81 | 15,8 % |
| SOL/EUR | PULLBACK_H1 | 0 | 23 | 21,7 % | 0,34 | −8,38 | −192,67 | 20,0 % |
| SOL/EUR | PULLBACK_H1 | 1,0 | 19 | 21,1 % | 0,26 | −10,08 | −191,45 | 19,1 % |
| SOL/EUR | PULLBACK_H1 | 1,5 | 18 | 22,2 % | 0,33 | −9,06 | −163,02 | 16,3 % |
| SOL/EUR | PULLBACK_H1 | 2,0 | 14 | 14,3 % | 0,12 | −12,36 | −173,11 | 17,3 % |

À 100 pb/côté, chaque scénario perd 8 à 40 EUR de plus : aucun ne devient positif.
Référence buy & hold sur la même fenêtre : BTC +11,67 %, ETH −7,48 %, SOL −43,37 %.

Ce que cela dit, sans détour :

- **48 scénarios, aucun positif.** PF de 0,11 à 0,62, espérance toujours négative,
  drawdown de 9 à 31 %. Aucun ne s'approche des seuils du brief (N ≥ 100, PF ≥ 1,30,
  espérance > 0, DD ≤ 15 %).
- **Le repli H1 ne crée pas d'arête** : il améliore le PF de BTC à pente égale
  (0,46 contre 0,29 à 1,5) et réduit son drawdown (13–14 % contre 18–31 %) parce qu'il
  trade moins, mais il fait moins bien sur ETH (0,50 contre 0,62) et SOL (0,34 contre
  0,45). Déplacer le point d'entrée ne change pas le signe.
- **N plafonne à 39 par paire et par 24 mois**, même à pente 0 et coupe-circuits
  levés. En cumulant les trois paires, la pente 0 totalise 95 trades (H4) et 87 (H1) —
  mesures indépendantes, chacune repartant de 1 000 EUR, pas un portefeuille partagé.
  Le seuil de 100 n'est donc atteignable qu'en cumulant, jamais par paire.
  La cadence H4 ne voit qu'une opportunité H1 sur quatre ; c'est là que se joue le
  prochain élargissement, pas dans un troisième filtre.
- **Le verrou expliquait une partie des anciens chiffres.** Avec `max_consecutive_losses`
  à 4, BTC en pente 2,0 s'arrête après 9 trades (dernier le 2025-08-02) ; levé, il en
  produit 21 jusqu'au 2026-10-15. Les « 2 à 9 trades par paire et par an » mesurés
  précédemment étaient donc moitié rareté du setup, moitié gel de la mesure.
- **Le prochain levier n'est ni les frais ni l'exécution** : une stratégie qui perd
  avant frais ne se sauve pas par un ordre Maker. C'est la cadence de décision (H1) ou
  la couverture multi-paires qu'il faut mesurer ensuite.

## Le LLM peut vendre et arrêter

Depuis D-043, le modèle ne se contente plus de proposer un achat. À chaque bougie H4
clôturée pendant une position, il est interrogé avec `has_position=true` :

- `HOLD` : la position continue, le stop et le take-profit restent les protections ;
- `SELL` : la position est soldée à la clôture simulée (slippage et frais), avec
  `exit_reason = LLM_SELL` ;
- `HALT` : la position est soldée puis le rejeu s'arrête, avec
  `exit_reason = LLM_HALT`.

Le stop et le take-profit sont testés **avant** l'appel au modèle : ils vivent dans la
bougie, le modèle ne voit que sa clôture. Un SELL ne peut pas créer de short, et le
modèle ne propose jamais de taille ni de prix.

La commande `measure` rejoue cette politique sur plusieurs paires. **Chaque paire
repart de la même équité** : ce n'est pas un portefeuille partagé, c'est une mesure de
comportement paire par paire.

## Résultats du premier rejeu (prompt v1, avant D-043)

BTC/EUR · 2026-10-10 → 2026-11-04 · `qwen3.5:9b` · profil `micro` · 150 bougies.

| Mesure | Valeur |
|---|---|
| décisions dans la zone | 45 (27 BUY, 18 HOLD) |
| appels approuvés par le pare-feu | **0** |
| rejets | `CONFIDENCE_TOO_LOW` 27, `ACTION_IS_NOT_BUY` 18 |
| trades | 0 |
| PnL | 0,00 EUR · équité 9,37 → 9,37 |
| buy & hold sur la fenêtre | −6,15 % |
| contrefactuel « acheter à chaque HOLD/BUY refusé » | **−5,94 EUR** |
| latence médiane | 1,25 s |

**Constat principal** : le modèle ne dépasse jamais **0,72** de confiance sur un BUY
(min 0,62), alors que le profil `micro` exigeait **0,85** à l'époque. En production,
aucun achat du LLM ne pouvait donc passer. Le pare-feu fait son travail, mais le couple
modèle/seuil est incohérent. *(D-043 a depuis ramené le plancher à 0,60 ; le modèle
continue pourtant de répondre HOLD — voir la mesure 7 paires ci-dessous.)*

**Scénario à 0,70** (mêmes décisions enregistrées, seuil abaissé) :

| Mesure | Valeur |
|---|---|
| trades | 2, tous perdants |
| PnL | −0,25 EUR (−2,68 %) |
| refus de dimensionnement | 9 (`SIZING_BELOW_EXCHANGE_MINIMUM`) |
| buy & hold | −6,15 % |

Sur cette fenêtre baissière, le modèle perd moins qu'un achat aveugle, mais ne gagne
pas. Neuf BUY supplémentaires sont refusés parce que la taille calculée passe sous le
minimum Kraken : avec 9,37 EUR et un stop à 2,5 ATR, le dimensionnement est à la
frontière sur BTC/EUR.

**Interprétation** : aucun edge démontré. Ce n'est pas une preuve que le modèle est
mauvais — un mois, une paire, un régime baissier — mais c'est suffisant pour ne pas
baisser le seuil sans nouvelle mesure. Les prochaines pistes sont le calibrage du
prompt (demander une confiance mieux répartie), l'élargissement à d'autres paires et
une histoire plus profonde.

## Mesure après D-043 — règle + LLM sur 7 paires (prompt v3)

BTC, ETH, ADA, DOGE, PAXG, HBAR, BNB · 2026-10-10 → 2026-11-04 · `qwen3.5:9b` ·
profil `micro` · 150 bougies H4 · capital indépendant par paire.

Le prompt v3 ajoute une section « scénario d'achat » qui décrit explicitement le
pullback EMA50, et le payload marché porte `ema50_above_ema200`,
`distance_to_ema200_pct`, `distance_to_ema50_atr` et `above_ema200_with_buffer`.
Le modèle est passé de 0 BUY (prompt v2) à 33 BUY sur les sept paires.

| Paire | décisions | BUY | HOLD | trades | PnL | buy & hold |
|---|---:|---:|---:|---:|---:|---:|
| BTC/EUR | 44 | 22 | 22 | 3 | −0,4811 | −6,03 % |
| ETH/EUR | 1 | 0 | 1 | 0 | 0,00 | −7,85 % |
| ADA/EUR | 11 | 7 | 4 | 0 | 0,00 | −25,06 % |
| DOGE/EUR | 0 | 0 | 0 | 0 | 0,00 | −13,14 % |
| PAXG/EUR | 0 | 0 | 0 | 0 | 0,00 | −3,96 % |
| HBAR/EUR | 3 | 0 | 3 | 0 | 0,00 | −23,43 % |
| BNB/EUR | 7 | 4 | 3 | 1 | −0,2140 | −6,98 % |
| **total** | **66** | **33** | **33** | **4** | **−0,6951** | — |

*Frais Kraken au tarif réel du compte : 0,80 % taker par côté, 1,60 % par
aller-retour. Les chiffres précédents (−0,4999 EUR) utilisaient le tarif périmé de
0,26 % exposé par CCXT ; ils ne sont plus la référence.*

**Constat principal** : le modèle achète désormais, mais les quatre trades approuvés
ont tous fini au stop-loss — **0 gagnant sur 4**, aucune sortie SELL ou HALT. Le PnL
cumulé est −0,6951 EUR (−7,4 % du compte de 9,37 EUR), meilleur que le buy & hold sur
BTC (−5,13 % vs −6,03 %) et sur BNB (−2,28 % vs −6,98 %), mais toujours négatif. Sur
ADA, les 7 BUY ont été refusés par `SIZING_BELOW_EXCHANGE_MINIMUM` : le budget de
risque (1,75 % = 0,164 EUR) divisé par un stop de 2,5 ATR donne un notionnel inférieur
au minimum Kraken de 3,37 EUR. Ce n'est pas la règle d'entrée qui bloque, c'est
l'arithmétique du petit capital.

**Interprétation** : quatre trades ne prouvent rien, mais le signal est clair — le
prochain levier est la géométrie entrée/sortie, pas le seuil de confiance ni le nombre
de paires. Le stop à 2,5 ATR est peut-être trop serré pour le bruit H4, ou la règle
achète trop tôt dans une tendance baissière. À tester sur le même golden, en rejeu
déterministe : `stop_atr_multiple` 2,5 → 3,5 et `take_profit_r_multiple` 2,0 → 3,0.
Ne pas forcer le modèle à acheter davantage : il achète déjà, et les quatre essais
perdent.

## Mesure après D-051 — pente EMA200 sur 7 paires

Même fenêtre, même modèle, avec le filtre de pente EMA200 (2,0 ATR sur 10 jours) :

| Paire | décisions | trades | PnL | buy & hold |
|---|---:|---:|---:|---:|
| BTC/EUR | 19 | 2 | −0,3268 | −6,03 % |
| ETH/EUR | 0 | 0 | 0,00 | −7,85 % |
| ADA/EUR | 10 | 0 | 0,00 | −25,06 % |
| DOGE/EUR | 0 | 0 | 0,00 | −13,14 % |
| PAXG/EUR | 0 | 0 | 0,00 | −3,96 % |
| HBAR/EUR | 3 | 0 | 0,00 | −23,43 % |
| BNB/EUR | 2 | 0 | 0,00 | −6,98 % |
| **total** | **34** | **2** | **−0,3268** | — |

Le filtre supprime les deux trades perdants de BNB et coupe un trade BTC : 4 → 2 trades,
−0,6951 → −0,3268 EUR. Il ne rend toujours pas la stratégie positive, et il assèche les
alts. C'est une protection de régime, pas une arête.

## Scénarios de sortie sur le golden (décisions figées)

La commande `scenario` rejoue les décisions enregistrées en changeant uniquement la
géométrie de sortie. Elle ne modifie rien et n'appelle pas le LLM. Si une décision
manque parce que la position vit plus longtemps que dans l'enregistrement, elle est
remplacée par un HOLD de scénario — le nombre de décisions manquantes est affiché.

| stop | take-profit | trades | PnL | retour | note |
|---:|---:|---:|---:|---:|---|
| 2,5 ATR | 2 R | 3 | −0,4811 | −5,13 % | golden de référence |
| 3,0 ATR | 2 R | 2 | −0,3603 | −3,85 % | 3 décisions manquantes ; un achat devient sous le minimum |
| 3,5 ATR | 2 R | 2 | −0,3937 | −4,20 % | 3 décisions manquantes |
| 2,5 ATR | 3 R | 3 | −0,4811 | −5,13 % | TP jamais atteint, identique au baseline |
| 3,5 ATR | 3 R | 2 | −0,3937 | −4,20 % | TP jamais atteint |
| 4,0 ATR | 3 R | 2 | −0,3823 | −4,08 % | 13 décisions manquantes, peu fiable |
| 2,5 ATR + filtre H1 positif | 2 R | 2 | −0,3226 | −3,44 % | 0 décision manquante ; évite un perdant |

Un seuil de confiance à **0,90** supprime tous les trades : 0 trade, 0,00 EUR. Ce n'est
pas une stratégie positive, c'est l'arrêt. À 0,85, les 3 trades perdent encore −5,15 %.
Aucun réglage de sortie ou de confiance ne crée l'arête qui manque.

## Filtre de pente EMA200 (D-051)

La règle refuse désormais un pullback si l'EMA200 H4 ne monte pas d'au moins
**2,0 ATR sur 10 jours** (60 bougies). Mesure sur le golden BTC, décisions figées :

| pente minimale | décisions | trades | PnL | retour |
|---:|---:|---:|---:|---:|
| 0 (ancien comportement) | 44 | 3 | −0,4811 | −5,13 % |
| 1,0 ATR | 21 | 2 | −0,3268 | −3,49 % |
| 2,0 ATR (défaut) | 19 | 2 | −0,3268 | −3,49 % |
| 3,0 ATR | 9 | 1 | −0,1391 | −1,48 % |
| 5,0 ATR | 6 | 1 | −0,1391 | −1,48 % |

Le filtre **réduit la perte de 32 %** et empêche d'acheter un trend qui s'essouffle. Il ne
rend pas la stratégie positive : deux trades, c'est un échantillon, pas une preuve. Le
seuil 3,0 ATR est plus sélectif mais encore moins concluant en nombre de trades. Aucun
réglage testé ne produit un PnL positif **avec des trades**.

## Mandat short — laboratoire (D-052)

Kraken spot ne permet pas de vendre à découvert ; le laboratoire `scripts/short_lab.py`
mesure donc le mandat short **sans ordre**, sur le même golden BTC et avec les mêmes
frais (0,80 %/côté) et le même slippage :

| variante | trades | gagnants | PnL | retour |
|---|---:|---:|---:|---:|
| short miroir, stop 2,5 ATR / TP 1,5 R | 1 | 1 | +0,0871 | +0,93 % |
| short miroir + H1 négative | 1 | 1 | +0,0871 | +0,93 % |
| short, stop 3,0 ATR / TP 2 R | 1 | 1 | +0,1858 | +1,98 % |
| short, distance −2..0 ATR | 2 | 0 | −0,2725 | −2,91 % |

Le short **retourne le signe** sur cette fenêtre baissière (BTC −6,03 %), mais avec **un
seul trade**, entré le 29 octobre. Ce n'est pas une preuve : c'est le signe qu'un mandat
short mérite d'être mesuré sur plusieurs régimes, pas qu'il est rentable. Le short réel
exige un autre lieu d'exécution (marge spot ou Kraken Futures) et des garde-fous de
liquidation ; il n'est pas câblé.

**Constat** : aucun scénario ne rend la stratégie positive. Les quatre trades de
référence sortent tous au stop ; le take-profit à 2R ou 3R n'est jamais touché. Le
« meilleur » scénario à 3,0 ATR perd moins, mais parce qu'un troisième achat devient
impossible : le stop plus large réduit le notionnel sous le minimum Kraken. C'est un
effet de sélection, pas un edge. Le filtre « dernière H1 positive » évite un perdant
et améliore le PnL à −2,39 %, mais sur deux trades seulement.

**Décision** : ne pas changer le stop ou le TP en production sur cette base. Trois
trades ne permettent pas de distinguer un edge d'un hasard. Le prochain travail utile
est d'accumuler des échantillons (boucle papier, `shadow_trades`) et de mesurer sur
une fenêtre plus longue ou un autre régime, avant de figer un filtre. Le laboratoire
`scenario` reste disponible pour rejouer une hypothèse sans toucher au direct.

## Mémoire de leçons — premier A/B

Même fenêtre BTC/EUR, 30 bougies H4, politique règle + LLM, avec et sans deux leçons
génériques (« les achats dans un EMA200 descendant perdent », « un stop trop serré
coupe le rebond ») :

| scénario | décisions | BUY | trades | PnL | retour | B&H |
|---|---:|---:|---:|---:|---:|---:|
| sans leçons | 19 | 11 | 2 | −0,3268 | −3,49 % | −2,63 % |
| avec leçons | 21 | 14 | 2 | −0,3229 | −3,45 % | −2,63 % |

**Constat** : l'écart est de +0,0039 EUR, dans le bruit ; les deux scénarios perdent,
avec 0 gagnant. Les leçons n'ont pas empêché les achats perdants. L'échantillon est
trop petit (2 trades) pour conclure quoi que ce soit sur l'utilité de la mémoire.

**Décision** : le mécanisme est prêt, pas la preuve. Attendre au moins 5 résultats
réels, lancer la relecture, puis refaire l'A/B sur la fenêtre complète. Ne pas
présenter la mémoire comme un gain tant que la mesure ne le montre pas.

## Swing macro Daily — mesure multi-années (2026-11-04)

Commande :

```bash
cd backend && .venv/bin/python scripts/backtest.py swing \
  --symbols BTC/EUR,ETH/EUR,SOL/EUR --months 60 --fee-bps 80 \
  --swing-triggers both,donchian,value \
  --max-consecutive-losses 9999 --max-hwm-drawdown 1.0
```

Le D1 est agrégé depuis le H4 en base (6 bougies complètes par jour) ; une journée
incomplète est ignorée. Le chemin est celui de la production : règle d'entrée →
`evaluate()` → `PaperBroker`, stop structurel et breakeven inclus.

### 2021-11 → 2026-11 (60 mois)

| paire | déclencheur | trades | N/an | réussite | payoff | PF | net | DD max |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| BTC | both | 31 | 7,2 | 9,7 % | 4,06 | 0,44 | −126,94 | 12,69 % |
| BTC | donchian | 9 | 2,1 | 33,3 % | 1,80 | 0,90 | −10,44 | 5,27 % |
| BTC | value | 28 | 6,5 | 10,7 % | 4,02 | 0,48 | −106,13 | 10,95 % |
| ETH | both | 19 | 4,4 | 5,3 % | 10,34 | 0,57 | −55,45 | 9,04 % |
| ETH | donchian | 5 | 1,2 | 20,0 % | 8,45 | 2,11 | +68,86 | 3,49 % |
| ETH | value | 18 | 4,2 | 5,6 % | 9,78 | 0,58 | −55,35 | 9,03 % |
| SOL | both | 13 | 3,0 | 7,7 % | 2,25 | 0,19 | −55,46 | 5,60 % |
| SOL | donchian | 7 | 1,6 | 0,0 % | n/a | 0,00 | −14,01 | 1,40 % |
| SOL | value | 8 | 1,9 | 12,5 % | 1,16 | 0,17 | −63,74 | 6,37 % |

**Aucun scénario positif sur la fenêtre demandée.** ETH Donchian est le seul positif,
sur 5 trades : hypothèse, pas résultat.

### Tout l'historique

| paire | déclencheur | trades | N/an | réussite | payoff | PF | net | DD max |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| BTC | both | 87 | 7,1 | 17,2 % | 10,56 | 2,20 | +148,9 % | 13,94 % |
| BTC | donchian | 37 | 3,0 | 35,1 % | 7,80 | 4,23 | +199,9 % | 7,64 % |
| BTC | value | 71 | 5,8 | 14,1 % | 9,80 | 1,61 | +42,2 % | 11,89 % |
| ETH | both | 74 | 7,1 | 12,2 % | 7,94 | 1,10 | +6,1 % | 15,34 % |
| ETH | donchian | 34 | 3,3 | 17,6 % | 7,06 | 1,51 | +16,2 % | 15,55 % |
| ETH | value | 54 | 5,2 | 9,3 % | 16,39 | 1,67 | +36,5 % | 10,39 % |

**Lecture** : le trend-following D1 fonctionne sur les cycles 2014–2021 (BTC Donchian
PF 4,23) mais perd sur les cinq dernières années. La réussite de 40 % n'est jamais
atteinte : le système gagne rarement, gros. Durée moyenne 9,4 j (BTC) / 5,8 j (ETH),
dans la cible 3–15 j. Ce n'est pas une arête actuelle, et ce n'est pas activé.

## Mean Reversion Bollinger — mesure 2021-2026 (2026-11-05)

Hypothèse opposée au trend-following : acheter la peur. Entrée à la clôture sous la
bande de Bollinger basse (20 ; 2), RSI(14) < 32 et volume de la bougie supérieur à la
moyenne des 20 volumes précédents ; sortie au contact de la bande médiane (`touch`) ou
à sa clôture (`close`), avec stop structurel d'urgence sous le creux des 10 bougies.
Le pare-feu reste l'autorité : chaque intention passe par `evaluate()`.

Deux variantes sont mesurées :

- **`gated`** : la configuration exécutable, filtre « prix au-dessus de l'EMA200 H4 »
  compris ;
- **`raw`** : le même filtre neutralisé (`RiskConfig.allow_counter_trend`, laboratoire
  uniquement, jamais lu depuis l'environnement) pour mesurer l'hypothèse elle-même.
  **Cette variante n'est pas activable en production.**

Commande :

```bash
cd backend && .venv/bin/python scripts/backtest.py meanrev --months 60 \
  --symbols BTC/EUR,ETH/EUR,SOL/EUR --timeframes 4h,1d \
  --mr-rsi 30,32,35 --mr-exit touch,close --mr-variants gated,raw \
  --fee-bps 80,100
```

### Scénario principal demandé (RSI 32, sortie `touch`, 80 pb)

| paire | tf | trades | N/an | réussite | PF | net | DD max | B&H |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| BTC | H4 | 4 | 0,8 | 0,0 % | 0,00 | −53,25 | 5,32 % | +67,63 % |
| ETH | H4 | 4 | 0,8 | 0,0 % | 0,00 | −54,00 | 5,40 % | −41,18 % |
| SOL | H4 | 6 | 1,2 | 50,0 % | 1,28 | +15,53 | 3,12 % | −48,68 % |
| BTC | D1 | 2 | 0,5 | 0,0 % | 0,00 | −46,48 | 4,65 % | +211,60 % |
| ETH | D1 | 1 | 0,2 | 100,0 % | n/a | +47,31 | 0,00 % | +29,52 % |
| SOL | D1 | 0 | 0,0 | — | n/a | 0,00 | 0,00 % | +106,46 % |

### Les 144 scénarios

| mesure | résultat |
|---|---|
| scénarios (3 paires × 2 tf × 3 RSI × 2 sorties × 2 variantes × 2 frais) | 144 |
| net positif | 18 |
| PF ≥ 1,30 | 5 — tous avec **2 à 6 trades** |
| PF maximum, variante `gated` | 1,95 (SOL H4, RSI 30, `close`, 80 pb, N = 2) |
| PF maximum, variante `raw` | 0,85 |
| net cumulé, variante `gated` | −1 730 EUR |
| net cumulé, variante `raw` | −4 804 EUR |
| trades médians par scénario | 3 (`gated`) / 4 (`raw`) |

**Verdict : hypothèse rejetée.** Les rares scénarios PF ≥ 1,30 tiennent sur 2 à 6
trades et ne sont pas interprétables ; la variante `raw`, qui mesure la règle sans le
filtre de tendance, plafonne à PF 0,85 et perd 4 804 EUR cumulés. L'arête n'existe pas
hors tendance haussière, et la cadence (0,2 à 1,2 trade/an par paire) reste le plafond.
Aucun seuil n'est figé sur ces échantillons, aucun filtre supplémentaire n'est empilé,
et le mode n'est pas activé. Le seuil d'activation live (PF ≥ 1,30 **et** N ≥ 100 sur
2021-2026) reste inchangé et non atteint. D-064.

## Sensibilité aux frais maker (25 pb vs 80 pb) — 2026-11-05

`--maker-fees` est une **sensibilité au coût**, pas un modèle d'exécution maker : le
fill reste la clôture + slippage, et les 25 pb remplacent seulement les 80 pb de frais.
Aucun ordre limite, aucune file d'attente, aucun statut maker n'est simulé.

```bash
cd backend && .venv/bin/python scripts/backtest.py swing --months 60 \
  --symbols BTC/EUR,ETH/EUR,SOL/EUR --fee-bps 80 --maker-fees 25 \
  --swing-triggers both,donchian,value --exit-mode ema20,chandelier \
  --max-consecutive-losses 9999 --max-hwm-drawdown 1.0

cd backend && .venv/bin/python scripts/backtest.py meanrev --months 60 \
  --symbols BTC/EUR,ETH/EUR,SOL/EUR --timeframes 4h,1d \
  --mr-rsi 30,32,35 --mr-exit touch,close --mr-variants gated,raw \
  --fee-bps 80 --maker-fees 25
```

### Swing D1 — 36 scénarios

| frais | scénarios PF ≥ 1 | PF ≥ 1,30 | PF max | net cumulé |
|---|---:|---:|---:|---:|
| 80 pb | 3 | 3 | 2,31 | −665,68 EUR |
| 25 pb | 8 | 3 | 2,92 | **−64,98 EUR** |

| scénario | N | PF 80 | net 80 | PF 25 | net 25 |
|---|---:|---:|---:|---:|---:|
| BTC Donchian Chandelier | 7 | 2,31 | +86,46 | 2,92 | +107,89 |
| ETH Donchian EMA20 | 5 | 2,11 | +68,86 | 2,60 | +83,59 |
| BTC both Chandelier | 23 | 0,77 | −48,08 | 1,00 | +0,18 |
| BTC both EMA20 | 31 | 0,44 | −126,94 | 0,63 | −61,86 |
| ETH both EMA20 | 19 | 0,57 | −55,45 | 0,78 | −21,89 |
| SOL both EMA20 | 13 | 0,19 | −55,46 | 0,53 | −21,36 |

Les frais maker améliorent chaque ligne, mais le net cumulé reste **négatif** et les
seuls PF ≥ 1,30 demeurent des poches Donchian de 5 à 7 trades. Aucun scénario large
n'atteint le seuil.

### Mean Reversion — 144 scénarios

| frais | nets positifs | PF ≥ 1,30 | PF max | N médian |
|---|---:|---:|---:|---:|
| 80 pb | 10 | 3 | 1,95 | 4 |
| 25 pb | 13 | 7 | 2,85 | 4 |

Meilleures lignes à 25 pb : SOL H4 RSI 32 `close` N = 6 PF 2,61 (+64,26 EUR) ;
SOL H4 RSI 32 `touch` N = 6 PF 2,26 (+49,80) ; SOL H4 RSI 30 `close` N = 2 PF 2,85
(+34,34) ; SOL H4 RSI 35 `touch` N = 15 PF 1,36 (+41,35) ; ETH D1 `raw` RSI 32
`close` N = 15 PF 1,29 (+41,44).

Scénario principal demandé (RSI 32, `touch`) à 25 pb : BTC H4 4 trades −32,06 EUR ;
ETH H4 4 trades −32,83 ; SOL H4 6 trades PF 2,26 +49,80 ; BTC D1 2 trades −35,87 ;
ETH D1 1 trade +53,12 ; SOL D1 0 trade. Aucune poche ne dépasse 15 trades avec
PF ≥ 1,30.

**Verdict** : les frais ne sont pas l'arête manquante. Même à 25 pb, le trend-following
D1 reste négatif en cumulé et la mean reversion ne produit que des poches à petit N.
Le seuil d'activation (PF ≥ 1,30 **et** N ≥ 100) reste non atteint. D-065.

## Rotation momentum multi-paires — laboratoire (2026-11-05, étendu D-067)

Hypothèse : ne pas choisir un actif, mais **le meilleur des trois à chaque instant**,
avec une porte de sortie vers le cash. Univers `BTC/EUR`, `ETH/EUR`, `SOL/EUR`,
`CASH/EUR`. Toutes les 7 ou 14 bougies D1, on calcule pour chaque crypto le momentum
moyen 30 j / 60 j, on retient le score le plus élevé, puis on applique un **filtre
absolu** (`--abs-filter`) :

| mode | condition pour garder le leader | sinon |
|---|---|---|
| `none` | aucune (relatif pur) | — |
| `ema200` | clôture > EMA200 D1 | 100 % CASH |
| `roc60` | ROC 60 D1 > 0 | 100 % CASH |
| `dual` | clôture > EMA200 D1 **et** ROC 60 D1 > 0 | 100 % CASH |

Le leader est toujours choisi avant le filtre : si le premier échoue, le laboratoire
va en cash, il ne se rabat pas sur le deuxième. Les frais sont comptés par transaction :
CASH → CASH = 0, crypto → CASH = 1 côté, CASH → crypto = 1 côté, crypto → crypto =
2 côtés. Aucun stop, aucun levier, aucun ordre réel : `app/backtest/rotation.py` est
pur et `scripts/lab_rotation.py` ne fait que charger les D1 depuis la base.

```bash
cd backend && .venv/bin/python scripts/lab_rotation.py --months 60 \
  --fees-bps 25,80 --cadences 7,14 --abs-filter dual --summary-table
# rejouer de même avec --abs-filter none, ema200, roc60
```

Fenêtre commune mesurée : 1 762 bougies D1 (2021-11 → 2026-11). Buy & hold sur la
même fenêtre : BTC +69,7 %, ETH −39,6 %, SOL −44,4 %.

| filtre | cadence | frais | équité finale | retour | DD max | rotations | N/an | temps cash | frais payés | final |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| none | 7 j | 25 pb | 987,80 | −1,22 % | 77,41 % | 54 | 11,2 | 0,0 % | 259,07 | BTC |
| none | 14 j | 25 pb | 737,64 | −26,24 % | 79,68 % | 48 | 9,9 | 0,0 % | 196,99 | BTC |
| none | 7 j | 80 pb | 545,37 | −45,46 % | 80,17 % | 54 | 11,2 | 0,0 % | 590,20 | BTC |
| none | 14 j | 80 pb | 435,04 | −56,50 % | 83,56 % | 48 | 9,9 | 0,0 % | 468,54 | BTC |
| ema200 | 7 j | 25 pb | 1 789,61 | +78,96 % | 62,18 % | 49 | 10,1 | 49,7 % | 349,20 | BTC |
| ema200 | 14 j | 25 pb | 1 951,44 | +95,14 % | 63,18 % | 38 | 7,9 | 50,1 % | 363,41 | BTC |
| ema200 | 7 j | 80 pb | 1 204,40 | +20,44 % | 68,88 % | 49 | 10,1 | 49,7 % | 901,13 | BTC |
| ema200 | 14 j | 80 pb | 1 418,44 | +41,84 % | 70,49 % | 38 | 7,9 | 50,1 % | 982,78 | BTC |
| roc60 | 7 j | 25 pb | 1 130,04 | +13,00 % | 71,51 % | 60 | 12,4 | 39,3 % | 241,00 | BTC |
| roc60 | 14 j | 25 pb | 936,92 | −6,31 % | 70,21 % | 43 | 8,9 | 39,7 % | 166,16 | BTC |
| roc60 | 7 j | 80 pb | 711,94 | −28,81 % | 74,51 % | 60 | 12,4 | 39,3 % | 599,18 | BTC |
| roc60 | 14 j | 80 pb | 651,70 | −34,83 % | 72,65 % | 43 | 8,9 | 39,7 % | 437,41 | BTC |
| dual | 7 j | 25 pb | 1 859,95 | +86,00 % | 64,55 % | 47 | 9,7 | 56,4 % | 331,11 | BTC |
| dual | 14 j | 25 pb | 2 255,58 | +125,56 % | 54,46 % | 32 | 6,6 | 56,4 % | 317,26 | BTC |
| dual | 7 j | 80 pb | 1 293,74 | +29,37 % | 68,44 % | 47 | 9,7 | 56,4 % | 870,15 | BTC |
| dual | 14 j | 80 pb | 1 713,26 | +71,33 % | 60,20 % | 32 | 6,6 | 56,4 % | 874,64 | BTC |

**Lecture** :

- Le relatif pur (`none`) est perdant et garde 77 à 84 % de drawdown : il reste
  investi dans l'actif « le moins baissier » pendant les purges, exactement le défaut
  identifié dans le brief.
- Les filtres absolus redressent le signe, mais **aucun scénario ne passe sous 25 % de
  drawdown**. Le meilleur est `dual` 14 j / 25 pb : +125,56 % (au-dessus du buy & hold
  BTC) mais 54,46 % de DD. `dual` 7 j / 25 pb : +86,00 %, 64,55 % de DD.
- Le filtre fait bien son travail en 2022 : `dual` 14 j / 25 pb passe **96,1 % de
  l'année en cash** (14 jours investis, 347 en cash) et bloque le bear market. Le
  drawdown maximal vient de **2024** : pic à 4 674,34 EUR le 2024-04-01, creux à
  2 128,82 EUR le 2024-08-06, soit −54,46 %, avec seulement 19,2 % de temps en cash
  cette année-là. La porte EMA200 hebdomadaire sort trop tard pendant la chute
  SOL/ETH.
- Les frais taker (80 pb) divisent environ le gain par deux ; `dual` 14 j / 80 pb
  reste à +71,33 % mais son DD monte à 60,20 %.
- **Changement de sémantique** : les chiffres D-066 (+163,74 % à 7 j/25 pb, +238,13 %
  à 14 j/25 pb) provenaient du filtre historique « EMA200 **et** score > 0 ». Le mode
  `none` est désormais le relatif pur demandé par le brief et ne reproduit pas ces
  chiffres ; `ema200` ne teste que l'EMA, sans la condition de score. Le tableau
  ci-dessus est la nouvelle référence.

**Verdict** : le dual momentum réduit fortement le drawdown par rapport au relatif pur
et redevient rentable, mais il reste très au-dessus de la cible de 25 %. Il n'est pas
branché sur le runtime. Le prochain levier est une sortie de risque plus rapide
(contrôle quotidien du titre détenu, stop ou ciblage de volatilité), pas un filtre
d'entrée supplémentaire. D-067.

## Tests récurrents

Le backtest tourne à trois moments, avec des coûts différents :

| Déclencheur | Quoi | Coût |
|---|---|---|
| `git commit` touchant `backend/app/`, `settings`, le prompt ou le backtest | golden rejoué (décisions figées) | < 1 s |
| `bash scripts/check.sh` | pytest, dont les tests de régression | < 1 s |
| lundi 8 h (job Hermes `backtest-drift`) | canari : 12 décisions témoins, uniquement si le prompt est inchangé ; rejeu complet sinon | ~15 s (ou ~2 min) |

Le planificateur exige un script sous `~/.hermes/scripts/` : le wrapper
`trading-ninja-backtest-watch.sh` appelle `scripts/backtest_watch.sh` du dépôt.

Le golden contient les bougies, les décisions et les métriques attendues. Deux
empreintes le protègent :

- **prompt** (modèle + version + texte du prompt) : si elle change, les décisions
  enregistrées ne sont plus représentatives → `record` ;
- **risque** (profil, frais, capital de départ) : si elle change, l'impact est mesuré
  en rejouant les mêmes décisions → `replay --min-confidence …`, puis `--bless` si le
  nouvel équilibre est voulu.

`--bless` ne bénit jamais tout seul : il réécrit les métriques attendues, et le diff
du fichier golden doit être relu et commité avec le changement.

**Le modèle n'est pas reproductible d'un processus à l'autre.** À température 0, une
décision isolée peut basculer sur une quasi-égalité (observé : HOLD 0,85 dans un run,
BUY 0,72 dans un autre, sur le même instantané). La régression teste donc les
**décisions enregistrées**, et la veille tolère 10 % de bascules ; en dessous de 90 %
d'accord, ce n'est plus du bruit.

## Limites

- Une paire, un mois, un régime : aucune conclusion statistique.
- H1 limité à 30 jours par Kraken (720 bougies).
- Pas de carnet d'ordres historique.
- Le prix d'entrée est la clôture H4 + slippage ; en réel, le prix serait rafraîchi
  juste avant l'ordre.
- Le scénario de seuil est dépendant du chemin : une position ouverte saute des
  décisions, donc les compteurs diffèrent de la fenêtre complète.
