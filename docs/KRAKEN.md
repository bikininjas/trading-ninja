# Kraken spot — spécification corrigée et état réel

Ce document remplace le brief « migration Binance → Kraken » tel qu'il a été fourni.
Il dit ce qui est vrai, ce qui est faux, ce qui est écrit, et ce qui reste.

---

## 1. Statut

**Il n'y a pas eu de migration.** Le brief visait `CCXTOrderExecutor`, `DataFeed` et
`app/core/config.py` : ces trois fichiers n'ont jamais existé dans ce dépôt. La phase 1
en est à sa deuxième étape ; aucun code ne parle encore à un exchange en dehors des
sondes de vérification. Ce qui a été fait est donc la **première** implémentation de la
couche Kraken, pas un remplacement.

Écrit le 2026-11-04 :

| Fichier | Rôle |
|---|---|
| `backend/app/exchange/kraken.py` | marchés → contraintes, formatage `Decimal`, bracket, permissions de clé |
| `backend/tests/test_kraken_adapter.py` | 23 tests, aucun réseau |
| `backend/scripts/check_kraken.py` | inspection lecture seule (marchés sans clé, permissions avec clé) |
| `backend/app/core/settings.py` | alias `KRAKEN_*`, frais par exchange, paires EUR |

**La clé Kraken présente dans `.env.local` est saine** (vérifié le 2026-11-04) :
`query-funds`, `query-open-trades`, `query-closed-trades`, `modify-trades`,
`close-trades`, `query-ledger`, `export-data`. Aucun droit de retrait ni de dépôt,
allowlist d'IP active sur une seule adresse. Le compte contient 9,37 EUR. Aucun ordre
n'a été envoyé.

---

## 2. Le brief face au dépôt

| Le brief dit | La réalité |
|---|---|
| « Remplacer l'intégration Binance » | Aucune intégration n'existe. La seule chose qui parle à Binance est `scripts/check_exchange.py`, une sonde lecture seule. |
| `app/core/config.py` | Le fichier s'appelle `app/core/settings.py`. |
| `CCXTOrderExecutor`, `DataFeed` | Inexistants. |
| « Dans `risk_gate.py` / `CCXTOrderExecutor` » | `risk_gate.py` est un module **pur**, sans I/O ni horloge. Y mettre un appel exchange détruirait la propriété qui rend le backtest, le papier et le réel identiques. |
| Vérifier les permissions « au boot » | Faisable, mais pas avec les libellés du brief : l'API renvoie des jetons kebab-case, pas les libellés de l'interface. |
| « Kraken ne propose pas de testnet Spot » | Exact. CCXT 4.5.91 n'expose aucune URL de test pour Kraken. |

Le dépôt `CryptoFacilities/REST-v3-Python` fourni en exemple est l'API **futures**
(ex-Crypto Facilities), dernière mise à jour 2021 : instruments `fi_xbtusd_…`,
`sendorder`, comptes sur marge. Rien ne s'y transpose au spot, sinon le principe de
signature (secret base64, HMAC-SHA512) que CCXT implémente déjà.

---

## 3. Faits mesurés

Mesures faites le 2026-11-04 avec `ccxt 4.5.91`, sur les marchés publics réels.

| Fait | Valeur |
|---|---|
| Symbole unifié | `BTC/EUR` → identifiant Kraken `XXBTZEUR` |
| Paires EUR actives | 491 |
| Mode de précision | `TICK_SIZE` (pas décimal fixe) |
| `BTC/EUR` | pas de quantité `1e-08`, tick prix `0,1`, min `0,00006 BTC`, min notionnel `0,45 EUR` |
| `ETH/EUR` | min `0,002 ETH`, tick `0,01`, min `0,45 EUR` |
| `SOL/EUR` | min `0,05 SOL`, tick `0,01`, min `0,45 EUR` |
| Frais taker/maker | 0,80 % / 0,40 % (palier 1, vérifié sur le compte le 2026-11-04) |
| Sandbox | aucune URL de test dans CCXT |
| `amount_to_precision` | **tronque** (`0.000123459` → `0.00012345`) |
| `price_to_precision` | **arrondit au plus proche** (`78123.45` → `78123.5`) |
| Permissions | `GetApiKeyInfo` existe, accessible sans permission préalable |

---

## 4. Corrections obligatoires du brief

### 4.1 `stopPrice` n'existe pas dans CCXT pour Kraken

Le brief propose :

```python
type="stop-loss", params={"stopPrice": safe_sl_price}
```

Requête réellement construite par CCXT 4.5.91 avec ce code :

```
{'pair': 'XXBTZEUR', 'type': 'sell', 'ordertype': 'stop-loss', 'volume': '0.001'}
reste={'stopPrice': 70000}
```

**Le prix n'est pas dans la requête.** L'ordre part en `stop-loss` sans prix : au mieux
Kraken le rejette, au pire la position reste sans protection. Le paramètre lu par CCXT
est `stopLossPrice` :

```
{'pair': 'XXBTZEUR', 'type': 'sell', 'ordertype': 'stop-loss',
 'volume': '0.001', 'price': '70000'}
```

Le bracket `close` du brief, lui, est correct :

```
{'pair': 'XXBTZEUR', 'type': 'buy', 'ordertype': 'market', 'volume': '0.001',
 'close': {'ordertype': 'stop-loss', 'price': '70000'}}
```

### 4.2 `float(...)` viole la règle du `Decimal`

Le brief convertit la sortie de précision en `float`. C'est exactement ce que D-007
interdit : un flottant binaire ne représente pas 0,1 exactement, et l'écart se retrouve
dans la comptabilité. L'adaptateur garde la chaîne rendue par CCXT (`"0.00012345"`) et
ne convertit en `Decimal` que pour comparer.

### 4.3 Les permissions ne se lisent pas avec les libellés de l'interface

L'API renvoie des jetons, pas les libellés du brief. Clé réellement inspectée :

```
query-funds, add-funds, withdraw-funds, earn-funds, query-open-trades,
query-closed-trades, modify-trades, close-trades, query-ledger, export-data
```

« Cancel/Close Orders » s'appelle `close-trades` dans le nouveau modèle. Un test qui
compare des sous-chaînes acceptait `query-closed-trades` comme droit d'annulation
(« closed » contient « close ») : corrigé par une comparaison de jetons exacts.

### 4.4 La marge de frais était calibrée pour Binance

`fee_buffer_pct` valait 0,2 %, soit un aller-retour à 0,10 % par côté — le tarif Binance.
Kraken prend **0,80 % par côté** (palier 1 depuis le 2026-07-09), donc **1,60 %** par
aller-retour. À 0,2 %, le dernier achat peut échouer pour quelques centimes de frais. La
valeur dépend maintenant de `EXCHANGE_ID` (`0.002` Binance, `0.016` Kraken), et reste
surchargeable.

Le tarif réel est lu par `fetch_trading_fee` quand la clé est présente : sur ce compte,
il renvoie `taker=0.008`, `maker=0.004`, volume 30 jours 0, prochain palier à 2 500 USD.
CCXT expose encore `market['taker'] = 0.26 %` : c'est un tarif historique, pas le palier
actuel. Les chemins de coût (paper, backtest, shadow, kill-switch, API) passent par
`resolve_taker_fee()` ; `market_info()` garde la valeur du marché comme défaut pour les
scans publics et les tests.

### 4.5 `adjustForTimeDifference` ne résout pas les nonces

Ce paramètre corrige l'horloge. Le problème réel de Kraken est ailleurs : le nonce doit
être strictement croissant **par clé**, et CCXT le gère par instance de processus. La
documentation de `incrementing_nonce()` le dit : « the counter is per exchange instance,
so it narrows the duplicate-nonce race but does not remove it across instances or
processes. »

Conséquence directe : le bot et le kill-switch (étape 1.11) ne peuvent pas partager la
même clé. Il faut deux clés, ou un seul processus signataire.

Le code lit désormais une paire **optionnelle** dédiée : `KILL_SWITCH_API_KEY` et
`KILL_SWITCH_SECRET`. `scripts/kill_switch.py` l'utilise en priorité ; si elle manque,
il retombe sur la clé du bot avec un avertissement explicite (collision de nonce
possible si le bot tourne). Une paire incomplète refuse le démarrage. La paire Kraken
elle-même reste à créer côté exchange.

### 4.6 Un seul `close` par ordre : pas de TP dans le bracket

Kraken accepte **une** protection conditionnelle attachée à l'entrée. Le take-profit
demande donc un second ordre, et l'annulation du frère quand l'un se déclenche. Le brief
parle d'« OCO » : ce n'est pas natif sur le spot Kraken. À écrire, pas à supposer.

### 4.7 Les WebSockets contredisent la règle des bougies clôturées

Le brief demande d'« écouter les WebSockets temps réel » en mode papier. Le projet
calcule sur bougies **clôturées** (H4/H1) précisément pour éviter le repaint : une bougie
en cours change et rend le comportement irreproductible. Le mode papier doit rejouer des
bougies clôturées, pas un flux temps réel. Les WebSockets resteront un confort
d'affichage, pas une source de décision.

### 4.8 L'envoi réel est dans `app/execution/live.py`, pas dans l'adaptateur

`kraken.py` prépare et formate ; `LiveBroker` est le seul module qui appelle
`create_order()`. Il envoie l'achat avec le bracket, attend le fill, relit le stop chez
Kraken et revend immédiatement si le stop manque. Le take-profit reste **local** tant
que l'OCO n'est pas écrit : un TP séparé sans annulation du frère peut vendre deux fois.
Le bot exige un **sous-compte dédié** ; sur un compte partagé, il ne peut pas distinguer
sa position d'un avoir manuel et refuse d'entrer.

---

## 5. Ce que fait l'adaptateur

- `market_info()` : marché CCXT → `ExchangeConstraints` en `Decimal` (pas, minimums, tick),
  plus les frais réels du palier.
- `to_exchange_amount()` : refuse un flottant, refuse un arrondi supérieur à la quantité
  validée.
- `to_exchange_stop_price()` : refuse un stop arrondi **plus bas** que le stop validé
  (une perte plus grande que le budget).
- `bracket_params()` / `build_bracket_entry()` : construisent l'achat marché avec
  `close.ordertype = stop-loss`. **Aucun envoi dans ce module.** La fonction exige une
  `RiskDecision` approuvée : l'appelant ne peut pas fournir une taille ou un stop libre.
  L'envoi est fait par `app/execution/live.py`.
- `verify_key_permissions()` : `GetApiKeyInfo`, retrait interdit, cinq droits exigés,
  réponse vide ou illisible = refus.

Ce qui n'existe pas encore, volontairement : l'envoi réel, l'annulation du TP frère,
l'application du plan de réconciliation (le constat et l'audit, eux, existent dans
`app/runtime/reconciliation.py` et `scripts/reconcile.py`), et le sweep réel.

À côté, `app/engine/affordability.py` (pur, testé) répond à la question qui manquait :
avec cette équité et cette volatilité, le plus petit ordre autorisé tient-il dans le
budget de risque, et sinon quelle équité faudrait-il ? Sans ce calcul, un compte trop
petit produit un refus à chaque évaluation et le bot semble simplement « ne rien
trouver ».

### Exemple chiffré, rejoué sur données réelles

Le 29/10/2026 à 16:00 UTC, `BTC/EUR` clôturait à 73 171,80, au-dessus de son EMA200 H4
(72 512,77), avec un ATR14 H4 de 749,34 et une dernière H1 à +1,43 ATR. Avec 9,37 EUR
et le profil `micro`, le pare-feu **approuve** :

```
quantité        0,00006402 BTC  →  4,68 EUR (50 % de l'équité, plafond d'exposition)
stop            71 298,40
take-profit     76 918,60
risque réel     0,1199 EUR = 1,28 % de l'équité   (budget 1,75 %)
```

Ce n'est pas un ordre : c'est le calcul rejoué sur des bougies passées, avec les
contraintes réelles du marché. Il montre que le dimensionnement n'est plus le blocage —
le filtre de tendance, lui, refuse tant que le prix reste sous l'EMA200.

---

## 6. Profit Sweeper : option A, sans migration

Kraken n'a pas de compartiment « funding » séparé pour le spot. Le schéma actuel suffit
déjà à tenir un ledger virtuel :

- écrire la ligne `profit_sweeps` avec `transfer_status = 'SIMULATED'` et
  `to_account = 'virtual_reserve'` ;
- calculer la réserve comme `SUM(swept_amount)` par mode et devise de cotation ;
- passer au pare-feu `free_quote = solde_exchange − réserve`.

L'option B (convertir en EUR ou transférer) déplace réellement l'argent et demande des
permissions de retrait — précisément ce qu'on refuse. Elle est écartée.

---

## 7. Ce qui bloque la suite

1. ✅ **Clé Kraken corrigée** (2026-11-04) : `withdraw-funds`, `add-funds` et
   `earn-funds` retirés, allowlist d'IP active. Le garde-fou passe.
   ✅ **Support de la seconde clé** (2026-11-05) : `KILL_SWITCH_API_KEY` /
   `KILL_SWITCH_SECRET` sont lus par `settings.py` et utilisés par `kill_switch.py`,
   avec repli averti sur la clé du bot. Reste à créer la paire côté Kraken pour que le
   kill-switch puisse signer en parallèle sans collision de nonce.
2. L'exécuteur réel et la réconciliation (étapes 1.2, 1.6, 1.7).
3. Le sweep virtuel (1.9) et le TP frère.
4. **Univers retenu : 7 paires.** Le scan de tout l'univers EUR montre que 10 paires
   sont finançables à 9,37 EUR, et que les moins connues (`NEAR`, `ZRO`, `TAO`,
   `ONDO`, `ARB`) exigent 12 à 27 EUR : leur ATR de 2 à 4 % impose un stop large.
   Retenues avec marge : `BTC`, `ETH`, `ADA`, `DOGE`, `PAXG`, `HBAR`, `BNB`.
   `XRP`, `TRX` et `LINK` passent à 0,03-0,23 EUR près — écartées, elles
   basculeraient au premier souffle d'ATR. `check_kraken.py --scan` recalcule la liste.
5. **Capital : 9,37 EUR, suffisant en `micro` seulement.** Mesuré sur le marché réel
   (ATR14 H4, stop 2,5 × ATR) :

   | Paire | Min ordre | ATR H4 | Équité requise à 0,5 % | à 1,75 % |
   |---|---|---|---|---|
   | `BTC/EUR` | 4,18 EUR | 1,20 % | 25,00 EUR | **8,36 EUR** |
   | `ETH/EUR` | 4,12 EUR | 1,48 % | 30,49 EUR | **8,71 EUR** |
   | `SOL/EUR` | 4,13 EUR | 1,89 % | 38,96 EUR | 11,13 EUR |

   Au profil prudent, **aucune** paire n'est tradable : le minimum d'ordre risquerait
   1,33 % à 2,08 % de l'équité, pas 0,5 %. Le profil `micro` (1,75 % de risque, 50 %
   d'exposition) rend `BTC/EUR` et `ETH/EUR` tradables, avec une perte maximale par
   trade de 0,16 EUR. `SOL/EUR` reste hors d'atteinte : il manque 1,76 EUR.
   `backend/.env` active `micro` sur `BTC/EUR` et `ETH/EUR` ; les deux valeurs se
   changent en une ligne.

## 8. Commandes

```bash
cd backend
.venv/bin/python scripts/check_kraken.py --markets       # aucune clé requise
.venv/bin/python scripts/check_kraken.py --permissions   # verdict fail-closed
.venv/bin/python scripts/check_kraken.py --affordable    # équité requise, équité réelle
.venv/bin/python scripts/check_kraken.py --affordable --profile micro --equity 9.37
.venv/bin/python scripts/reconcile.py               # audit base ↔ exchange, lecture seule
.venv/bin/python scripts/verify_drawdowns.py        # jeu figé, transaction annulée
.venv/bin/python -m pytest tests/test_kraken_adapter.py tests/test_affordability.py
```
