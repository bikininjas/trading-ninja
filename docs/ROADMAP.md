# Feuille de route — Trading Ninja

Statuts : ⬜ à faire · 🟨 en cours · ✅ fait et vérifié · ⛔ bloqué · 🔵 en attente d'une action de Seb

> **Règle de mise à jour** — dès qu'une étape *commence*, la passer de ⬜ à 🟨 avec la
> date du jour, et ajouter une entrée dans `JOURNAL.md`. Une étape ne passe à ✅ que
> lorsqu'elle a été **exécutée et vérifiée** (une commande réelle, pas une intention).
> Toute décision technique prise en route s'inscrit dans `DECISIONS.md`.

---

## Vue d'ensemble

| Phase | Objectif | Statut | Avancement |
|---|---|---|---|
| **0. Fondations** | Le socle sur lequel rien ne peut perdre d'argent par accident | ✅ | 9 / 9 |
| **1. Moteur** | Le bot existe et tourne, mais sans argent | 🟨 | 6 / 11 |
| **2. Simulation** | Prouver la mécanique sur des centaines de trades fictifs | 🟨 | 2 / 4 |
| **3. Testnet** | Prouver l'exécution réelle avec du capital fictif | ⬜ | 0 / 3 |
| **4. Argent réel** | Engager le minimum, profil prudent, sous surveillance | ⬜ | 0 / 3 |

Le principe qui gouverne l'ordre : **on ne passe à la phase suivante que sur un
critère chiffré**, jamais sur une impression. Une phase peut échouer — c'est un
résultat acceptable, et c'est même la façon la moins chère d'apprendre.

---

## Phase 0 — Fondations ✅

Objectif : que le système soit *incapable* de perdre gros même s'il est mal utilisé.

- ✅ **0.1 — Réconcilier les trois specs** *(2026-11-04)*
  Trois documents se contredisaient (codes de rejet, noms de champs, états, formules).
  Tableau d'autorité établi : le DDL PostgreSQL fait foi, les ENUMs Python le miroitent
  à l'identique.
  *Validé par* : plus aucun code de rejet divergent entre `risk_gate.py` et la base.

- ✅ **0.2 — Pare-feu déterministe** *(2026-11-04)*
  `backend/app/engine/risk_gate.py` : module pur, sans I/O ni horloge implicite, 15
  étapes de filtrage ordonnées, 23 codes de rejet. `evaluate()` ne lève jamais.
  *Validé par* : `.venv/bin/python -m pytest tests/test_risk_gate.py` → 129 passed.

- ✅ **0.3 — Configuration et presets de risque** *(2026-11-04)*
  Presets `prudent` / `standard` / `aggressive` pilotés par une variable, chaque
  réglage surchargeable, plafonds durs refusés au démarrage.
  *Validé par* : `settings.py` + 10 tests dédiés.

- ✅ **0.4 — Schéma PostgreSQL** *(2026-11-04)*
  9 tables, 2 partitionnées par mois, 5 vues, 2 migrations appliquées sur Supabase.
  *Validé par* : `scripts/db_migrate.py verify` → schéma conforme, écriture validée.

- ✅ **0.5 — Protection des secrets** *(2026-11-04)*
  `.gitignore` + crochet `pre-commit` + test réel des deux.
  *Validé par* : `.env.local` forcé dans l'index → commit refusé (exit 1).

- ✅ **0.6 — Outillage à jour** *(2026-11-04)*
  Python 3.15.2, Node 26.13.0 LTS, npm 11.20.0, uv 0.13.7. Système intact.
  *Validé par* : versions mesurées dans un shell neuf + suite de tests sur 3.15.

- ✅ **0.7 — Fermer l'accès public Supabase** *(2026-11-04)*
  Les vues contournaient la RLS et exposaient `bot_state` à la clé publique.
  Droits révoqués, vues en `security_invoker`.
  *Validé par* : `SET LOCAL ROLE anon` → privilège refusé sur les 10 relations.

- ✅ **0.8 — Documentation de reprise** *(2026-11-04)*
  `AGENTS.md` (règles et pièges), `docs/ARCHITECTURE.md` (couches, modules, modèle de
  données), bannière « périmé » sur les trois specs d'origine, test de contrat
  schéma↔code, et `scripts/check.sh` qui vérifie l'état en une commande.
  *Validé par* : `bash scripts/check.sh` → 167 tests, tout vert.

- ✅ **0.9 — Audit à froid et corrections** *(2026-11-04)*
  Un agent sans contexte a relu le dépôt comme s'il le reprenait. Verdict « prêt avec
  réserves », 18 contradictions documentaires et 4 vrais défauts trouvés, tous corrigés :
  verrou sandbox manquant (`testnet` + `EXCHANGE_SANDBOX_MODE=false` chargeait sans
  erreur), `evaluate()` qui levait sur deux entrées malformées, `risk_evaluations` qui
  interdisait de journaliser les refus précoces, `bot_state` unique tous modes confondus.
  *Validé par* : 167 tests, migration 0003 appliquée, `v_system_health` à 3 lignes.
  *Détail* : `docs/JOURNAL.md`, entrée « Audit à froid ».

---

## Phase 1 — Moteur 🟨

Objectif : le bot tourne en boucle complète, en mode papier uniquement. Le moteur papier
fonctionne de bout en bout (données réelles → LLM local → pare-feu → exécution simulée
persistée) ; l'envoi réel et l'API d'administration restent à faire.

- 🟨 **1.1 — Vérifier les permissions de la clé Binance** *(2026-11-04)*
  Au démarrage, interroger l'exchange et **refuser de démarrer** si la clé autorise les
  retraits. C'est le garde-fou n°1 contre une fuite de clé.
  Endpoint : `GET /sapi/v1/account/apiRestrictions` (signé), champs `enableWithdrawals`,
  `enableSpotAndMarginTrading`, `enableReading`. Si l'appel échoue ou si le compte n'a pas
  le droit de lire ses propres restrictions, on **refuse aussi** (fail-closed) : on ne
  suppose jamais qu'une clé est saine parce qu'on n'a pas pu la vérifier. Sur testnet,
  l'endpoint peut ne pas exister — dans ce cas, l'absence de réponse est traitée comme un
  refus en mode `live` uniquement.
  *Fait* : `backend/scripts/check_exchange.py`, sonde lecture seule, signature HMAC,
  recalcul du décalage d'horloge, verdict fail-closed. Clé **mainnet** vérifiée :
  `enableReading=true`, `enableSpotAndMarginTrading=true`, `enableWithdrawals=false`,
  `ipRestrict=true`, compte `SPOT` vide. Filtres BTC/USDT lus et convertis en
  `ExchangeConstraints` (tick 0,01 · pas 0,00001 · min_notional 5 USDT).
  *Reste* : porter ce contrôle dans un point d'entrée qui refuse le boot — il n'existe
  pas encore de point d'entrée (étape 1.2 / 2.1).
  *Critère* : une clé avec `enableWithdrawals=true` fait échouer le boot avec un message
  explicite ; une clé lecture+trade passe ; une erreur réseau refuse aussi.
  *Dépendance levée* : les clés fournies sont **mainnet**, pas testnet (voir `JOURNAL.md`).
  Une clé testnet dédiée reste à créer pour la phase 3.

- ✅ **1.2 — Machine à états persistée** *(2026-11-04)*
  `IDLE → EVALUATING → EXECUTING → IN_POSITION → POST_TRADE_SWEEP → COOLDOWN`, plus
  `LOCKED_CIRCUIT_BREAKER` et `EMERGENCY_HALT`. État lu et écrit dans `bot_state`.
  *Fait* : `app/engine/machine.py` (graphe explicite, transition interdite refusée
  **avant** écriture), `bot_state` + `system_state_logs` via `app/db/repository.py`,
  reprise du cooldown et normalisation des états `EVALUATING`/`EXECUTING` après arrêt.
  *Critère vérifié* : un redémarrage en `COOLDOWN` reprend le décompte et ne trade pas
  (`test_cooldown_is_resumed_after_a_restart`).

- 🟨 **1.3 — Broker papier** *(2026-11-04)*
  Exécution simulée : prix de remplissage, frais, slippage, stop déclenché au tick.
  *Fait* : `app/execution/paper.py` — slippage 5 bps, frais taker du marché, sortie sur
  bougie clôturée, gap traité au prix d'ouverture, stop prioritaire si stop et TP sont
  touchés dans la même bougie. Ordres persistés (`ENTRY` rempli, `STOP_LOSS` armé).
  *Reste* : la ligne `profit_sweeps` (1.9) et le TP comme ordre frère annulable.
  *Critère* : un trade gagnant simulé écrit la même séquence de lignes qu'un trade réel
  (`llm_signals` → `risk_evaluations` → `orders` → `positions` → `profit_sweeps`), avec
  `mode = 'PAPER'`. Un trade **perdant** écrit la même séquence **sauf** `profit_sweeps`
  — la contrainte `CHECK (realized_net_pnl > 0)` l'interdit, et c'est voulu : l'absence de
  ligne d'écrémage est le résultat attendu, à vérifier explicitement.

- ✅ **1.4 — Flux de marché et indicateurs** *(2026-11-04)*
  Bougies H4 et H1, EMA200(H4), ATR14(H4), ATR14(H1), rendement de la dernière H1 en
  multiples d'ATR. Uniquement sur bougies **clôturées**.
  *Fait* : `app/market/feed.py` — EMA200/EMA50 H4, ATR14 H4 et H1, rendement H1 en ATR,
  RSI14, histogramme MACD, déséquilibre du carnet. `test_feed.py` fige ATR = 1 et
  RSI = 100 sur des séries construites, et vérifie qu'une bougie en cours ne modifie
  jamais l'indicateur.
  *Critère vérifié* : valeurs de référence exactes, bougie en cours ignorée.

- ✅ **1.5 — Client LLM et validation** *(2026-11-04)*
  Client OpenAI-compatible (Ollama/vLLM), `response_format` JSON schema, validation
  stricte, un seul nouvel essai, bascule sur `HOLD` en cas d'échec. Timeout 5 s.
  *Fait* : `app/llm/client.py` — API **native** Ollama (seule à accepter `think=false`
  et un schéma de décodage contraint), validation stricte, HOLD de repli à confiance
  nulle, audit dans `llm_signals`, rétrogradation déterministe d'un BUY sous EMA200 ou
  avec RSI > 70. Le modèle ne voit ni solde ni clé.
  *Écart assumé* : le 14B du cahier des charges n'est pas installé ; `qwen3.5:9b`
  répond en 1,0-1,5 s. Délai porté à 8 s (4 s produisait 100 % de replis).
  *Critère vérifié* : JSON malformé, champ manquant, timeout et réponse hors schéma →
  tous convergent vers un HOLD, jamais vers un ordre (`test_llm.py`).

- 🟨 **1.6 — Exécuteur CCXT (Kraken spot)** *(2026-11-04)*
  Achat marché avec stop-loss conditionnel attaché (`close`, `ordertype=stop-loss`),
  pas de fenêtre nue entre l'entrée et la protection. TP en ordre séparé, avec
  annulation du frère à écrire. Si le stop est refusé → vente d'urgence et
  `EMERGENCY_HALT`.
  *Fait* : `app/exchange/kraken.py` — marchés → `ExchangeConstraints` en `Decimal`,
  formatage sans dépassement, construction du bracket exigeant une `RiskDecision`
  approuvée, vérification des permissions par `GetApiKeyInfo` (20 tests hors réseau).
  `app/engine/affordability.py` et `check_kraken.py --scan` calculent l'équité minimale
  par paire. Avec 9,37 EUR et le profil `micro`, 7 paires sont tradables (BTC, ETH, ADA,
  DOGE, PAXG, HBAR, BNB) ; les alts les plus volatiles exigent 12 à 27 EUR.
  *Écarté* : Binance (`STOP_LOSS_LIMIT`), le paramètre `stopPrice` du brief (inexistant
  dans CCXT 4.5.91), et le TP dans le bracket (un seul `close` par ordre).
  *Reste* : l'envoi réel et la réconciliation (1.7).
  *Critère* : sur un compte réel, un achat suivi d'un stop vérifié sur l'exchange ;
  stop refusé → position soldée et bot halte.
  *Détail* : `docs/KRAKEN.md`.

- 🟨 **1.7 — Réconciliation au démarrage** *(2026-11-04)*
  Relire solde + ordres ouverts, retrouver la position, vérifier que le stop est
  toujours armé. Stop absent → liquidation immédiate.
  *Fait* : une position `OPEN` en base est retrouvée et surveillée ; `EVALUATING` et
  `EXECUTING` résiduels sont normalisés sans double entrée. Côté exchange,
  `app/runtime/reconciliation.py` lit les ordres ouverts et les soldes, puis produit
  un plan : stop armé (OK), stop absent / trop petit / trop bas (liquidation), solde
  insuffisant (arrêt sans vente, on ne vend pas ce qu'on n'a pas). `scripts/reconcile.py`
  exécute cet audit en lecture seule, sans écrire ni envoyer.
  *Validé par* : audit réel `scripts/reconcile.py` → `mode PAPER · positions 0 ·
  ordres distants 0 · solde 9.37 EUR`, exit 0 ; 15 tests hors réseau.
  *Reste* : appliquer le plan (liquidation, annulation) via l'exécuteur live, qui
  n'existe pas encore ; vérifier un stop réellement armé exige un ordre, donc la
  phase 3. Sans objet en papier, obligatoire avant le réel.
  *Critère* : tuer le process en position ouverte, le relancer, il retrouve la position
  et son stop sans rien dupliquer.

- ✅ **1.8 — Instantanés d'équité et drawdowns** *(2026-11-04)*
  Écriture périodique dans `equity_snapshots`, calcul des drawdowns 24 h / 7 j / plus
  haut historique utilisés par le pare-feu.
  *Fait* : snapshot à chaque cycle et à chaque entrée/sortie, drawdowns calculés en SQL
  et injectés dans le pare-feu, arithmétique du drawdown testée.
  *Validé par* : `scripts/verify_drawdowns.py` écrit un jeu figé dans une transaction
  annulée et compare les trois valeurs au calcul manuel : pic jour 12,00 → 20,00 %,
  pic 7 jours 16,00 → 40,00 %, HWM 24,00 → 60,00 %. Intégré à `scripts/check.sh`.
  *Critère vérifié* : les trois valeurs calculées à la main correspondent exactement
  à celles produites par le SQL, sur une base réelle.

- ✅ **1.9 — Profit sweeper** *(2026-11-04)*
  À la clôture gagnante : 30 % vers le compte réserve, avec seuil minimum de transfert
  et statut explicite (`SKIPPED_BELOW_MINIMUM` plutôt que faux succès).
  *Fait* : `app/engine/sweep.py` (calcul pur, testé), écriture dans `profit_sweeps`,
  réserve déduite du capital de trading puis réinjectée dans l'équité
  (`SUM(swept_amount)` des statuts `SIMULATED`/`SUCCESS`). En papier le statut est
  `SIMULATED` : aucun virement, le ledger reste équilibré.
  *Critère vérifié* : 30 % de 0,20 EUR = 0,06 EUR, sous le seuil de 1,00 EUR → ligne
  `SKIPPED_BELOW_MINIMUM`, `swept_amount = 0`, date de report. Une perte n'écrit
  aucune ligne (contrainte SQL).

- ✅ **1.10 — API d'administration** *(2026-11-04)*
  FastAPI : login + TOTP, start/stop/panic-sell protégés par 2FA, flux WebSocket pour
  le dashboard, métriques et logs d'audit.
  *Fait* : `app/api/security.py` (scrypt, JWT HS256 15 min, TOTP ±1 pas avec anti-rejeu,
  limiteur 5 tentatives / 5 min), `app/api/main.py` (login, status, pause, resume,
  panic, historique, WebSocket), `app/api/service.py` (sérialisation sans flottants).
  Swagger désactivé, démarrage refusé sans les trois secrets.
  *Critère vérifié* : panic sans TOTP frais → 403, avec TOTP → liquidation ; session
  expirée → 401 ; login réel 200 avec `expires_in = 900`.
  *Fait aussi* : dashboard Node.js zéro dépendance (`dashboard/`) — proxy de l'API,
  cookie `HttpOnly`, SSE, 18 tests. Le jeton reste côté serveur. `scripts/manage.sh`
  pilote les deux composants (start/stop/restart, PID vérifiés avant `kill`).
  *Reste* : le panic-sell réel.

- 🟨 **1.11 — Kill-switch autonome** *(2026-11-04)*
  Script indépendant du processus principal, qui parle directement à l'exchange : annule
  tous les ordres ouverts et solde la position. Utilisable depuis un terminal.
  *Fait* : `scripts/kill_switch.py` — connexion propre, fonctionne backend arrêté,
  liquide au prix courant (slippage + frais), annule le stop armé, écrit un ordre
  `EMERGENCY_EXIT`, passe la position en `EMERGENCY_LIQUIDATED` et force `bot_state`
  en `EMERGENCY_HALT`. `--dry-run` montre sans modifier.
  *Reste* : le chemin réel (annulation et vente sur Kraken), refusé tant que
  l'exécuteur live n'est pas revu.
  *Critère* : exécuté alors que le backend est arrêté, il solde quand même la position.
  (Vérifié en papier ; réel non câblé.)

---

## Phase 2 — Simulation papier 🟨

Objectif : mesurer, pas espérer.

- ✅ **2.1 — Boucle de décision automatique** *(2026-11-04)*
  Une évaluation par bougie H4 clôturée, pas une par minute. `app/runtime/scheduler.py`
  calcule la prochaine clôture (alignée sur l'époque Unix) et `python -m app.main
  --serve` y aligne ses cycles. Le garde `last_signal_at()` compare le dernier signal
  en base à l'heure de clôture : une bougie déjà traitée est sautée, y compris après
  un redémarrage.
  *Validé par* : `python -m app.main --once --no-warmup` → 7 paires « bougie clôturée à
  08:00 UTC déjà évaluée », 0 signal, 0 appel LLM ; `--serve --serve-cycles 1` → même
  résultat sans erreur ; 8 tests de cadence.

- ✅ **2.2 — Journalisation de toutes les décisions**, y compris les refus et leur code
  *Fait* : tout HOLD est écrit dans `llm_signals` (et n'entre pas dans le pare-feu,
  D-030) ; tout BUY est écrit dans `risk_evaluations` avec `rejection_code`,
  `rejection_details` et les valeurs normalisées ; un repli LLM est marqué `(repli)`.
  *Validé par* : `test_hold_never_reaches_the_risk_gate`,
  `test_counter_trend_buy_is_refused_by_the_gate` et le cycle réel du 2026-11-04
  (7 signaux persistés, 0 évaluation car tous HOLD).

- ⬜ **2.3 — Suivre le résultat qu'aurait eu chaque trade accepté comme chaque trade refusé**
- ⬜ **2.4 — Rapport de fin de phase** (nombre de trades, taux de réussite, espérance,
      drawdown maximal, répartition des codes de rejet)

**Critère de passage en phase 3** : au moins **100 trades simulés**, drawdown maximal
sous le seuil configuré, espérance positive. En dessous, on corrige ou on abandonne —
les deux sont des résultats utiles.

---

## Phase 3 — Testnet ⬜

Objectif : prouver la mécanique avec du capital fictif mais un vrai exchange.

- ⬜ **3.1 — Cycle complet sur testnet** (achat → stop → sortie → sweep)
- ⬜ **3.2 — Tests de panne** : coupure réseau, redémarrage, ordre rejeté, stop refusé
- ⬜ **3.3 — Vérifier que le stop survit à l'arrêt du PC** (il vit chez l'exchange)

**Critère de passage** : un cycle complet sans incident mécanique, et chaque scénario de
panne s'est terminé sans position nue.

---

## Phase 4 — Argent réel ⬜

- ⬜ **4.1 — Choisir le montant** que Seb accepte de perdre **entièrement**
- ⬜ **4.2 — Clés mainnet dédiées** : retraits désactivés, restriction IP, sous-compte
- ⬜ **4.3 — Profil `prudent`, surveillance quotidienne du premier mois**

**Règle** : le montant engagé est celui dont la perte totale ne change rien à la vie de
Seb. Le risque par trade (0,5 %) reste inchangé quel que soit le capital.

---

## Ce qui attend une action de Seb

| # | Question | Impact |
|---|---|---|
| 1 | ✅ **Répondu le 2026-11-04** : clés **mainnet**, lecture + trading spot, retraits désactivés, IP restreinte. **Kraken spot n'a pas de testnet** dans CCXT 4.5.91 : la phase 3 devra être redéfinie (ordres minuscules en réel, ou exchange de simulation) | Sans cette redéfinition, la phase 3 telle qu'écrite est infaisable |
| 2 | Quel montant acceptes-tu de perdre **entièrement** ? | Détermine la taille de tout le reste (phase 4) |
| 3 | Ollama est-il installé sur la machine, et avec quel modèle ? | Détermine la phase 1.5 (le LLM local) |
| 4 | ✅ **Résolu le 2026-11-04** : clé Kraken remplacée puis vérifiée — 7 permissions, aucun droit de retrait, IP restreinte. Le garde-fou passe | — |
| 5 | Créer une **seconde paire de clés Kraken** pour le kill-switch, ou accepter que le kill-switch ne puisse pas signer en parallèle du bot (nonces strictement croissants par clé) | Sans elle, le kill-switch réel ne peut pas tourner pendant que le bot tourne |
