# Journal de projet — Trading Ninja

Une entrée par session de travail, **la plus récente en haut**. Chaque entrée dit ce
qui a été fait, ce qui l'a vérifié (`commande → résultat réel`), et ce qui a été décidé.
Ce qui est *prévu* va dans `ROADMAP.md`, pas ici.

---

## 2026-11-04 · 12:05 — Backtest historique et régression récurrente

**Construit** : `app/backtest/` (rejeu sans lookahead, réutilise `build_snapshot`,
`evaluate`, `PaperBroker`), golden de décisions, CLI `scripts/backtest.py`
(record / replay / watch / scénario de seuil), veille `scripts/backtest_watch.sh`,
régression dans pytest et dans le crochet pre-commit, rendez-vous hebdomadaire.

**Mesuré** — BTC/EUR, 2026-10-10 → 2026-11-04, `qwen3.5:9b`, profil `micro` :

```
bougies                 150 · décisions 45 (BUY 27, HOLD 18)
appels approuvés        0
rejets                  CONFIDENCE_TOO_LOW 27, ACTION_IS_NOT_BUY 18
trades                  0 · PnL 0,00 EUR
buy & hold              -6,15 %
contrefactuel           -5,94 EUR (acheter à chaque refus)
latence médiane         1,25 s
```

**Constat** : le modèle plafonne à 0,72 de confiance sur les BUY, le profil exige
0,85 → aucun achat ne peut passer. Scénario à 0,70 : 2 trades, 2 pertes,
−0,25 EUR (−2,68 %), 9 refus de dimensionnement. Aucun edge démontré sur cette
fenêtre ; le seuil ne doit pas être baissé sans nouvelle mesure.

**Vérifié** : golden rejoué en 0,8 s, 12/12 décisions témoins identiques, pre-commit
ajouté pour `backend/app/`, risque et prompt.

**Décidé** : D-039 (golden déterministe + veille tolérante).

---

## 2026-11-04 · 11:55 — Documentation remise à niveau

`AGENTS.md` annonçait encore « phase 1 non commencée », l'absence de serveur HTTP et
142 tests. Réécrit : 5 étapes sur 11, moteur papier, API et dashboard, règle sur les
secrets (`.env` et `.env.local`), règle sur l'exposition du dashboard, carte du dépôt
à jour, pièges Ollama et réseau, prochaine action 1.7.

Alignés aussi : `docs/ARCHITECTURE.md` (337 tests au total), `docs/ROADMAP.md`
(18 tests Node + `manage.sh`), `backend/README.md` (gestion des processus),
`dashboard/README.md` (start via le script).

**Vérifié** : `bash scripts/check.sh` → **TOUT EST VERT**, y compris le contrôle du
nombre de tests annoncé dans `AGENTS.md`.

---

## 2026-11-04 · 11:40 — Script de gestion start/stop/restart

`scripts/manage.sh` pilote `api`, `dashboard` ou `all` : `start`, `stop`, `restart`,
`status`, `logs`. Processus détachés (`nohup`), fichiers PID et journaux dans
`scripts/.run/` (ignoré par git). Avant tout `kill`, la ligne de commande du PID est
vérifiée ; un port occupé bloque le démarrage ; les trois secrets de l'API sont
contrôlés avant lancement.

**Vérifié** (vraies commandes, processus réels) :

```
start all          → api PID 1065855, dashboard PID 1065898
curl               → api 401, dashboard 200 (page de login)
start all (bis)    → « déjà » pour les deux, aucun doublon
restart dashboard  → arrêt puis nouveau PID 1066406
stop api           → api injoignable, dashboard toujours en cours
start api          → OK
stop all           → ports 8080 et 8081 libres, PID supprimés
PID périmé 999999  → fichier ignoré et supprimé, rien tué
port 8081 occupé   → « port déjà utilisé », exit 1, dashboard non démarré
PID recyclé        → avertissement, processus innocent laissé vivant
```

**Décidé** : D-038 (vérification du PID avant `kill`).

---

## 2026-11-04 · 10:50 — Dashboard exposé sur 0.0.0.0

À la demande de Seb : le dashboard écoute sur `0.0.0.0:8081` via `dashboard/.env`.
Garde-fou ajouté : toute interface hors boucle locale exige
`DASHBOARD_ALLOW_REMOTE=true`, sinon le serveur refuse de démarrer. Il avertit que le
mot de passe et le code Authy circulent en clair sans TLS. L'API reste sur
`127.0.0.1:8080` — le dashboard est le seul point d'entrée réseau.

**Vérifié** :

```
ss -ltn                      → LISTEN 0.0.0.0:8081
http://127.0.0.1:8081/       → 302 /login
http://192.168.1.49:8081/    → 302 /login
login via l'IP LAN           → 302 / · cookie posé
status via l'IP LAN          → 200 · IDLE · 9.37
pause / resume via l'IP LAN  → 200 · LOCKED_CIRCUIT_BREAKER / IDLE
node --test                  → 18 passed
```

**Décidé** : D-037 (deux gestes pour exposer, API maintenue sur la boucle).

**Reste** : TLS ou tunnel avant tout usage hors réseau local, et le panic réel.

---

## 2026-11-04 · 10:20 — Dashboard Node.js

**Construit** — interface de supervision zéro dépendance (`node:http`, `fetch`,
`node:test`), client mince de l'API :

```
dashboard/server.js       sessions, proxy API, SSE, en-têtes de sécurité
dashboard/public/         index, login, app.js, style.css (aucun framework)
dashboard/test/           15 tests sur une fausse API
```

Le JWT reste dans le processus Node. Le navigateur ne reçoit qu'un cookie
`HttpOnly` / `SameSite=Strict` de 15 minutes, et le panic-sell redemande un code
Authy frais. Le flux temps réel est du SSE : le serveur interroge l'API toutes les
2 secondes, le navigateur n'a besoin d'aucune bibliothèque.

**Vérifié bout en bout** (vraie API, vraie base, vrai serveur Node) :

```
racine          302 → /login
login           302 → / · cookie tn_session posé
page            200 · HTML servi
status          200 · IDLE · équité 9.37
pause           200 · LOCKED_CIRCUIT_BREAKER
resume          200 · IDLE
panic           200 · EMERGENCY_HALT (aucune position à liquider)
resume (post)   200 · IDLE
SSE             data: {"mode":"PAPER","state":"IDLE",...}
node --test     15 passed
pytest          319 passed
```

**Décidé** : D-036 (client mince, cookie HttpOnly, SSE, zéro dépendance).

**Reste** : le panic-sell réel et le kill-switch réel — donc l'exécuteur live, qui
attend un feu vert explicite.

---

## 2026-11-04 · 09:50 — API d'administration

**Construit** — FastAPI, trois preuves pour entrer, aucune documentation publiée :

```
app/api/security.py   scrypt + JWT HS256 15 min + TOTP ±1 pas anti-rejeu + limiteur
app/api/main.py       login, status, pause, resume, panic, historique, WebSocket
app/api/service.py    sérialisation sans flottants (Decimal → str)
scripts/hash_password.py   hash du mot de passe admin
scripts/totp_setup.py      secret TOTP + QR code (Authy, Aegis...)
```

Le panic-sell exige un code TOTP **frais** en plus de la session : une session volée ne
suffit pas à liquider. Le WebSocket s'authentifie par un premier message, pas par
l'URL — un jeton en query string finit dans les journaux. Swagger est désactivé.

**Vérifié sur un vrai serveur** (uvicorn, vraie base, secrets temporaires en
environnement — pas dans `.env`) :

```
login           200 · expire en 900 s
status          200 · IDLE · équité 9.37
pause           200 · LOCKED_CIRCUIT_BREAKER
resume          200 · IDLE
panic           200 · state EMERGENCY_HALT (aucune position à liquider)
resume (post)   200 · IDLE
pytest          318 passed
```

**Décidé** : D-034 (trois preuves, pas de docs, WS hors URL), D-035 (pause = état
bloqué, reprise uniquement depuis un état bloqué).

**Reste** : le dashboard (le flux WebSocket est prêt) et le panic-sell réel.

---

## 2026-11-04 · 19:20 — Écrémage et kill-switch

**Profit Sweeper** — à chaque clôture gagnante, 30 % du gain net part en réserve
virtuelle. `app/engine/sweep.py` calcule, l'orchestrateur écrit. Trois cas :

```
perte ou break-even   → aucune ligne (la contrainte SQL realized_net_pnl > 0 l'impose)
gain 0,20 EUR         → 0,06 EUR calculé < seuil 1,00 → SKIPPED_BELOW_MINIMUM
gain >= 3,33 EUR      → 1,00 EUR écrémé → SIMULATED, cash réduit, réserve augmentée
```

La réserve est `SUM(swept_amount)` des lignes `SIMULATED`/`SUCCESS`. Elle sort du
capital de trading (le dimensionnement en tient compte) mais reste dans l'équité :
l'argent n'est plus risqué, il n'est pas perdu.

Les reports s'accumulent : 0,06 reporté + 0,06 du trade suivant = 0,12 ≥ 0,10 →
écrémage exécuté, reports antérieurs soldés. Vérifié en base : 0 → 0,006 → 0.

**Kill-switch** — `scripts/kill_switch.py`, processus séparé, fonctionne backend
arrêté. Il liquide au prix courant avec slippage et frais, annule le stop armé, écrit
un ordre `EMERGENCY_EXIT`, passe la position en `EMERGENCY_LIQUIDATED` et force
`bot_state` en `EMERGENCY_HALT`. `--dry-run` montre sans modifier. Le mode `live` est
refusé : le chemin d'envoi réel n'est pas câblé, et un kill-switch à moitié réel serait
plus dangereux que pas de kill-switch.

**Vérifié sur le vrai schéma** (transaction annulée ensuite, base intacte) :

```
liquidation   → BTC/EUR 89,955 · net -0,484789 · 1 ordre annulé
état          → EMERGENCY_HALT
position      → EMERGENCY_LIQUIDATED / EMERGENCY_KILL
écrémage      → SIMULATED 0,06 · SKIPPED 0,006 (seul le premier compte)
réserve       → 0,06 · 2 lignes profit_sweeps · rollback OK
scripts/kill_switch.py --dry-run → aucune position, exit 0
pytest        → 278 passed
```

**Décidé** : D-032 (écrémage virtuel, réserve comptable), D-033 (kill-switch = override
humain, hors graphe et hors pare-feu).

**Reste** : le kill-switch réel (annulation + vente Kraken) et l'API d'administration.

---

## 2026-11-04 · 18:10 — Le moteur papier tourne de bout en bout

**Construit** — la tranche verticale complète, de la bougie au fill simulé :

```
app/market/feed.py        bougies clôturées, EMA200/50, ATR, RSI14, MACD, carnet
app/llm/client.py         API native Ollama, schéma contraint, repli HOLD, audit
app/db/repository.py      signals, évaluations, positions, ordres, équité, état
app/engine/machine.py     graphe d'états explicite, transitions interdites refusées
app/execution/paper.py    slippage, frais taker, gaps, stop prioritaire sur le TP
app/runtime/orchestrator.py  un cycle = une décision, jamais deux
app/main.py               --once / --cycles / --status, papier uniquement
```

**Premier cycle réel** (Kraken + Supabase, 7 paires) :

```
signaux 7 · évaluations 0 · approuvés 0 · replis LLM 0
BTC/EUR HOLD 0.95 · ETH HOLD 0.95 · ADA HOLD 0.95 · DOGE HOLD 0.95
PAXG HOLD 0.75 · HBAR HOLD 0.95 · BNB HOLD 0.85
```

Tous HOLD parce que le prix est sous l'EMA200 H4 : c'est le filtre de tendance qui
parle, pas une panne. Aucun ordre, aucune position.

**LLM mesuré** — `qwen3.5:9b` met 6,3 s avec réflexion, **1,0-1,5 s avec `think=false`**.
L'API compatible OpenAI d'Ollama ignore `think` et `format` ; le client est passé à
l'API native `/api/chat` avec schéma de décodage contraint. Délai porté à 8 s : à 4 s,
100 % des appels basculaient en repli.

**Intégration base réelle** — un cycle complet avec décision approuvée a écrit signal,
évaluation, position, ordres et instantané d'équité, puis la transaction a été annulée.
Les casts d'énumérations, JSONB, UUID et NUMERIC sont donc validés sur le vrai schéma,
pas seulement sur des doubles.

**Vérifié**
```
python -m app.main --once       → 7 signaux persistés, état IDLE → IDLE, 32,8 s
python -m app.main --status     → signaux lisibles, aucune décision, aucune position
pytest                          → 263 passed
écritures SQL                   → signal, évaluation, position, ordre, clôture : OK
cycle approuvé en transaction   → position créée puis annulée : OK
```

**Décidé** : D-028 (LLM natif, 8 s), D-029 (papier pessimiste), D-030 (HOLD hors
pare-feu), D-031 (ledger dans `equity_snapshots`).

**Reste** : câbler l'envoi réel (le module Kraken construit un bracket, aucun chemin ne
l'appelle), la réconciliation exchange (1.7), le sweep (1.9), l'API (1.10), le
kill-switch (1.11). Le mode `live` refuse de démarrer tant que ces briques manquent.

---

## 2026-11-04 · 17:20 — Clé corrigée, univers de 7 paires

**Clé Kraken** — Seb a retiré `Withdraw Funds`, `Deposit Funds` et `Earn`. Vérifié par
`GetApiKeyInfo` : `query-funds`, `query-open-trades`, `query-closed-trades`,
`modify-trades`, `close-trades`, `query-ledger`, `export-data`. Le garde-fou passe.

**Mon erreur, corrigée** — j'avais annoncé « aucune IP allowlist » : l'API renvoie
`ip_allowlist` en snake_case, je lisais `ipAllowlist` (la forme de la doc). La restriction
était bien en place (`82.65.136.32/32`). Le nom de champ est maintenant lu dans les deux
formes, et un test le fige.

**Question « d'autres cryptos ? »** — oui, et c'est calculable. Scan de tout l'univers EUR
(spot, actif, liquidité ≥ 100 k EUR, stablecoins exclus), ATR14 H4 réel sur les 30 plus
liquides :

```
10 paires tradables à 9,37 EUR en micro :
BTC, ETH, XRP, TRX, ADA, LINK, DOGE, PAXG, HBAR, BNB

Les alts moins connues sont hors budget :
NEAR 17,80 · ZRO 20,94 · TAO 12,90 · ONDO 15,03 · ARB 14,94
```

Ce n'est pas la notoriété qui bloque, c'est l'ATR : 2 à 4 % de volatilité élargissent le
stop, donc l'équité requise. À 9,37 EUR, on peut trader des alts, mais pas les plus
volatiles.

**Univers retenu** — 7 paires avec au moins 0,4 EUR de marge : `BTC/EUR`, `ETH/EUR`,
`ADA/EUR`, `DOGE/EUR`, `PAXG/EUR`, `HBAR/EUR`, `BNB/EUR`. `XRP`, `TRX` et `LINK`
passent à 0,03-0,23 EUR près, écartées : elles basculeraient au premier souffle d'ATR.
PAXG (or tokenisé, ATR 0,77 %) est conservée comme paire la plus stable de l'univers.

**Écrit**
- `check_kraken.py --scan` : scan reproductible de l'univers, avec équité requise.
- `KeyAudit` : permissions + `ip_allowlist` + `nonce_window`, au lieu d'un simple set.
- `backend/.env` : 7 paires, profil micro, Kraken.
- 2 tests sur les deux formes du nom de champ IP.

**Vérifié**
```
check_kraken.py --permissions   → OK, 7 permissions, IP 82.65.136.32/32
check_kraken.py --scan          → 46 candidates, 10 tradables, 7 retenues
check_kraken.py --affordable    → 7/7 OK, équité requise 7,74 à 8,91 EUR
pytest tests/test_kraken_adapter.py → 22 passed
```

**Décidé** : D-027.

---

## 2026-11-04 · 16:10 — Compte de 9,37 EUR : profil micro et préflight de faisabilité

**Décision de Seb** — ne pas ajouter de capital. Le code doit donc fonctionner avec
9,37 EUR, ou dire précisément pourquoi il ne peut pas.

**Mesure** — le blocage n'est pas le plafond d'exposition (20 % = 1,87 EUR), c'est le
budget de risque face au minimum d'ordre. Avec un ATR H4 réel et un stop à 2,5 × ATR :

| Paire | Min ordre | Équité requise à 0,5 % | à 1,75 % |
|---|---|---|---|
| BTC/EUR | 4,18 EUR | 25,00 EUR | 8,36 EUR |
| ETH/EUR | 4,12 EUR | 30,49 EUR | 8,71 EUR |
| SOL/EUR | 4,13 EUR | 38,96 EUR | 11,13 EUR |

Au profil prudent, aucune paire n'est tradable. Le minimum d'ordre risquerait 1,33 % à
2,08 % de l'équité, pas 0,5 %.

**Écrit**
- `app/engine/affordability.py` : module pur qui calcule le minimum d'ordre, le risque
  qu'il représente, l'équité requise et la contrainte bloquante (`risk`, `exposure`,
  `absolute_cap`).
- Preset `micro` : risque 1,75 %, exposition 50 %, confiance 0,85, drawdown 8 %/15 %/20 %.
  Un test vérifie que le drawdown journalier reste ≥ 2 × le risque par trade, sinon une
  seule perte verrouillerait la journée.
- `backend/.env` (non secret, ignoré par git) : `RISK_PROFILE=micro`, `EXCHANGE_ID=kraken`,
  `BTC/EUR` et `ETH/EUR`. `SOL/EUR` exclu — il manque 1,76 EUR.
- `check_kraken.py --affordable`, avec lecture du solde en lecture seule.
- 6 tests d'affordabilité, dont un qui vérifie que la prédiction correspond au refus réel
  du pare-feu (`SIZING_BELOW_EXCHANGE_MINIMUM`).

**Vérifié**
```
check_kraken.py --affordable                        → BTC 8,36 · ETH 8,71 · OK
check_kraken.py --affordable --profile prudent      → 25,00 / 30,49 / 38,96 · tout refusé
check_kraken.py --affordable --symbol SOL/EUR       → manque 1,76 EUR
load_settings()                                     → micro, kraken, BTC/EUR, EUR, 0.0052
pare-feu rejoué sur la H4 du 29/10 16:00 UTC        → APPROUVÉ, 4,68 EUR, risque 0,12 EUR
```

**Exemple rejoué (données réelles, aucun ordre)** — `BTC/EUR` clôture 73 171,80 au-dessus
de l'EMA200 H4, ATR14 H4 749,34 : quantité 0,00006402 BTC (4,68 EUR, 50 % de l'équité),
stop 71 298,40, TP 76 918,60, risque effectif 0,1199 EUR (1,28 %). Le filtre de tendance
refuse le marché actuel (prix sous l'EMA200), pas la taille.

**Décidé** : D-025, D-026.

**Perte maximale par trade** : 0,16 EUR. Aucun ordre envoyé, aucun exécuteur n'existe.

---

## 2026-11-04 · 15:30 — Kraken : brief corrigé, adaptateur écrit, clé refusée

**Contexte** — brief de « migration Binance → Kraken » reçu. Vérification des prémisses
avant d'écrire : le brief vise `CCXTOrderExecutor`, `DataFeed` et `app/core/config.py`,
qui n'existent pas. Ce n'est pas une migration, c'est la première implémentation de la
couche exchange.

**Découverte de sécurité** — `.env.local` contient désormais une clé Kraken (ajoutée
depuis la session du matin). Elle authentifie, mais `GetApiKeyInfo` renvoie
`withdraw-funds`, `add-funds`, `earn-funds`, `export-data`… et **aucune IP allowlist**.
Le garde-fou la refuse. Compte : 9,37 EUR. Aucun ordre envoyé.

**Le brief, confronté au réel (CCXT 4.5.91)**
1. `params={"stopPrice": …}` produit `ordertype=stop-loss` **sans prix**. Le paramètre lu
   est `stopLossPrice`. Un stop sans prix = position nue ou rejet.
2. `float(...)` sur la précision viole la règle `Decimal` (D-007).
3. `fee_buffer_pct` valait 0,2 % : tarif Binance. Kraken prend 0,26 %/côté, donc 0,52 %
   l'aller-retour → D-021.
4. `adjustForTimeDifference` ne règle pas les nonces ; le compteur CCXT est par instance,
   donc bot + kill-switch sur la même clé = collisions (docstring `incrementing_nonce`).
5. Le TP ne peut pas tenir dans le `close` : un seul conditionnel par ordre.
6. Les WebSockets temps réel contredisent la règle des bougies clôturées.
7. `CryptoFacilities/REST-v3-Python` est l'API **futures** (2021), pas le spot.

**Écrit**
- `app/exchange/kraken.py` : marchés → `ExchangeConstraints` en `Decimal`, formatage qui
  refuse tout dépassement, bracket `close`/`stop-loss` exigeant une `RiskDecision`
  approuvée, permissions fail-closed.
- `scripts/check_kraken.py` : marchés sans clé, permissions avec clé.
- `settings.py` : alias `KRAKEN_*` choisis selon `EXCHANGE_ID`, frais par exchange,
  devise de cotation déduite du symbole, liste de paires.
- `docs/KRAKEN.md` : la spec corrigée, avec les requêtes réelles en preuve.

**Défaut trouvé par un test** — la comparaison de permissions par sous-chaînes faisait
passer `query-closed-trades` pour un droit d'annulation (« closed » contient « close »).
Remplacée par une comparaison de jetons exacts (D-022).

**Vérifié**
```
check_kraken.py --markets      → BTC/EUR, ETH/EUR, SOL/EUR actifs, min 0,45 EUR,
                                 tick 0,1 / 0,01, taker 0,26 %
check_kraken.py --permissions  → ÉCHEC attendu : withdraw-funds présent
fetch_balance (lecture)        → 9,37 EUR disponibles, 1 470 marchés chargés
pytest                         → 203 passed (dont 20 Kraken, 7 settings)
```

**Décidé** : D-020 à D-024.

**Reste** : remplacer la clé Kraken (sans retrait, IP allowlist), puis l'envoi réel et
la réconciliation.

---

## 2026-11-04 · 14:20 — Clé Binance vérifiée, index du code généré

**Contexte** — reprise du dépôt : lecture complète des trois specs périmées, de
`AGENTS.md`, des quatre documents de suivi, du pare-feu et de la configuration.
Deux demandes : indexer le code pour chaque étape à venir, puis tenter une connexion
Binance avec les clés existantes.

**Index** — `scripts/index_codebase.py` génère `docs/CODE_INDEX.md` depuis les sources
(AST Python + regex SQL) : modules, classes, méthodes avec numéros de ligne, migrations
et objets créés, comptage des tests. Il n'est pas écrit à la main, sinon il mentirait
au premier commit. Deux modes : écriture et `--check`. `scripts/check.sh` le vérifie
désormais en section 6.

**Piège rencontré pendant l'indexation** — un comptage statique des `def test_*`
annonçait **73** tests alors que pytest en collectait **167** : `parametrize` multiplie
les cas d'une même fonction. Le générateur demande donc la mesure à
`pytest --collect-only` et retombe sur le comptage statique, explicitement étiqueté,
seulement si pytest est absent. Un index faux est pire que pas d'index.

**9 tests ajoutés** (`tests/test_check_exchange.py`) pour figer le verdict de la sonde,
dont la régression de D-018 : une clé saine ne doit pas être condamnée parce que le
compte, lui, peut retirer. Suite finale : **176 cas**.

**Sonde Binance** — `backend/scripts/check_exchange.py` : lecture seule (aucun ordre,
aucun transfert), signature HMAC-SHA256, décalage d'horloge recalculé depuis
`/api/v3/time`, verdict fail-closed. Deux réseaux et un symbole testés : compte mainnet,
compte testnet, filtres BTC/USDT.

**Résultat mesuré**
```
clé : 64 caractères (valeur non affichée)
[OK   ] mainnet — clé lecture + trading spot, retraits désactivés
        canTrade=True canWithdraw=True canDeposit=True type=SPOT
        apiRestrictions : enableReading=True enableSpotAndMarginTrading=True
                          enableWithdrawals=False ipRestrict=True
        soldes non nuls : aucun (compte vide)
[ÉCHEC] testnet — HTTP 401 · code -2015 · Invalid API-key, IP, or permissions
[OK   ] marché — BTCUSDT TRADING · prix 78 412 USDT
        tick=0.01 pas=0.00001 min_qty=0.00001 min_notional=5 USDT
```

**Deux enseignements**
1. Les clés sont **mainnet**, pas testnet. La question n°1 de la feuille de route est
   levée ; une clé testnet dédiée reste à créer pour la phase 3.
2. `canWithdraw=True` au niveau du compte n'est **pas** la permission de la clé.
   `apiRestrictions.enableWithdrawals=false` est la valeur qui fait foi. Confondre les
   deux condamne une clé saine et transforme un garde-fou en fausse alerte → D-018.

**Vérifié**
```
backend/.venv/bin/python scripts/check_exchange.py --market → exit 0, mainnet + filtres OK
backend/.venv/bin/python scripts/index_codebase.py       → 309 lignes, 176 cas collectés
backend/.venv/bin/python scripts/index_codebase.py --check → « à jour »
backend/.venv/bin/python -m pytest                       → 176 passed
bash scripts/check.sh                                    → VERT (une alerte AGENTS.md protégée)
```

**Décidé** : D-018 (autorité de `apiRestrictions`), D-019 (index généré et vérifié).

**Reste** : porter le refus de boot de 1.1 dans un point d'entrée qui n'existe pas
encore ; le compte mainnet est vide, donc aucun montant n'est engagé.

---

## 2026-11-04 · 09:05 — Audit à froid : verdict et corrections

**Méthode** — un agent séparé, sans aucun accès à l'historique de la conversation, a relu
le dépôt seul et répondu à quatre questions : ce que fait le projet, quelles garanties de
sécurité il a trouvées, l'état exact, la prochaine action. Puis il a listé tout ce qui
l'avait obligé à deviner.

**Verdict** : `pret_avec_reserves`. 18 contradictions documentaires, 7 lacunes, 10 gestes
à risque identifiés. Il a trouvé **quatre vrais défauts** qu'aucun test n'attrapait.

**Défauts de code corrigés**
1. `TRADING_MODE=testnet` + `EXCHANGE_SANDBOX_MODE=false` chargeait **sans erreur** :
   ordres réels étiquetés `TESTNET` dans le ledger. Le verrou ne couvrait que `live`.
   → D-015, 3 tests ajoutés.
2. `evaluate()` levait sur un horodatage naïf (`TypeError`) et sur `Decimal('NaN')`, qui
   n'est jamais `<= 0` et traversait donc tous les filtres. Le module promettait pourtant
   de ne jamais lever. → validation en entrée + enveloppe `INTERNAL_ERROR`, 7 tests.
3. `risk_evaluations` imposait `market_price > 0` en NOT NULL : un refus précoce
   (`MALFORMED_INTENT`, `INVALID_MARKET_DATA`) ne pouvait pas être journalisé, alors que
   la phase 2 exige de tracer tous les refus. → colonnes nullables + contrainte
   « contexte obligatoire seulement si accepté » (migration 0003).
4. `bot_state` était un singleton tous modes confondus : passer en `LIVE` héritait du
   plus-haut d'équité de la simulation, donc d'un verrou permanent. → une ligne par mode.

**Contradictions documentaires corrigées**
nombres de tests faux dans quatre fichiers, phase 1 déclarée « en cours » alors que ses
11 étapes étaient toutes à zéro, tableau des filtres incomplet (deux codes manquants),
D-004 qui parlait encore de 1 %, « toutes les tables portent `mode` » alors que
`schema_migrations` n'en a pas, crochet `pre-commit` annoncé comme scannant les `.md`
alors qu'il les excluait, renvois de bannière imprécis.

**Fait aussi** : `check.sh` vérifie désormais que le nombre de tests annoncé dans
`AGENTS.md`, `docs/ARCHITECTURE.md` et `backend/README.md` correspond au réel — c'est ce
qui aurait attrapé la dérive de chiffres sans intervention humaine.

**Vérifié**
```
pytest                       → 167 passed (129 pare-feu, 15 contrat, 13 config, 10 base)
db_migrate.py apply          → 0003_audit_fixes.sql en 164 ms
db_migrate.py verify         → 25 relations, 5 vues, 16 partitions
bot_state                    → PAPER / TESTNET / LIVE, colonne id supprimée
v_system_health              → 3 lignes, compteurs filtrés par mode
```

**Reste en attente** : `AGENTS.md` n'a pas pu être mis à jour — l'écriture sur ce fichier
est protégée par une demande d'approbation qui a expiré. Il annonce encore 142 tests et
une règle 2 absolue qui contredit l'exception des sorties d'urgence. `check.sh` le signale
en rouge tant que ce n'est pas corrigé. Le reste du dépôt est cohérent.

---

## 2026-11-04 — Documentation de reprise durcie

**Question posée** : « si une IA reprend le projet, elle comprendra tout sans souci ? »
Réponse honnête : non. Trois pièges ont été identifiés et corrigés.

**Les trois pièges**
1. Les trois `PROMPT_*.md` d'origine étaient toujours là, non marqués, et **contredisaient
   l'implémentation** (TimescaleDB, codes de rejet, états, colonnes). Un repreneur les
   aurait pris pour la référence.
2. Rien ne disait où chercher en arrivant : pas de fichier `AGENTS.md`, aucune carte des
   modules, aucune liste de ce qui existe réellement.
3. L'ordre d'autorité entre documents était une phrase perdue dans `DECISIONS.md`, sans
   application mécanique.

**Fait**
- `AGENTS.md` : règles non négociables, ordre d'autorité, carte du dépôt, pièges de
  l'environnement, prochaine action.
- `docs/ARCHITECTURE.md` : flux de décision, tableau « ce qui existe / ce qui n'existe
  pas », les 15 filtres, modèle de données.
- Bannière « DOCUMENT HISTORIQUE — PÉRIMÉ » en tête des trois specs d'origine.
- `backend/tests/test_schema_contract.py` : lit le SQL sur disque et le compare aux
  `StrEnum` Python. Une divergence entre l'ENUM `risk_rejection_code` et le code ne peut
  plus être committée.
- `scripts/check.sh` : vérifie outillage, secrets, tests, migrations et cohérence
  documentaire en une commande, avec un message explicite par ligne rouge.

**Vérifié**
```
bash scripts/check.sh
  → outillage Python 3.15.2 / node v26.13.0
  → .env.local ignoré, crochet actif, non suivi
  → 157 passed (dont 15 de contrat de schéma)
  → 2 migrations appliquées, aucune en attente
  → documentation complète → TOUT EST VERT
```

**En cours** : audit à froid par un agent sans contexte, chargé de lire uniquement le
dépôt et de signaler ce qui reste ambigu. Résultat consigné ci-dessous à réception.

---

## 2026-11-04 — Clés Binance raccordées, outillage à jour, suivi de projet

**Fait**
- Le code accepte désormais les noms `BINANCE_API_KEY` / `BINANCE_SECRET_KEY` du
  `.env.local` (alias de `EXCHANGE_API_KEY` / `EXCHANGE_SECRET`), sans renommer le
  fichier de secrets.
- Python 3.15.2 et Node 26.13.0 LTS installés en espace utilisateur, venv du projet
  reconstruit, tout le code exécuté dessus.
- Création de `docs/ROADMAP.md`, `docs/DECISIONS.md` et de ce journal.

**Vérifié**
```
.venv/bin/python -m pytest                → 142 passed
scripts/db_migrate.py verify              → schéma conforme, 25 relations, 5 vues, 16 partitions
python3 -V (dans le projet)               → 3.15.2
/usr/bin/python3 -V (système, intact)     → 3.12.3
node -v  /  npm -v                        → v26.13.0  /  11.20.0
load_settings()                           → clé API présente (64 car.), secret présent, mode PAPER
```

**Décidé**
- D-012, D-013 (voir `DECISIONS.md`).

---

## 2026-11-04 — Faille d'exposition Supabase fermée

**Constat mesuré, pas supposé**
La clé publique Supabase permet d'interroger le schéma `public` par l'API REST. Les
tables étaient protégées (RLS active, zéro ligne visible), mais **les vues ne l'étaient
pas** : une vue s'exécute avec les droits de son propriétaire et contourne la RLS.
`v_system_health` renvoyait 1 ligne à `anon` alors que `bot_state` n'en renvoyait aucune.

**Fait**
- Migration `0002_lock_public_api.sql` : vues en `security_invoker`, révocation de tous
  les droits de `anon` et `authenticated`, `ALTER DEFAULT PRIVILEGES` pour les objets
  futurs, RLS réappliquée sur toutes les tables.

**Vérifié**
```
SET LOCAL ROLE anon → select … from positions / bot_state / llm_signals / orders /
   profit_sweeps / equity_snapshots / v_*       → privilège refusé sur les 10 relations
rôle postgres (backend)                         → lecture normale, 1 ligne dans bot_state
```

**Décidé** : D-011.

---

## 2026-11-04 — Base de données créée sur Supabase

**Fait**
- Schéma complet appliqué : 9 tables (dont `bot_state`, `equity_snapshots`), 2 tables
  partitionnées par mois, 5 vues analytiques.
- Coureur de migrations avec `status` / `apply` / `verify`, verrou d'advisory lock,
  empreinte SHA-256 de chaque migration.
- Garde-fous portés par la base elle-même : index unique « une seule position ouverte
  par symbole », index du coupe-circuit « max ordres/heure », `positions.take_profit_price`,
  `profit_sweeps.transfer_status` en énumération, colonne `execution_mode` partout.

**Deux surprises rencontrées**
- TimescaleDB n'existe pas sur Supabase en PostgreSQL 17 → réécriture en partitionnement
  natif avec partition `DEFAULT` et fonction `ensure_partitions()`.
- La connexion directe Supabase est **IPv6 uniquement** → repli sur le pooler
  (`aws-0-eu-west-1.pooler.supabase.com`, région trouvée par élimination).

**Vérifié**
```
db_migrate.py apply    → 0001 en 415 ms, 0002 en 59 ms
db_migrate.py verify   → 25 relations, 5 vues, 16 partitions, garde-fou monoposition testé
verify via pooler      → OK également
```

**Décidé** : D-001, D-008, D-010, D-011.

---

## 2026-11-04 — Secrets protégés (trois couches)

**Fait**
- `.gitignore` complet (secrets, venv, données, exports, sauvegardes).
- Crochet `pre-commit` qui refuse un fichier de secrets dans l'index **et** une clé
  collée dans un fichier anodin ; activé par `git config core.hooksPath .githooks`.
- Dépôt git initialisé, `.env.local` vérifié non suivi.

**Vérifié**
```
git check-ignore -q .env.local            → 0 (ignoré)
git ls-files .env.local                   → aucun fichier connu de git
git add -f .env.local && .githooks/pre-commit  → exit 1, « fichier de secrets »
faux sb_secret_… collé dans un .js        → exit 1
commit normal                             → exit 0
```

---

## 2026-11-04 — Pare-feu et configuration

**Fait**
- `risk_gate.py` : module pur (aucune I/O, aucune horloge implicite), 15 étapes de
  filtrage ordonnées, dimensionnement à trois plafonds concurrents, plancher au pas de
  l'exchange, risque effectif toujours ≤ budget.
- `settings.py` : presets de risque, plafonds durs, double verrou d'armement pour
  l'argent réel.
- `demo_guardrails.py` : matrice de décisions lisible sur des chiffres réels.

**Quatre défauts des specs corrigés** (ils auraient coûté de l'argent)
1. Stop calculé sur l'ATR H1 pour une décision H4 → stop dans le bruit.
2. Filtre « falling knife » comparant un pourcentage à un prix → formule inapplicable.
3. Aucun coupe-circuit sur l'écart au plus haut d'équité → rien n'arrête une stratégie
   qui a cessé de fonctionner.
4. Aucune limite d'ordres par heure → un bug de boucle pouvait vider le compte.

**Vérifié**
```
pytest                 → 139 tests, puis 142 après les ajouts de la journée
demo_guardrails.py     → 15 scénarios, refus motivés sur chiffres réels
```

**Décidé** : D-002 à D-009.

---

## 2026-10-30 — Réception des specs

Trois documents fournis : `PROMPT_BACKEND.md`, `PROMPT_DATABASE.md`,
`PROMPT_RISK_GW.md`. Lus intégralement. Le dépôt ne contenait aucun code.

Constat de départ : l'architecture est saine (fail-closed, zéro confiance dans le LLM,
stop distant sur l'exchange, risque fractionnaire fixe), mais les trois documents se
contredisent sur les codes de rejet, les noms de champs et les états, et plusieurs
pièces indispensables manquent (pas de table d'état courant, pas de take-profit, pas
de rattachement des positions à leur décision d'entrée).
