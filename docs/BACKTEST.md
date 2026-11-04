# Backtest — mesurer les décisions, sans argent

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
(le modèle le voit à zéro), et Kraken ne rend que 30 jours de H1 — au-delà, la
fenêtre n'est plus fidèle.

## Résultats du premier rejeu

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
(min 0,62), alors que le profil `micro` exige **0,85**. En production, aucun achat du
LLM ne peut donc passer. Le pare-feu fait son travail, mais le couple
modèle/seuil est incohérent.

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
