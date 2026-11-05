# Journal de projet — Trading Ninja

Une entrée par session de travail, **la plus récente en haut**. Chaque entrée dit ce
qui a été fait, ce qui l'a vérifié (`commande → résultat réel`), et ce qui a été décidé.
Ce qui est *prévu* va dans `ROADMAP.md`, pas ici.

---

## 2026-11-05 · 07:30 — Frais maker : pas d'arête cachée ; rotation momentum mesurée (D-065, D-066)

**Fait — sensibilité aux frais maker.** `scripts/backtest.py` accepte `--maker-fees
[valeur]` (défaut 25), cumulable avec `--fee-bps` ; les trois balayages (`sweep`,
`swing`, `meanrev`) utilisent la même grille dédupliquée. C'est une **sensibilité au
coût**, pas un modèle d'exécution maker : le fill reste la clôture plus le slippage.

**Mesuré — swing D1, 60 mois, 36 scénarios.**
```
cd backend && .venv/bin/python scripts/backtest.py swing --months 60 \
  --symbols BTC/EUR,ETH/EUR,SOL/EUR --fee-bps 80 --maker-fees 25 \
  --swing-triggers both,donchian,value --exit-mode ema20,chandelier \
  --max-consecutive-losses 9999 --max-hwm-drawdown 1.0
```
→ 80 pb : 3 PF ≥ 1, 3 PF ≥ 1,30, PF max 2,31, net cumulé **−665,68 EUR**.
→ 25 pb : 8 PF ≥ 1, 3 PF ≥ 1,30, PF max 2,92, net cumulé encore **−64,98 EUR**.
Les seuls PF ≥ 1,30 restent des poches Donchian de 5 à 7 trades.

**Mesuré — mean reversion, 144 scénarios.**
→ 80 pb : 10 nets positifs, 3 PF ≥ 1,30, PF max 1,95, N médian 4.
→ 25 pb : 13 nets positifs, 7 PF ≥ 1,30, PF max 2,85, N médian 4 ; aucune poche
au-delà de 15 trades n'atteint PF 1,30. Scénario RSI 32/touch à 25 pb : SOL H4
6 trades PF 2,26 (+49,80 EUR), BTC H4 4 trades (−32,06), BTC D1 2 trades (−35,87),
ETH H4 4 trades (−32,83), ETH D1 1 trade (+53,12), SOL D1 0.
**Verdict** : les frais expliquent une partie des pertes, pas l'absence d'arête.
D-065.

**Fait — laboratoire de rotation.** `app/backtest/rotation.py` et
`scripts/lab_rotation.py` : univers BTC/ETH/SOL/CASH, score = moyenne des rendements
30 et 60 jours, 100 % sur le meilleur actif seulement s'il clôture au-dessus de son
EMA200 D1 et si le score est positif, sinon 100 % cash. Cadence 7 ou 14 jours, frais
et slippage par côté, mark-to-market quotidien. 6 tests unitaires.

**Mesuré — 2021-11 → 2026-11, 1 762 bougies D1 communes.**
```
cd backend && .venv/bin/python scripts/lab_rotation.py --months 60 \
  --fee-bps 25,80 --cadence-days 7,14 --verbose
```
| cadence | frais | équité finale | retour | DD max | rotations | frais |
|---|---:|---:|---:|---:|---:|---:|
| 7 j | 25 pb | 2 637,37 | +163,74 % | 54,57 % | 48 (9,9/an) | 391,12 |
| 14 j | 25 pb | 3 381,33 | +238,13 % | 60,13 % | 34 (7,0/an) | 443,16 |
| 7 j | 80 pb | 1 774,94 | +77,49 % | 58,80 % | 48 | 994,12 |
| 14 j | 80 pb | 2 512,46 | +151,25 % | 61,84 % | 34 | 1 200,24 |

Buy & hold même fenêtre : BTC +69,7 %, ETH −39,6 %, SOL −44,4 %. Petit compte
9,37 EUR → 24,71 EUR (mêmes proportions), mais les minimums d'ordre de l'exchange ne
sont pas modélisés par le laboratoire. **Verdict** : la rotation bat le buy & hold et
tient la cadence visée, mais des drawdowns de 55–62 % sont hors du critère de 20 %.
Laboratoire prometteur, **non activé**. D-066.

**Vérifié**
```
cd backend && .venv/bin/python -m pytest -q -o addopts=""   → 587 passed
bash scripts/check.sh                                       → TOUT EST VERT
```
587 tests = 581 + 6 rotation. `TRADING_MODE=paper`, aucun ordre réel, chemin live
toujours fermé. Aucun quatrième filtre d'entrée ajouté aux stratégies existantes.

---

## 2026-11-05 · 06:45 — Panic-sell réel, clé kill-switch dédiée, sonde 3.1 et mean reversion rejetée (D-063, D-064)

**Fait — plomberie d'urgence.**

- `app/execution/live.py` : `panic_sell()` annule **tous** les ordres ouverts, puis
  vend au marché chaque solde de base réellement détenu — y compris hors univers
  configuré. Chaque vente est vérifiée (résidu > 0,1 % = erreur), et `emergency_exit`
  ré-annule les ordres du symbole avant de vendre pour qu'un stop ne double pas la
  sortie. `PanicReport` expose les ventes, les annulations et les erreurs sans lever.
- `app/runtime/recovery.py` : `record_panic_exits()` écrit les **fills réels** au
  ledger (prix, frais, slippage), y compris pour un solde sans position en base
  (`EMERGENCY_EXIT` sans `position_id`), puis force `EMERGENCY_HALT`. Un échec partiel
  lève `PanicLiquidationError` **après** la halte.
- `app/api/main.py` : le `409` « panic réel non câblé » a disparu. Le mode `LIVE`
  passe par `panic_sell` + `record_panic_exits`, et une vente manquée répond `502`
  avec la liste de ce qui reste exposé.
- `app/core/settings.py` + `scripts/kill_switch.py` : paire secondaire optionnelle
  `KILL_SWITCH_API_KEY` / `KILL_SWITCH_SECRET`, utilisée en priorité par le
  kill-switch. Sans elle, repli averti sur la clé du bot (collision de nonce possible).
  Une paire incomplète refuse le démarrage.
- `scripts/live_probe.py` : sonde 3.1, ordre réel volontairement sous le minimum du
  marché, plafonné à 0,05 EUR, avec revente immédiate si l'ordre était accepté.

**Vérifié — sonde 3.1 sur Kraken.**
```
cd backend && .venv/bin/python scripts/live_probe.py --confirm-live --symbol BTC/EUR
→ mode paper (inchangé) · solde libre 9.37 EUR
→ ordre volontairement invalide : BUY market 7E-7 BTC/EUR (≈ 0,0500 EUR)
→ InvalidOrder: kraken {"error":["EGeneral:Invalid arguments:volume minimum not met"]}
→ ordres ouverts après le test : 0 · aucun ordre créé, aucun plantage
```
Le compte principal a servi de sonde : aucun sous-compte dédié n'existe encore. Aucun
ordre réel n'a été exécuté.

**Fait — laboratoire Mean Reversion Bollinger.** `app/backtest/mean_reversion.py` et
`backtest.py meanrev` implémentent l'hypothèse demandée : clôture sous la bande basse
(20 ; 2), RSI(14) < 32, volume > SMA(volume, 20), sortie au contact de la bande médiane
ou stop structurel sous le creux des 10 bougies. Le pare-feu reste l'autorité ; la
variante `raw` neutralise uniquement le filtre EMA200 via `allow_counter_trend`
(laboratoire, jamais lu depuis l'environnement) pour distinguer l'hypothèse de l'effet
du filtre de tendance.

**Mesuré — 2021-2026, BTC/ETH/SOL, H4 et D1, 144 scénarios.**
```
cd backend && .venv/bin/python scripts/backtest.py meanrev --months 60 \
  --symbols BTC/EUR,ETH/EUR,SOL/EUR --timeframes 4h,1d \
  --mr-rsi 30,32,35 --mr-exit touch,close --mr-variants gated,raw \
  --fee-bps 80,100
→ 144 scénarios · 18 nets positifs · 5 PF ≥ 1,30 (tous N = 2 à 6)
→ variante gated : PF max 1,95 (SOL H4, RSI 30, N = 2), net cumulé −1 730 EUR
→ variante raw   : PF max 0,85, net cumulé −4 804 EUR
```
Scénario principal demandé (RSI 32, `touch`, 80 pb) : BTC H4 4 trades PF 0,00
(−53,25 EUR), ETH H4 4 trades PF 0,00 (−54,00), SOL H4 6 trades PF 1,28 (+15,53),
BTC D1 2 trades PF 0,00 (−46,48), ETH D1 1 trade +47,31, SOL D1 0 trade.
**Hypothèse rejetée** : l'arête n'existe pas hors tendance haussière, et les rares
scénarios positifs tiennent sur un échantillon non interprétable.

**Vérifié — suite complète.**
```
cd backend && .venv/bin/python -m pytest -q -o addopts=""   → 581 passed
bash scripts/check.sh                                       → TOUT EST VERT
```
581 tests = 570 précédents + 7 plomberie d'urgence + 4 mean reversion. Le golden a été
rebéni après l'ajout de `allow_counter_trend` : seules `created_at` et
`risk_fingerprint` changent, les métriques sont identiques.

**Décidé** — Rien n'est activé : `TRADING_MODE=paper`, aucun ordre réel, chemin live
toujours fermé. Le seuil d'activation (PF ≥ 1,30 **et** N ≥ 100 sur 2021-2026) reste
gelé et non atteint. Pas de quatrième filtre : le prochain levier reste la cadence de
décision (H1) ou la couverture multi-paires. La seconde clé Kraken reste à créer côté
exchange ; le code est prêt.

---

## 2026-11-05 · 06:30 — LLM D1 mesuré : il filtre les perdants, mais coupe 70 % des gagnants (D-061)

**Fait** — Le blocage Ollama de l'entrée du 2026-11-04 19:30 est levé. L'installation
système reste cassée (`/usr/local/lib/ollama` vide), mais le paquet officiel 0.40.14 a
été installé **sans sudo** dans `~/.local/opt/ollama-0.40.14`, et un second serveur
écoute sur `127.0.0.1:11435` avec `OLLAMA_MODELS=/mnt/e/models/ollama`. Le GPU
RTX 5070 Ti est utilisé ; premier chargement à froid 88 s, appels suivants 0,2–0,3 s.
L'archive (1,5 Go) est dans `/tmp/opencode/ollama-dl/`.

**Mesuré — `eval_llm_swing.py`, tout l'historique, 80 pb/côté, sortie Chandelier**
144 trades, **144 appels non dégradés** (30 BUY, 114 HOLD) :

| paire | trades | gagnants | approuvés | rétention | filtration |
|---|---|---|---|---|---|
| BTC/EUR | 71 | 14 | 16 | 42,9 % | 82,5 % |
| ETH/EUR | 62 | 8 | 12 | 12,5 % | 79,6 % |
| SOL/EUR | 11 | 1 | 2 | 0,0 % | 80,0 % |
| **global** | **144** | **23** | **30** | **30,4 %** | **81,0 %** |

Le modèle **filtre 81 % des perdants** (98/121), au-delà des 25 % demandés, mais il
**rejette 69,6 % des gagnants** (16/23) : rétention 30,4 %, verdict
`KO rétention<90%`. Il est trop sélectif. La confiance ne discrimine pas : 0,65 en
moyenne chez les gagnants, 0,62 chez les perdants ; le seuil de confiance n'est pas le
levier.

**Vérifié**
```
cd backend && LLM_BASE_URL=http://127.0.0.1:11435/v1 .venv/bin/python \
  scripts/eval_llm_swing.py --symbols BTC/EUR,ETH/EUR,SOL/EUR --months 0 \
  --fee-bps 80 --triggers both --exit-mode chandelier --out /tmp/eval_llm_swing.json
→ 144 trades, 144/144 non dégradés, rétention 30,4 %, filtration 81,0 %
```
Aucun ordre passé, aucune écriture en base.

**Décidé** — Le critère LLM D1 n'est **pas** satisfait. Le mode swing reste **non
activé**, `SWING_EXIT_MODE=ema20` par défaut, le chemin live reste fermé, aucun
changement de `.env`, aucun commit. Le prochain levier est le prompt D1 ou la définition
du signal d'entrée, pas le seuil de confiance ni un quatrième filtre.

---

## 2026-11-04 · 19:45 — Dashboard : jauges de risque, activité et refus du pare-feu (D-062)

**Fait** — Le dashboard devient un poste de supervision complet, toujours en client
mince Node.js sans dépendance.

- **API** : `friendly_settings` expose en lecture seule les plafonds hebdomadaire,
  depuis le plus haut, de pertes consécutives et le risque par achat du preset.
- **Proxy** : `GET /api/history/{table}` côté Node, limité à `llm_signals`,
  `risk_evaluations`, `orders` et `positions`, avec `limit` borné à 1..50 (défaut 20).
  Une table inconnue est refusée en `400` avant l'appel FastAPI.
- **Écran** : cinq jauges de risque, courbe du capital avec périodes 24 h / 7 j / 30 j /
  tout, infobulle et résumé haut/bas, histogramme quotidien avec infobulle, refus du
  pare-feu libellés en français, onglets Signaux / Pare-feu / Ordres / Positions,
  badge `PAPER`/`LIVE`, indicateur de fraîcheur, bouton « Recharger » des réglages,
  lien d'évitement, focus visible, onglets clavier, `aria-live`, favicon local.
- **Documentation** : `docs/DASHBOARD.md` (architecture, panneaux, contrat API,
  sécurité, dépannage, limites), liens depuis `README.md` et `dashboard/README.md`,
  mise à jour d'`ARCHITECTURE.md` et de la feuille de route 1.12.

**Vérifié**
```
cd dashboard && node --test test/          → 26 tests, 26 pass
cd backend && .venv/bin/python -m pytest   → 570 passed (1 warning Starlette)
bash scripts/check.sh                      → TOUT EST VERT
```

Le `check.sh` couvre l'outillage, les secrets, les 570 tests Python, les 9 migrations,
les drawdowns, la documentation, l'index du code, la syntaxe de `server.js`, les 26
tests Node et le golden de backtest.

Un smoke test Chromium headless a été exécuté sur une fausse API et une copie temporaire
du dashboard (aucun `.env`, aucun ordre réel) : mode `PAPER`, connexion SSE, 9 cartes,
jauges, quatre périodes de courbe, infobulle, histogramme, 4 onglets, changement de
limite, rechargement des réglages, aucune erreur JavaScript. La capture est dans
`/tmp/opencode/dashboard.png` (hors dépôt).

**Décidé** — D-062 : historique en liste blanche, volumes bornés, plafonds de risque
permanents en lecture seule dans le dashboard, TOTP frais réservé au panic-sell.

**Limites** — Pas de test navigateur automatisé dans `check.sh` (smoke test manuel) ;
pas encore de TOTP frais pour les réglages ; aucun commit, aucun ordre réel, aucun
changement de `.env` ou `.env.local`.

---

## 2026-11-04 · 19:30 — Quatre chantiers swing : Chandelier mesuré, LLM D1 bloqué par Ollama (D-061)

**Fait** — Les quatre chantiers du brief sont implémentés et testés.

1. **LLM D1** : prompt `D1_SYSTEM_PROMPT` / `v3-d1` séparé du H4 (intact), payload
   d'indicateurs D1 sans fuite H4, `decision_timeframe` propagé. `eval_llm_swing.py`
   rejoue les trades sans passer d'ordre et calcule rétention/filtration.
2. **Sortie Chandelier** : `max(High_20) − 3,0 × ATR_14`, cliquet haussier, sortie à la
   clôture sous le niveau. `SWING_EXIT_MODE=ema20` par défaut, `chandelier` en option ;
   période et multiple paramétrables.
3. **Infra D1** : migration `0009_allow_1d_timeframe.sql` **appliquée** sur Supabase
   (`1h`, `4h`, `1d` ; couverture 86 400 s ; vue `security_invoker`). Scheduler à
   **00:05 UTC** pour le D1 (délai de 300 s après clôture).
4. **Breakeven live** : `update_stop_loss_order()` édite si `editOrder` existe, sinon
   crée le nouveau stop **avant** d'annuler l'ancien, vérifie le prix, et retire le
   nouveau si l'ancien ne peut pas être annulé. Non branché en production.

**Mesuré — Chandelier vs EMA20, 80 pb/côté, coupe-circuits levés**
2021-11 → 2026-11 : BTC 0,44 → **0,77** (−12,69 % → −4,81 %, 23 trades) ; ETH 0,57 →
0,38 ; SOL 0,19 → 0,14. **Aucun positif.**
Tout l'historique : BTC 87 trades PF 2,20 → **71 trades PF 2,81** (+192,47 %, DD
11,99 %) ; ETH 74 trades PF 1,10 → **62 trades PF 1,93** (+68,29 %) ; SOL 13 → 11
trades, PF 0,19 → 0,14. BTC et ETH passent PF/payoff/MDD/N, SOL non.

**Blocage — la mesure LLM ne vaut rien.** 144 appels sur les trades Chandelier :
**144/144 repli dégradé HOLD**, 0 approbation, rétention 0 %, filtration 100 %. Ollama
0.40.14 répond HTTP 500 `error starting llama-server: llama-server binary not found`.
Ce n'est pas un verdict du modèle. Aucune réparation système tentée ; rejouer après
réparation avant toute conclusion. **Levée le 2026-11-05** : voir l'entrée du 06:30 —
144 appels non dégradés, rétention 30,4 %, filtration 81,0 %, critère de rétention
non satisfait.

**Vérifié** — `pytest -q -o addopts=""` → **570 passed** (1 warning Starlette) ;
`db_migrate.py status` → 9 migrations appliquées, 0 en attente ; golden re-béni après
`replay` montrant des métriques identiques (seuls `created_at` et `risk_fingerprint`
changent). Aucun ordre réel, aucun changement de `.env`, aucun commit.

**Décidé** — D-061. Critères swing révisés : PF ≥ 1,35 net, payoff ≥ 3,5, MDD ≤ 20 %,
N ≥ 50 sur l'historique complet ; abandon du winrate ≥ 40 %. Le mode reste **non
activé**, le live reste fermé.

---

## 2026-11-04 · 18:30 — Moteur swing Daily : la mécanique est prête, l'arête ne l'est pas (D-060)

**Fait** — Un mode `SWING_MACRO_D1` complet, du filtre d'entrée au backtest multi-années.

1. **Entrée D1** : clôture au-dessus de l'EMA50 **et** de l'EMA200, pente EMA50 positive
   sur 5 jours, puis cassure Donchian 20 **ou** rebond de valeur EMA20 avec RSI 50–65.
   `DECISION_TIMEFRAME=1d` est exigé au démarrage.
2. **Risque** : `StopMode.STRUCTURAL_D1` sous le creux des 10 D1, plafonné à 5 % sous
   l'entrée ; breakeven à +1 R (papier) ; sorties EMA20 D1 ou Bollinger (20 ; 2,5) avec
   divergence baissière. Pas de take-profit fixe.
3. **Petit compte** : 100 % d'exposition sous 50 EUR via
   `SMALL_ACCOUNT_FULL_ALLOCATION`, avec un plancher de viabilité qui porte le risque au
   minimum nécessaire pour atteindre le notionnel Kraken (~10 EUR), plafonné à 5 %.
4. **Mesure** : `backtest.py swing` agrège le D1 depuis le H4 en base (journée incomplète
   ignorée, jamais interpolée) et réutilise `evaluate()` + `PaperBroker`.

**Mesuré — 2021-11 → 2026-11, 80 pb/côté, coupe-circuits levés**
BTC `both` : 31 trades (7,2/an), réussite 9,7 %, payoff 4,06, PF 0,44, −126,94 EUR.
ETH `both` : 19 trades, PF 0,57, −55,45 EUR. SOL `both` : 13 trades, PF 0,19, −55,46 EUR.
**Aucun scénario positif sur la fenêtre demandée.** Les 5 trades Donchian d'ETH sont
positifs (PF 2,11) mais l'échantillon est trop petit pour conclure.

**Mesuré — tout l'historique**
BTC `both` : 87 trades (7,1/an), réussite 17,2 %, payoff 10,56, PF 2,20, +148,9 %.
BTC Donchian seul : 37 trades (3,0/an), réussite 35,1 %, PF 4,23, +199,9 %.
ETH `value` : 54 trades (5,2/an), PF 1,67, +36,5 %. La réussite de 40 % n'est jamais
atteinte : c'est un trend-following à faible taux de réussite et fort payoff, pas un
système à 40 % de trades gagnants. Durée moyenne 9,4 j (BTC) / 5,8 j (ETH), dans la
cible 3–15 j. Sorties BTC : 39 stops, 46 EMA20, 2 Bollinger.

**Constat** : le résultat positif vient des cycles 2014–2021. Sur les cinq dernières
années, le mode perd sur les trois paires. Le levier n'est donc pas la cadence D1 seule :
il faut un filtre de régime ou une sortie moins whipsaw que l'EMA20, pas un quatrième
filtre d'entrée.

**Décidé** — D-060. Le mode est implémenté et testé mais **non activé** ; aucun ordre
réel, aucun changement de `.env`.

**Vérifié par** — `pytest -o addopts=""` → **520 passed** ; `backtest.py swing
--symbols BTC/EUR,ETH/EUR,SOL/EUR --months 60 --fee-bps 80 --max-consecutive-losses 9999
--max-hwm-drawdown 1.0` → 9 lignes, aucune OK ; même commande `--months 0` → BTC
Donchian PF 4,23, BTC both PF 2,20, ETH value PF 1,67 ; `replay --bless` → métriques
identiques (seul le fingerprint de risque change, nouveaux champs aux valeurs par
défaut).

**Reste** — mesurer un filtre de régime (BTC vs ETH, breadth) ou une sortie moins
whipsaw ; ne pas figer les seuils sur 31 ou 87 trades ; ne pas activer le réel.

---

## 2026-11-04 · 16:15 — Le repli H1 ne débloque pas N, et le verrou faussait la mesure (D-057 → D-059)

**Fait** — L'hygiène de build, un second mode d'entrée, et la réparation du banc.

1. **Hygiène** : compteur de tests à jour (477 → 497), D-057 (plancher de 100 Mo libres
   sur Supabase) écrite et référencée dans `AGENTS.md` §7, index du code régénéré,
   `check.sh` **vert**.
2. **Second mode d'entrée** (D-058) : `EntryMode` (`EMA50_H4` | `PULLBACK_H1`) choisi
   par `ENTRY_MODE`, défaut inchangé. Le mode H1 exige que le bas de la dernière H1
   touche l'EMA50 H1, que la clôture referme au-dessus, une extension ≤ 1 ATR H1 et un
   RSI H1 ≤ 55. Mêmes garde-fous, même sizing, même pare-feu. `sweep --entry-modes`
   compare les deux modes sur les mêmes bougies.
3. **Banc réparé** (D-059) : `sweep` expose désormais `gel` (rejets par coupe-circuit
   *permanent*) et la date du dernier trade, et sait lever les deux plafonds
   (`--max-consecutive-losses`, `--max-hwm-drawdown`). Sans cela, la fenêtre de 24 mois
   s'arrêtait au premier mauvais mois.

**Mesuré — la bande RSI du brief est vide par construction.** Sur 102 replis BTC, le
RSI(14) H1 d'une bougie qui referme au-dessus de son EMA50 vaut 45 à 60 : **aucune**
occurrence sous 45. La bande 38–45 ne filtre pas, elle supprime. Corrigée en 38–55
(plafond anti-extension) ; la zone de valeur est portée par le contact EMA50.

**Mesuré — 48 scénarios, 24 mois, coupe-circuits levés** (3 paires × 2 modes ×
4 pentes × 2 niveaux de frais) : **aucun positif**. PF de 0,11 à 0,62 ; N de 14 à 39
trades par paire et par 24 mois. Meilleur PF : ETH `EMA50_H4` pente 2,0 → 0,62 sur
18 trades (−79,78 EUR). Le repli H1 améliore BTC à pente égale (PF 0,46 contre 0,29 à
1,5 ; DD 13,2 % contre 21,9 %) mais fait moins bien sur ETH (0,50 contre 0,62) et SOL
(0,34 contre 0,45) : **déplacer le point d'entrée ne change pas le signe**.

**Mesuré — le verrou expliquait une partie des anciens chiffres.** BTC `EMA50_H4`
pente 2,0 : **9 trades** avec `max_consecutive_losses=4` (dernier le 2025-08-02) contre
**21** levé (dernier le 2026-10-15). Les « 2 à 9 trades par paire et par an » de la
session précédente étaient moitié rareté du setup, moitié gel de la mesure.

**Constat** : le seuil de N ≥ 100 n'est pas atteignable par paire à cadence H4. En
cumulant les trois paires à pente 0 (mesures indépendantes, chacune repartant de
1 000 EUR), on totalise **95 trades** (H4) et **87** (H1) sur 24 mois. Le plafond est
la cadence de décision (une opportunité H1 sur quatre est seulement vue), pas la
finesse du filtre.

**Décidé** — D-057 (plancher de stockage), D-058 (deux modes d'entrée, RSI corrigé),
D-059 (balayage avec coupe-circuits levés et gel visible).

**Vérifié par** — `pytest -o addopts=""` → **497 passed** ; `check.sh` → **TOUT EST
VERT** ; `backtest.py sweep --months 24 --entry-modes EMA50_H4,PULLBACK_H1
--max-consecutive-losses 9999 --max-hwm-drawdown 1.0` → 48 lignes, aucune OK ;
`backtest.py replay` → métriques identiques, fingerprint re-bénit (les nouveaux
réglages ne changent aucun comportement par défaut).

**Reste** — mesurer une cadence H1 ou un portefeuille multi-paires ; ne pas empiler un
troisième filtre d'entrée. Le chemin live reste fermé, aucun ordre réel.

---

## 2026-11-04 · 17:20 — 13 ans d'historique, 24 scénarios, zéro rentable (D-053 → D-056)

**Fait** — Les trois chantiers prioritaires du brief, et le quatrième gelé.

1. **Ingestion multi-années (jalon 1)** : `app/market/kraken_archive.py` lit les
   archives OHLCVT de Kraken **par requêtes HTTP Range** (répertoire central + membres,
   ZIP64 géré), `app/market/history.py` analyse le CSV, détecte les trous, agrège
   H1 → H4 et écrit par lots idempotents, `scripts/ingest_history.py` pilote le tout.
   Migration 0008 : `market_candles_history` + `v_history_coverage`, RLS et
   révocation. Aucune hypertable : TimescaleDB n'existe pas sur Supabase 17 et
   158 000 lignes ne justifient pas de partition.
2. **Balayage statistique (jalon 2)** : `backtest.py sweep` — 24 mois, 3 paires,
   pentes 0/1,0/1,5/2,0 ATR, frais 80 et 100 pb, équité virtuelle 1 000 EUR.
   Métriques PF, espérance, drawdown, N, avec verdict par les seuils du brief.
3. **Capital découplé (jalon 3)** : `PAPER_START_EQUITY` passe de 9,37 à **1 000 EUR**
   (existant depuis la phase 1, aucune ligne de code nouvelle) — la formule de sizing
   est enfin éprouvable sans toucher au compte.
4. **Ordres Maker (chantier 4)** : **rien écrit**, gel décidé en 2.10. Optimiser
   l'exécution d'une stratégie qui perd avant frais ne fait que perdre plus lentement.

**Mesuré — historique** (`ingest_history.py --source full`) : BTC/EUR 113 899 H1
depuis 2013-09-10, ETH/EUR 97 407 depuis 2015-08-07, SOL/EUR 47 041 depuis
2021-06-17. Cohérence H1 → H4 : **aucune bougie divergente** sur les trois paires.
Manquantes : 1,18 % BTC, 1,18 % ETH, 0,30 % SOL (concentrées en 2013-2014), plus un
trou de 111 h en H1 entre le 01 et le 05/10/2026 (soudure archive ↔ REST), signalé et
non interpolé.

**Mesuré — balayage 24 mois** (2024-11-04 → 2026-11-04, 4 378 H4 par paire) :

| paire | meilleur scénario | trades | PF | espérance | net | B&H |
|---|---|---|---|---|---|---|
| BTC/EUR | pente 0, 80 pb | 6 | 0,29 | −10,83 | −64,96 | +11,67 % |
| ETH/EUR | pente 2,0, 80 pb | 18 | 0,62 | −4,43 | −79,78 | −7,48 % |
| SOL/EUR | pente 0, 80 pb | 15 | 0,62 | −4,67 | −70,12 | −43,37 % |

**24 scénarios, zéro positif.** PF de 0,00 à 0,62 ; à 100 pb/côté chacun perd 8 à
25 EUR de plus. Aucun n'atteint N ≥ 100 : la règle d'entrée produit 2 à 9 trades par
paire et par an. **Le problème n'est pas la profondeur d'historique, c'est la rareté
du setup.** Le filtre de pente protège relativement sur SOL (B&H −43 % contre −12 %)
mais ne crée d'arête nulle part.

**Décidé** — D-053 (Range plutôt que téléchargement), D-054 (table simple, pas
d'hypertable), D-055 (trous signalés, jamais interpolés), D-056 (balayage avec un
`propose` constant plutôt que `mechanical=True`, qui laisse `trades` vide).

**Vérifié par** — `db_migrate.py apply` → 0008 appliquée ; `ingest_history.py
--source full` → 3 paires, cohérence OK ; `ingest_history.py --report` → tableau
ci-dessus ; `backtest.py sweep --months 24` → 24 lignes, aucune OK ; `pytest
-o addopts=""` → **477 passed** ; `check.sh` → **TOUT EST VERT**.

**Reste** — élargir la règle d'entrée pour obtenir un N interprétable et refaire le
balayage ; ne rien figer sur ces chiffres. Le chemin live reste fermé.

---

## 2026-11-04 · 16:45 — Mandat short : +0,93 % sur un seul trade, pas une preuve (D-052)

**Fait** — `app/backtest/short_lab.py` + `scripts/short_lab.py` : mesure du mandat
short hors production, mêmes bougies, mêmes frais (0,80 %/côté), même slippage. Aucun
ordre, aucune écriture. La règle miroir vend un rallye dans une structure baissière
(prix sous EMA200, EMA50 sous EMA200, pente ≤ 0, RSI 30–65).

**Mesuré** — BTC, fenêtre du golden : 1 trade le 29 octobre, **+0,0871 EUR (+0,93 %)**
avec stop 2,5/TP 1,5 ; **+0,1858 EUR (+1,98 %)** avec stop 3/TP 2 ; la variante
« distance −2..0 ATR » perd −2,91 % sur 2 trades. Buy & hold BTC : −6,03 %.

**Limite dure** — Kraken spot ne permet pas le short. Kraken Futures a un environnement
demo, mais c'est un autre adaptateur (contrats, USD, levier, liquidation, financement) ;
9,37 EUR est trop petit pour ce produit. `SHORT` n'est pas câblé dans le schéma, le
Risk Gateway, le broker papier ni l'orchestrateur. **Un trade ne prouve rien** : le
laboratoire dit seulement que le mandat mérite une mesure multi-régimes, pas qu'il est
rentable.

**Vérifié par** — `short_lab.py` (deux exécutions, sorties ci-dessus) ; `pytest` →
442 tests ; `check.sh` → **TOUT EST VERT**.

---

## 2026-11-04 · 16:35 — Filtre de pente EMA200 : moins de pertes, pas d'arête (D-051)

**Fait** — `MarketSnapshot.ema_200_slope_atr` (pente EMA200 H4 sur 60 bougies, en ATR)
est calculée sans lookahead dans `build_snapshot` ; `RiskConfig.entry_ema200_slope_min`
vaut 2,0 ATR dans les presets et `entry_rule` refuse une pente insuffisante. Le golden a
été ré-enregistré après revue : 44 → 19 décisions dans la zone, 3 → 2 trades,
−0,4811 → −0,3268 EUR.

**Mesuré** — seuils de pente : 0 ATR = 3 trades −5,13 % ; 1–2 ATR = 2 trades −3,49 % ;
3–5 ATR = 1 trade −1,48 %. **Aucun seuil ne produit un PnL positif avec des trades.**
Mesure 7 paires avec le filtre : 2 trades cumulés (BTC), −0,3268 EUR ; les alts ne
produisent plus aucun trade. Le filtre réduit la perte de 32 % mais ne crée pas d'arête.

**Vérifié par** — `pytest` → 442 tests ; `replay --bless` → golden cohérent ; `check.sh`
→ **TOUT EST VERT**.

---

## 2026-11-04 · 16:20 — Le seuil 0,90 supprime les trades, il ne crée pas d'arête

**Mesuré** — `scenario --min-confidence` sur le golden BTC : 0,60 à 0,82 laissent 3
trades et −5,09 à −5,13 % ; 0,85 laisse 3 trades et −5,15 % ; **0,90 ne laisse aucun
trade (0,00 %)**. Un PnL nul sans trade n'est pas une stratégie positive, c'est l'arrêt.
Le filtre H1 positif reste le moins mauvais avec 2 trades et −3,44 %.

**Conséquence** — le point « stratégie positive » n'est pas résolu. Le chemin live est
prêt, mais l'activer maintenant reviendrait à payer des frais pour une entrée sans
arête. Prochain levier : le signal d'entrée, pas le seuil ni la sortie.

---

## 2026-11-04 · 16:10 — Chemin live écrit et testé hors réseau (D-050)

**Fait** — `app/execution/live.py` : achat marché avec stop bracket, attente du fill,
lecture des frais réels, vérification du stop chez Kraken, revente immédiate si le stop
manque, sortie qui annule les protections avant de vendre et réarme le stop si la vente
échoue. `factory.py` choisit papier/live et refuse testnet. `main.py` démarre en live
seulement si la réconciliation est propre. `scripts/reconcile.py --apply --confirm`
liquide les positions non protégées ; `kill_switch.py --confirm-live` et l'API panic
utilisent le broker réel. En live, la boucle lit d'abord le solde et le stop distants :
position plate → elle enregistre la vente réelle sans renvoyer d'ordre ; solde supérieur
à la position → halte ; stop absent → vente d'urgence puis halte.

**Sécurité** — pas de TP exchange tant que l'OCO n'est pas écrit (un TP séparé peut
vendre deux fois) ; le TP reste local. Le broker refuse d'entrer si un solde de base
existe déjà sur la paire : **sous-compte dédié obligatoire**.

**Pas fait** — aucun ordre réel n'a été envoyé. Il reste à créer le sous-compte, choisir
le montant, poser les trois verrous et faire le canary de la phase 3.

**Vérifié par** — `pytest` → 440 tests ; `node --test` → 22 tests ; `node --check` OK ;
`check.sh` → **TOUT EST VERT**.

---

## 2026-11-04 · 15:35 — Choix : assumer la perte nette au stop, sans réduire la taille (D-049)

**Décidé** — après la correction des frais, le risque affiché reste **brut** : la perte
nette au stop inclut les 1,60 % d'aller-retour, soit ~1,4× le budget de 1,75 %. On ne
réduit pas la taille, car plusieurs tickets passeraient sous le minimum Kraken. Le
dashboard affiche désormais « Risque brut » et « Perte au stop (frais inclus) » ;
`/api/status` expose `estimated_net_risk_quote` et `fee_rate`. Les coupe-circuits
restent calculés sur le PnL net.

**Pas fait** — les ordres limit (0,40 %/côté au lieu de 0,80 %) attendent : le backtest
ne modélise pas les fills limit, et l'entrée est déjà négative avant frais. Optimiser
l'exécution ne corrige pas une entrée perdante.

**Vérifié par** — `pytest tests/test_api.py` → 19 tests ; `node --test` → 22 tests ;
`pytest --collect-only` → 422 tests ; `node --check public/app.js` OK. Un `check.sh`
complet suit.

---

## 2026-11-04 · 15:20 — Frais Kraken réels : 0,80 %/côté, pas 0,26 % (D-048)

**Constat** — `fetch_trading_fee("BTC/EUR")` sur le compte renvoie `taker=0.008`,
`maker=0.004`, volume 30 jours 0, prochain palier à 2 500 USD. CCXT exposait
`0.0026/0.0016` : un tarif historique. La grille Kraken du 2026-07-09 confirme le
palier 1 à **0,80 % taker / 0,40 % maker**. Le code utilisait `market['taker']`, donc
**sous-estimait les frais d'un facteur 3**.

**Corrigé** — `resolve_taker_fee()` lit le tarif du compte via `fetch_trading_fee`,
avec repli 0,008 ; `TAKER_FEE_PCT`/`MAKER_FEE_PCT` surchargeables ; `fee_buffer_pct`
Kraken passe à 0,016 (aller-retour 1,60 %). Paper, backtest, shadow, kill-switch et
API utilisent le tarif résolu.

**Mesuré après correction**
```
record BTC 150 bougies   44 décisions (22 BUY) · 3 trades · 0 gagnant
                         −0.4811 EUR (−5.13 %)   [avant : −0.3357]
measure 7 paires         4 trades · 0 gagnant · −0.6951 EUR   [avant : −0.4999]
scénario 3,0 ATR         −0.3603 EUR (−3.85 %)
scénario filtre H1       −0.3226 EUR (−3.44 %) · aucun scénario positif
A/B leçons               −3.49 % sans · −3.45 % avec · non concluant
```

**Conséquence** — à 9,37 EUR, un aller-retour à 1,60 % coûte ~0,064 EUR sur un ticket
de 4 EUR, soit ~39 % du budget de risque (0,164 EUR). Le prochain levier n'est pas la
sortie, c'est l'entrée et le coût des frais. Ne pas passer en réel sans une mesure
positive **nette de frais**.

---

## 2026-11-04 · 15:05 — Premier A/B de la mémoire : non concluant

**Mesuré** — BTC/EUR, 30 bougies H4, prompt avec et sans deux leçons génériques
(recalculé après correction des frais, voir 15:20) :
```
sans leçons   19 décisions · 11 BUY · 2 trades · −0.3268 EUR (−3.49 %)
avec leçons   21 décisions · 14 BUY · 2 trades · −0.3229 EUR (−3.45 %)
```
**Constat** — l'écart (+0,0039 EUR) est dans le bruit ; les deux scénarios perdent,
0 gagnant. Les leçons n'ont pas empêché les achats perdants. Avec 2 trades, on ne
peut pas conclure sur l'utilité de la mémoire — seulement dire que le mécanisme
fonctionne.

**Décision** — ne pas présenter la mémoire comme un gain. Attendre au moins 5
résultats réels, lancer `lessons.py review`, puis refaire l'A/B sur la fenêtre
complète. Le golden reste la référence du prompt de base ; les leçons sont mesurées
par `measure --lessons-file`.

---

## 2026-11-04 · 14:55 — Mémoire de leçons : une relecture, pas un entraînement (D-047)

**Construit**
- migration 0007 `lessons` : une seule version active par mode, expiration 30 jours,
  source des résultats et empreinte du journal.
- `app/llm/lessons.py` : digest des trades clos + shadow trades, validation des
  leçons (1 à 5, 240 caractères max, rejet d'une consigne de contournement), refus
  sous 5 résultats. `SignalClient.review` : un appel séparé, schéma JSON dédié.
- `scripts/lessons.py` : `review` (un appel, `--dry-run`, `--force`, `--show-digest`),
  `show`, `history`, `clear`.
- Injection : `SignalClient.propose(..., lessons=…)`, bloc « Leçons de tes dernières
  décisions » dans le prompt, `lessons_count` et `lessons_sha256` dans le payload
  persisté. Orchestrateur : lecture des leçons actives à chaque cycle.
- Dashboard : panneau « Leçons » ; API `/api/stats` expose la version active.
- A/B : `scripts/backtest.py measure --lessons-file`.

**Vérifié**
```
db_migrate.py apply       → 0007 appliquée (67 ms)
db_migrate.py verify      → 29 relations, 5 vues, 16 partitions
lessons.py review         → 5 shadow trades de test, 5 leçons, version 1
lessons.py show/history   → leçons actives puis purge
pytest --collect-only     → 415 tests
node --test               → 22 tests
```

**Constat** — le premier digest disait « Shadow trades évalués » sans préciser que
ces trades n'avaient pas été pris. Le modèle a écrit des leçons inversées (« les
rejets entraînent des pertes »). Correction : « achats NON pris · PnL contrefactuel ·
un négatif = perte évitée ». La deuxième relecture produit des leçons correctes
(« le rejet CONFIDENCE_TOO_LOW protège le capital »). Le libellé du digest est donc
partie intégrante de la qualité de l'apprentissage.

**Reste** — attendre 5 résultats réels, lancer `lessons.py review`, puis comparer
`measure` avec et sans leçons. Ne pas garder la mémoire si la mesure ne montre pas
un mieux.

---

## 2026-11-04 · 14:40 — Shadow trades : les achats non pris sont mesurés (D-046)

**Construit**
- migration 0006 `shadow_trades` (partitionnée ; `GATE_REJECTED_BUY` / `MODEL_HOLD`,
  plan `OPEN`/`NO_PLAN`, issues `STOP_LOSS`/`TAKE_PROFIT`/`END_OF_DATA`).
- `Orchestrator._record_shadow` : à chaque BUY refusé par le pare-feu et à chaque
  HOLD sur setup d'entrée, le plan est recalculé avec `evaluate(confidence=1.0,
  régime du signal)`. Aucun ordre, aucune position, aucun argent.
- `app/runtime/shadow.py` : `simulate_shadow` (stop prioritaire, frais et slippage
  du broker papier, fin de données) et `evaluate_open_shadow_trades`.
- `scripts/shadow_report.py` : résumé par kind et par code de rejet, `--evaluate`
  pour fermer les lignes `OPEN`.
- API `GET /api/stats` et panneau dashboard « Achats refusés ». D-046.

**Vérifié**
```
db_migrate.py apply       → 0006 appliquée (104 ms)
db_migrate.py verify      → 28 relations, 5 vues, 16 partitions
shadow_report.py          → 0 ligne (table vide au départ)
insert / close / summary  → 1 ligne OPEN puis STOP_LOSS −0.27, nettoyée
pytest --collect-only     → 403 tests
node --test               → 22 tests
```

**Constat** — la table est vide pour l'instant : les 7 derniers signaux sont des HOLD
antérieurs à la fonctionnalité. Elle se remplira aux prochains HOLD ou BUY refusés ;
`--evaluate` donnera alors le contrefactuel. Les lignes `NO_PLAN` diront si le refus
vient du minimum exchange ou de la structure, pas du modèle.

**Reste** — accumuler quelques semaines de lignes, lire `shadow_report.py`, puis
décider si le plancher de confiance ou la règle d'entrée doivent bouger.

---

## 2026-11-04 · 14:30 — Scénarios de sortie : le stop n'est pas le levier

**Construit**
- `scripts/backtest.py scenario` : rejoue le golden avec `--stop-atr`,
  `--take-profit-r`, `--min-confidence` et `--require-positive-h1`, sans écrire et
  sans appeler le LLM. `ReplayLLM` accepte un HOLD de scénario pour les décisions
  absentes, avec un compteur affiché.
- `RiskConfig.entry_require_positive_h1` (défaut `False`) : laboratoire pour exiger
  que la dernière H1 clôturée soit positive. **Pas activé en production.**

**Mesuré** (golden 44 décisions, BTC/EUR, décisions figées)
```
stop 2.5 / TP 2.0    3 trades  −0.3357 EUR  (−3.58 %)  référence
stop 3.0 / TP 2.0    2 trades  −0.2619 EUR  (−2.80 %)  3 décisions manquantes
stop 3.5 / TP 2.0    2 trades  −0.2957 EUR  (−3.16 %)  3 décisions manquantes
stop 2.5 / TP 3.0    3 trades  −0.3357 EUR  (−3.58 %)  TP jamais atteint
stop 3.5 / TP 3.0    2 trades  −0.2957 EUR  (−3.16 %)  TP jamais atteint
stop 4.0 / TP 3.0    2 trades  −0.2842 EUR  (−3.03 %)  13 décisions manquantes
2.5 + filtre H1 +    2 trades  −0.2238 EUR  (−2.39 %)  0 décision manquante
```

**Constat** — aucun scénario ne rend la stratégie positive. Les quatre trades de
référence sortent tous au stop ; le take-profit 2R/3R n'est jamais touché. Le 3,0 ATR
perd moins parce qu'un troisième achat devient impossible (notionnel sous le minimum
Kraken) : effet de sélection, pas un edge. Le filtre H1 positive évite un perdant et
donne −2,39 %, mais sur deux trades seulement. Trois trades ne distinguent pas un
edge d'un hasard.

**Décision** — ne pas changer le stop ou le TP en production. Accumuler des
échantillons (boucle papier, `shadow_trades`) et mesurer une fenêtre plus longue ou
un autre régime avant de figer un filtre. Le laboratoire `scenario` reste disponible.

**Vérifié**
```
pytest --collect-only          → 397 tests
pytest tests/test_backtest_golden.py → 3 verts (fingerprint re-béni, métriques identiques)
scenario 2.5/2.0               → identique au golden (−0.3357)
```

---

## 2026-11-04 · 14:20 — Dashboard pilotable, réglages en base, session 7 jours (D-044, D-045)

**Construit**
- `runtime_settings` (migration 0005) : une ligne par mode, payload JSONB, `updated_at`,
  `updated_by`. Le bot relit la ligne à chaque cycle (`Orchestrator._refresh_settings`) ;
  `.env` reste le point de départ, la base devient la vérité des réglages modifiés.
- `app/api/settings_store.py` : libellés français (Prudent / Équilibré / Audacieux /
  Micro), validation, fusion du payload, liste blanche `RUNTIME_ALLOWED_KEYS`.
- API : `GET /api/settings`, `POST /api/settings`, `GET /api/stats`.
- Dashboard : cartes de résultat, courbe du capital, gain/perte par jour, panneau
  modèle (dernière décision, conviction, latence, répartition, replis), formulaire de
  réglages sans jargon, bandeau d'arrêt avec compte à rebours et bouton « Reprendre
  maintenant ».
- `HALT` du modèle : solde puis `EMERGENCY_HALT` avec `auto_resume_at = now + 24 h` ;
  reprise auto au cycle suivant ou bouton. Un panic-sell humain n'a pas d'échéance.
- Prompt v3 : section « scénario d'achat » (pullback EMA50) et payload enrichi ; le
  modèle est passé de 0 BUY à 23 BUY sur BTC.
- Session : JWT 7 jours (`TOKEN_TTL_SECONDS = 604800`), cookie `tn_session` HttpOnly
  aligné sur le plus court des deux TTL. D-045.

**Vérifié** (commandes réelles)
```
pytest --collect-only          → 397 tests
node --test                    → 22 tests
db_migrate.py status            → 0005 appliquée à 12:49:49
db_migrate.py verify            → 26 relations, 5 vues, 16 partitions
API réelle + base réelle        → GET 200 (0.50), POST 200 (0.35/48 h), base 0.35/48,
                                  orchestrateur applique 0.35/48, combinaison invalide 422,
                                  payload restauré à {}
backtest.py record              → 44 décisions (23 BUY, 21 HOLD), 3 trades approuvés,
                                  3 perdants, −3,58 % vs B&H −6,03 %, golden 44
```

**Constat** — la calibration du prompt débloque l'achat : 36 BUY sur les sept paires,
4 trades approuvés, **0 gagnant**, −0,4999 EUR (−5,3 % du compte). BTC −3,58 % vs
buy & hold −6,03 % ; BNB −1,75 % vs −6,98 % ; ADA a produit 7 BUY mais tous refusés
par `SIZING_BELOW_EXCHANGE_MINIMUM` (le budget de risque est trop petit face au
minimum Kraken). Le prochain levier est la géométrie entrée/sortie (stop 2,5 ATR,
take-profit 2R), pas le seuil de confiance.

**Reste**
- Vérifier l'affichage du dashboard dans un navigateur réel (cartes, SVG, formulaire).
- Rejouer le golden avec `stop_atr_multiple` 3,5 et `take_profit_r_multiple` 3,0 pour
  voir si les quatre stops étaient du bruit H4 ou une mauvaise entrée.
- Étape 2.3 : `shadow_trades` (migration 0006, le numéro 0005 est pris par
  `runtime_settings`).

---

## 2026-11-04 · 14:10 — Le LLM peut acheter, vendre et arrêter (D-043)

**Construit**
- `app/engine/entry_rule.py` : règle pure de pullback EMA50 dans une tendance
  (prix > EMA200 + marge, EMA50 > EMA200, RSI 40-65, ATR dans la bande, pas de couteau).
  Elle décide **quand** le modèle est consulté, pas ce qu'il doit répondre.
- `app/engine/exit_guard.py` : garde-fou pur de sortie. Un SELL ou un HALT du modèle ne
  peut que solder une position existante, jamais créer un short, jamais dépasser la
  quantité détenue. Seuils séparés (0,55 pour SELL, 0,80 pour HALT).
- Action `HALT` dans le schéma LLM et le prompt (v2) ; migration 0004
  (`signal_action.HALT`, `order_purpose.LLM_EXIT`).
- Orchestrateur : le LLM est consulté à chaque bougie clôturée pendant une position ;
  SELL solde au prix courant, HALT solde puis force `EMERGENCY_HALT`. Le bouton rouge
  2FA reste l'override manuel.
- Backtest : `_simulate_trade` bougie par bougie, stop/TP **avant** le LLM, puis
  SELL/HALT à la clôture ; commande `scripts/backtest.py measure` sur les 7 paires.

**Vérifié** (commandes réelles)
```
pytest                        → 386 passed
db_migrate.py apply           → 0004 appliquée (48 ms)
db_migrate.py verify          → 25 relations, 5 vues, 16 partitions, OK
backtest.py record            → golden ré-enregistré : 26 décisions, 0 BUY, 0 trade
backtest.py replay            → 3 tests de régression verts
```

**Constat mesuré** — BTC/EUR, 150 bougies H4, politique règle + LLM :
la règle a déclenché 26 fois, le modèle a répondu **HOLD 26 fois** (confiance 0,40 à
0,90), aucun achat. Le plancher à 0,60 n'est donc plus le blocage : c'est le modèle
lui-même qui refuse le pullback. Le contrefactuel « acheter à chaque refus » perd
−3,50 EUR sur la fenêtre ; ses HOLD étaient justifiés. Le seuil n'est pas le problème,
la calibration du prompt ou le choix de la règle le sont.

**Mesure 7 paires** (BTC, ETH, ADA, DOGE, PAXG, HBAR, BNB ; 150 bougies H4) :
**46 déclenchements, 46 HOLD, 0 BUY, 0 trade**. Le buy & hold est négatif sur les sept
paires (−3,96 % à −25,06 %). Contrefactuels : −3,50 EUR BTC, −0,11 EUR ETH, −0,16 EUR
BNB ; ADA et HBAR n'ont même pas de contrefactuel finançable. Le modèle est capable
d'acheter — il ne le fait pas, et sur cette fenêtre il a eu raison.

**Décidé** : D-043 (le LLM déclenche dans un cadre déterministe, ne dimensionne rien,
SELL/HALT gardés). Le plancher `micro` passe à 0,60 pour rendre un BUY possible ; à
0,85 il était mathématiquement impossible.

**Reste** : calibrer le prompt pour définir explicitement le pullback comme setup
d'achat (ou accepter que le LLM ne trade pas dans ce régime), puis 2.3
(`shadow_trades`, migration 0005) et 2.4. Aucun argent réel, aucun ordre envoyé.

---

## 2026-11-04 · 12:50 — Réconciliation exchange, drawdowns prouvés, une décision par bougie

**Construit**
- `app/runtime/reconciliation.py` : planificateur pur (positions en base, ordres
  ouverts, soldes) qui distingue stop armé, stop absent, stop trop petit ou trop bas,
  et solde insuffisant. `scripts/reconcile.py` exécute l'audit en lecture seule.
- `scripts/verify_drawdowns.py` : jeu de données figé écrit puis annulé, comparaison
  exacte avec un calcul manuel.
- `app/runtime/scheduler.py` et `python -m app.main --serve` : cycles alignés sur les
  clôtures H4. Garde `last_signal_at()` dans l'orchestrateur : une bougie déjà traitée
  est sautée, même après redémarrage.

**Vérifié** (commandes réelles)
```
reconcile.py                        → mode PAPER · positions 0 · ordres distants 0 · 9.37 EUR, exit 0
verify_drawdowns.py                 → 20,00 % / 40,00 % / 60,00 % exacts, transaction annulée
app.main --once --no-warmup         → 7/7 « bougie clôturée à 08:00 UTC déjà évaluée », 0 signal, 0 LLM
app.main --serve --serve-cycles 1   → même résultat, sans erreur
pytest                              → 354 passed
```

**Constat** : `--cycles --interval 60` redemandait la même bougie H4 toutes les
minutes. Corrigé sans migration : le dernier signal en base suffit à savoir qu'une
bougie a été traitée. Le sens de l'erreur reste prudent — manquer une bougie est
acceptable, la trader deux fois ne l'est pas.

**Décidé** : D-040 (la réconciliation constate, l'exécuteur live applique), D-041
(une bougie = une évaluation), D-042 (drawdowns sur transaction annulée).

**Reste** : l'application du plan de réconciliation (liquidation/annulation) exige
l'exécuteur live, qui n'est pas écrit et attend une revue ; puis 2.3 (résultat des
trades refusés) et 2.4 (rapport de phase).

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
