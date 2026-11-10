# Feuille de route — Trading Ninja

Statuts : ⬜ à faire · 🟨 en cours · ✅ fait et vérifié · ⛔ bloqué · 🔵 en attente d'une action de Seb

> **Règle de mise à jour** — dès qu'une étape *commence*, la passer de ⬜ à 🟨 avec la
> date du jour, et ajouter une entrée dans `JOURNAL.md`. Une étape ne passe à ✅ que
> lorsqu'elle a été **exécutée et vérifiée** (une commande réelle, pas une intention).
> Toute décision technique prise en route s'inscrit dans `DECISIONS.md`.

---

**Décision au 9 novembre 2026 : NO-GO.** La revue de `master` 39db447 a
reproduit des défauts bloquants, dont un appel d’annulation exchange en PAPER.
Le schéma cible est encore en 0009. Voir
[AUDIT_PRE_GO_LIVE_2026-11-09.md](AUDIT_PRE_GO_LIVE_2026-11-09.md) et ses portes G0–G6.

## Corrections pré go live — 10 novembre 2026

- ✅ **Lots 1–4 : sécurité et qualification de la version candidate.**
  Lots 1–4 publiés sur master avec SHA distant vérifié ; lot 4 validé
  (738 Python, 31 Node, navigateur), CI GitHub réussie (run 42771455250).
  État détaillé : [CORRECTIONS_PRE_GO_LIVE.md](CORRECTIONS_PRE_GO_LIVE.md).
  La cible SQL, l’exploitation et la qualification économique restent distinctes.

- ✅ **Lot 5 : compte rendu des liquidations durables** *(2026-11-10)*.
  Fills retournés à l’opérateur et pertes consécutives conservés, absence
  de faux snapshot de portefeuille vide après liquidation partielle testée ; master 50b4304.

- ✅ **Lot 6 : interruption réelle de processus sur PostgreSQL jetable** *(2026-11-10)*.
  Durabilité PREPARED/ACKNOWLEDGED/FILLED, verrou commun et blocage de reprise
  après arrêt brutal vérifiés, sans exchange. Suite complète : 744 Python dont
  35 contrats SQL. La matrice intégrale de pannes reste à qualifier en R2.

## Vue d'ensemble

| Phase | Objectif | Statut | Avancement |
|---|---|---|---|
| **0. Fondations** | Le socle sur lequel rien ne peut perdre d'argent par accident | ✅ | 9 / 9 |
| **1. Moteur** | Le bot existe et tourne, mais sans argent | 🟨 | 6 / 12 |
| **2. Simulation** | Prouver la mécanique sur des centaines de trades fictifs | 🟨 | 13 / 21 |
| **3. Pré-live** | Prouver l'exécution réelle avec un canary minuscule (Kraken n'a pas de testnet) | 🟨 | 1 / 3 |
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
persistée). L'API d'administration et le chemin d'exécution réel sont écrits ;
le réel reste désactivé et sa validation sur un compte dédié reste à faire.

- 🟨 **1.1 — Vérifier les permissions de la clé Kraken** *(2026-11-04 ; état relu le 2026-11-09)*
  Kraken spot est l'exchange retenu. `app/exchange/kraken.py::verify_key_permissions()`
  interroge `GetApiKeyInfo` et refuse les droits de retrait, les permissions de
  trading manquantes et les réponses illisibles. `scripts/check_kraken.py` expose
  cette sonde en lecture seule ; détails dans `docs/KRAKEN.md`.
  *Fait dans le code* : `app.main` appelle déjà `verify_key_permissions()` avant
  la réconciliation live (confirmé par la revue de 39db447).
  *Reste* : qualifier le comportement avec la clé de la machine cible ; le contrôle
  n’est pas une autorisation d’activation et ne ferme pas les défauts de l’audit.
  *Critère* : retrait autorisé, permission manquante ou erreur de lecture → refus
  de démarrer ; clé lecture+trade sans retrait → démarrage autorisé si les autres
  verrous sont satisfaits.
  *Historique* : `scripts/check_exchange.py` et les mesures Binance du journal
  concernent l'ancien exchange. Aucune clé testnet Kraken n'est à créer : Kraken
  spot n'a pas de testnet CCXT ; la phase 3 est un pré-live canary.

- ✅ **1.2 — Machine à états persistée** *(2026-11-04)*
  `IDLE → EVALUATING → EXECUTING → IN_POSITION → POST_TRADE_SWEEP → COOLDOWN`, plus
  `LOCKED_CIRCUIT_BREAKER` et `EMERGENCY_HALT`. État lu et écrit dans `bot_state`.
  *Fait* : `app/engine/machine.py` (graphe explicite, transition interdite refusée
  **avant** écriture), `bot_state` + `system_state_logs` via `app/db/repository.py`,
  reprise du cooldown et normalisation des états `EVALUATING`/`EXECUTING` après arrêt.
  *Critère vérifié* : un redémarrage en `COOLDOWN` reprend le décompte et ne trade pas
  (`test_cooldown_is_resumed_after_a_restart`).

- 🟨 **1.3 — Broker papier** *(2026-11-04)*
  Exécution simulée : prix de remplissage, frais, slippage, stop sur bougie clôturée.
  *Fait* : `app/execution/paper.py` — slippage 5 bps, frais taker du marché, sortie sur
  bougie clôturée, gap traité au prix d'ouverture, stop prioritaire si stop et TP sont
  touchés dans la même bougie. Ordres persistés (`ENTRY` rempli, `STOP_LOSS` armé).
  *Fait aussi* : écrémage des clôtures gagnantes (1.9).
  *Reste* : le TP comme ordre frère annulable.
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
  Client Ollama natif, sortie JSON contrainte et validation stricte,
  bascule sur `HOLD` en cas d'échec. Timeout 8 s.
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
  *Fait aussi* : `app/execution/live.py` envoie le bracket, vérifie le stop et
  prévoit une sortie d'urgence si la protection manque. Aucun ordre réel envoyé.
  *Reste* : valider ce chemin sur un compte dédié (1.7 et phase 3) et compléter
  le TP distant avec annulation du frère.
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
  exécute cet audit en lecture seule par défaut. `--apply` applique le plan ;
  en live, `--confirm` est aussi obligatoire. `app.main` refuse le démarrage live
  si la réconciliation n'est pas propre.
  *Validé par* : audit réel `scripts/reconcile.py` → `mode PAPER · positions 0 ·
  ordres distants 0 · solde 9.37 EUR`, exit 0 ; 15 tests hors réseau.
  *Reste* : valider l'application du plan et la présence d'un stop réellement
  armé sur le compte dédié en phase 3. Le chemin existe ; sa présence dans le
  code ne constitue pas une validation de l'exécution réelle.
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
  *Fait* : `app/api/security.py` (scrypt, JWT HS256 7 jours, TOTP ±1 pas avec anti-rejeu,
  limiteur 5 tentatives / 5 min), `app/api/main.py` (login, status, pause, resume,
  panic, historique, WebSocket), `app/api/service.py` (sérialisation sans flottants).
  Swagger désactivé, démarrage refusé sans les trois secrets.
  *Critère vérifié* : panic sans TOTP frais → 403, avec TOTP → liquidation ; session
  expirée → 401 ; login réel 200 avec `expires_in = 604800`.
  *Fait aussi* : dashboard Node.js zéro dépendance (`dashboard/`) — proxy de l'API,
  cookie `HttpOnly`, SSE, 26 tests à ce jour. Le jeton reste côté serveur. `scripts/manage.sh`
  pilote les deux composants (start/stop/restart, PID vérifiés avant `kill`).
  *Fait aussi* : chemin de panic-sell réel écrit (D-063), sans ordre réel envoyé.
  *Reste* : valider l'exécution réelle sur compte dédié et résoudre les défauts
  live rappelés en D-069 avant activation.

- 🟨 **1.11 — Kill-switch autonome** *(2026-11-04)*
  Script indépendant du processus principal, qui parle directement à l'exchange : annule
  tous les ordres ouverts et solde la position. Utilisable depuis un terminal.
  *Fait* : `scripts/kill_switch.py` — connexion propre, fonctionne backend arrêté,
  liquide au prix courant (slippage + frais), annule le stop armé, écrit un ordre
  `EMERGENCY_EXIT`, passe la position en `EMERGENCY_LIQUIDATED` et force `bot_state`
  en `EMERGENCY_HALT`. `--dry-run` montre sans modifier.
  *Fait aussi* : chemin réel avec `--confirm-live` (D-063), non activé.
  *Reste* : revoir les défauts live rappelés en D-069 et valider sur compte dédié.
  *Critère* : exécuté alors que le backend est arrêté, il solde quand même la position.
  (Vérifié en papier ; chemin réel écrit, non validé sur exchange.)

- 🟨 **1.12 — Pilotage et réglages depuis le dashboard** *(2026-11-04)*
  Le dashboard montre les statistiques, les graphiques, l'état du modèle et permet de
  modifier les réglages sans jargon financier.
  *Fait* : `stats.py` et `settings_store.py` côté API, table `runtime_settings`
  (migration 0005), panneaux HTML/JS (courbe du capital, gain/perte par jour, état du
  modèle, formulaire de réglages), reprise automatique 24 h après un `HALT` du modèle,
  bouton « Reprendre maintenant ». D-044.
  *Fait aussi (2026-11-04)* : jauges de risque (jour, semaine, plus haut, pertes
  consécutives, ordres/heure), refus du pare-feu avec libellés français, onglets
  d'activité (signaux, pare-feu, ordres, positions) via un proxy `/api/history` en
  liste blanche et borné à 50 lignes, périodes 24 h / 7 j / 30 j / tout et infobulles
  sur la courbe, mode `PAPER`/`LIVE` visible, accessibilité (lien d'évitement, focus,
  onglets clavier, `aria-live`), `docs/DASHBOARD.md` et 26 tests Node. D-062.
  *Reste* : valider sur le navigateur de Seb (un smoke test Chromium headless a déjà
  vérifié le rendu, les quatre périodes, l'infobulle, les onglets et l'absence
  d'erreur JS), et décider si les réglages doivent demander un TOTP frais
  (aujourd'hui : session 2FA seulement).
  *Critère* : une modification dans le dashboard change la ligne `runtime_settings`,
  le bot la relit au cycle suivant, et les graphiques reflètent les données réelles.

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

- 🟨 **2.3 — Suivre le résultat qu'aurait eu chaque trade accepté comme chaque trade refusé** *(2026-11-04)*
  *Fait* : table `shadow_trades` (migration 0006), enregistrement des BUY refusés par
  le pare-feu et des HOLD sur setup valide, plan contrefactuel recalculé avec une
  confiance forcée à 1,0, évaluation des lignes `OPEN` par
  `scripts/shadow_report.py --evaluate` (stop/TP/fin de données, frais et slippage),
  panneau « Achats refusés » dans le dashboard. D-046.
  *Validé par* : `db_migrate.py apply` → 0006 appliquée ; `verify` → 28 relations ;
  `shadow_report.py` → 0 ligne au départ ; insertion/fermeture réelle en base puis
  nettoyage ; 403 tests Python.
  *Reste* : accumuler des lignes (le bot en crée à chaque HOLD ou BUY refusé) et lire
  le rapport après quelques semaines.
  → Passer le statut en ✅ quand une première lecture réelle existe.
- ⬜ **2.4 — Rapport de fin de phase** (nombre de trades, taux de réussite, espérance,
      drawdown maximal, répartition des codes de rejet)

- 🟨 **2.5 — LLM autonome : BUY / SELL / HALT** *(2026-11-04)*
  Le modèle peut acheter, vendre pour solder une position et arrêter le bot. Il ne
  dimensionne toujours rien : la règle d'entrée déterministe choisit le contexte, le
  pare-feu calcule taille et stop, et un garde-fou de sortie interdit toute vente à
  découvert.
  *Fait* : `app/engine/entry_rule.py` (pullback EMA50 dans une tendance),
  `app/engine/exit_guard.py` (SELL/HALT sur position existante), action `HALT` dans le
  schéma LLM, migration 0004 (`signal_action.HALT`, `order_purpose.LLM_EXIT`),
  orchestrateur (vente et arrêt demandés par le modèle), backtest multi-paires
  (`scripts/backtest.py measure`). Seuil `micro` ramené à 0,60 pour rendre un BUY
  possible ; D-043. Filtre de pente EMA200 (D-051) : une tendance qui s'essouffle
  n'est plus achetée ; 3 → 2 trades, −0,4811 → −0,3268 EUR.
  *Mesure v3* : 33 BUY sur 7 paires, 4 trades approuvés, 0 gagnant, −0,6951 EUR
  (frais Kraken réels 0,80 %/côté ; l'ancien −0,4999 utilisait 0,26 %).
  Les 4 sorties sont des stops ; aucune sortie SELL/HALT. Le laboratoire
  `scripts/backtest.py scenario` a testé stop 3,0/3,5/4,0 ATR, TP 3R et un filtre
  « H1 positive » : **aucun scénario n'est positif**. Le 3,0 ATR perd moins seulement
  parce qu'un achat devient sous le minimum Kraken (effet de sélection).
  *Reste* : accumuler des échantillons (boucle papier, `shadow_trades` 2.3) et
  mesurer une fenêtre plus longue ou un autre régime avant de figer un filtre. Le
  flag `entry_require_positive_h1` reste un laboratoire, désactivé en production.
  La pente EMA200 protège mais ne crée pas d'arête : 2 trades à −3,49 %, aucun
  scénario positif avec trades.
  *Critère* : le backtest multi-paires produit un nombre de trades interprétable, avec
  des sorties LLM identifiables, et le golden de régression rejoue le nouveau
  comportement sans réseau. (Atteint pour la mécanique ; pas pour l'edge.)

- 🟨 **2.6 — Mémoire de leçons** *(2026-11-04)*
  *Fait* : table `lessons` (migration 0007), relecture à un seul appel via
  `scripts/lessons.py review`, injection des leçons actives dans le prompt de
  décision, panneau « Leçons » dans le dashboard, A/B
  `backtest.py measure --lessons-file`. D-047.
  *Validé par* : `db_migrate.py apply` → 0007 appliquée ; `verify` → 29 relations ;
  relecture réelle sur 5 shadow trades de test → 5 leçons, version 1, puis nettoyage ;
  422 tests Python.
  *Reste* : attendre 5 résultats réels, lancer la relecture, comparer avec et sans
  leçons, et ne garder la mémoire que si la mesure montre un mieux. Premier A/B
  (BTC/EUR, 30 bougies) : −3,49 % sans leçons, −3,45 % avec, écart dans le bruit ;
  non concluant.

- 🟨 **2.7 — Mandat short (laboratoire)** *(2026-11-04)*
  *Fait* : `app/backtest/short_lab.py` + `scripts/short_lab.py` mesurent un short
  miroir hors production, mêmes bougies/frais/slippage. BTC : 1 trade le 29 octobre,
  **+0,93 %** (stop 2,5/TP 1,5) ou **+1,98 %** (stop 3/TP 2) ; variante distance
  −2..0 ATR : −2,91 % sur 2 trades. D-052.
  *Limite* : Kraken spot ne permet pas le short ; il faut de la marge ou Kraken Futures
  (autre adaptateur, contrats, levier, liquidation). 9,37 EUR est trop petit.
  *Reste* : mesurer sur plusieurs régimes et les 7 paires ; exiger un nombre de trades
  interprétable avant d'ajouter `SHORT` au schéma, au Risk Gateway et au broker papier.
  *Critère* : au moins 100 trades short simulés sur plusieurs régimes, espérance
  positive nette de frais ; sinon on abandonne le short.

- 🟨 **2.8 — Historique multi-années (jalon 1)** *(2026-11-04)*
  720 bougies REST ne permettent pas de valider un filtre de tendance. Les archives
  OHLCVT publiques de Kraken (trimestrielles, et complète en 5 parties) sont lues
  **par requêtes HTTP Range** : le répertoire central du ZIP est analysé, seuls les
  CSV `60`/`240` des paires visées sont récupérés. ~9,5 Go d'archive ne coûtent que
  quelques mégaoctets.
  *Fait* : `app/market/kraken_archive.py` (lecteur ZIP par plages, ZIP64),
  `app/market/history.py` (CSV, trous, agrégation, cohérence, écriture idempotente),
  `scripts/ingest_history.py`, migration 0008 (`market_candles_history` + vue
  `v_history_coverage`, RLS + révocation). D-053, D-054, D-055.
  *Validé par* : `db_migrate.py apply` → 0008 appliquée ; `ingest_history.py
  --source full` → BTC/EUR 113 899 H1 depuis 2013-09-10, ETH/EUR 97 407 depuis
  2015-08-07, SOL/EUR 47 041 depuis 2021-06-17 ; cohérence H1→H4 **OK** sur les trois
  paires (aucune bougie H4 divergente de l'agrégation H1) ; `--report` → 1,18 % de
  bougies manquantes sur BTC (concentrées en 2013-2014), 0,02 % sur SOL.
  *Limite connue* : l'archive s'arrête au 30/09/2026 et le REST ne remonte qu'à 720
  bougies → un trou de 111 h en H1 entre le 01/10 et le 05/10 2026 (le H4 est continu).
  Un `--rest-only` quotidien empêche le trou de réapparaître ; celui-ci est assumé
  (5 jours sur 730) et signalé, jamais interpolé.

- 🟨 **2.9 — Balayage statistique du filtre de pente (jalon 2)** *(2026-11-04)*
  `backtest.py sweep` rejoue 24 mois sur l'historique en base, en mode mécanique
  (une réponse BUY constante remplace le LLM : le chemin règle d'entrée → `evaluate()`
  → `PaperBroker` reste celui de la production). Pentes 0 / 1,0 / 1,5 / 2,0 ATR,
  frais 80 pb (taker réel) et 100 pb (stress), équité virtuelle 1 000 EUR.
  Métriques : N, PF, espérance par trade, drawdown max, verdict par les seuils du
  brief (N ≥ 100, PF ≥ 1,30, E > 0, DD ≤ 15 %). D-056.
  *Validé par* : `backtest.py sweep --months 6` → 7 trades (pente 0) et 5 trades
  (pente 2,0) sur BTC, PF 0,21 et 0,10 — mesures ci-dessous.
  *Résultat 24 mois, 48 scénarios* : voir `docs/BACKTEST.md` § « Balayage 24 mois ».
  Deux modes d'entrée mesurés (D-058 : `EMA50_H4` et `PULLBACK_H1`), coupe-circuits
  permanents levés et colonne `gel` (D-059 — sans quoi le verrou de pertes tronquait
  la fenêtre au premier mauvais mois). **Aucun scénario positif** : PF de 0,11 à 0,62,
  N de 14 à 39 trades par paire et par 24 mois. Le repli H1 améliore BTC à pente égale
  (0,46 contre 0,29) mais fait moins bien sur ETH et SOL : déplacer le point d'entrée
  ne change pas le signe.
  *Conclusion* : le plafond de N n'est pas la finesse du setup mais la **cadence de
  décision H4** (une opportunité H1 sur quatre est seulement vue) et la mesure par
  paire. Le seuil de 100 trades n'est atteint qu'en agrégeant les trois paires
  (95 trades à pente 0, mode H4), jamais sur une paire seule.
  *Reste* : mesurer une cadence H1 ou un portefeuille multi-paires ; pas un troisième
  filtre d'entrée. Ne rien figer sur ces chiffres.

- ⛔ **2.10 — Ordres Maker (chantier 4) : gelé par décision**
  Remplacer l'achat au marché par un ordre limite `post_only` au meilleur bid, avec
  annulation après 120 s sans fill. **Interdit tant que 2.9 ne montre pas PF ≥ 1,30** :
  optimiser l'exécution d'une stratégie perdante ne fait que perdre plus lentement, et
  le chemin live est lui-même gelé (aucun ordre réel). Aucun code écrit.

- ✅ **2.11 — Moteur swing macro Daily (D-060, D-061)** *(2026-11-04)*
  *Fait* : `EntryMode.SWING_MACRO_D1` (clôture D1 > EMA50/EMA200, pente EMA50 5 j > 0,
  cassure Donchian 20 **ou** rebond EMA20 avec RSI 50–65), `StopMode.STRUCTURAL_D1`
  (creux 10 D1, plafond 5 %), breakeven à +1 R, sorties EMA20 D1 / Bollinger (20 ; 2,5)
  avec divergence, preset `swing`, `backtest.py swing` (D1 agrégé du H4 en base, sans
  interpolation), allocation 100 % sous 50 EUR avec plancher de viabilité.
  D-061 ajoute : prompt LLM D1 séparé (`v3-d1`), évaluation rétention/filtration sans
  ordre, sortie **Chandelier** optionnelle (`SWING_EXIT_MODE=ema20` par défaut),
  migration **0009** (`1d` en base), scheduler **00:05 UTC**, breakeven live avec
  remplacement de stop sûr (création avant annulation).
  *Validé par* : **581 tests Python** (dont 21 swing/stop structurel, Chandelier,
  scheduler D1, breakeven live) ; `db_migrate.py status` → 9 migrations appliquées ;
  `backtest.py swing` sur BTC/ETH/SOL, 60 mois puis tout l'historique, sorties
  `ema20,chandelier`.
  *Résultat* : **négatif sur 2021-11 → 2026-11** (BTC ema20 PF 0,44 / chandelier 0,77 ;
  ETH 0,57 / 0,38 ; SOL 0,19 / 0,14). Sur tout l'historique, Chandelier porte BTC à
  71 trades, PF 2,81, +192,47 %, DD 11,99 % (EMA20 : 87 trades, PF 2,20) et ETH à
  62 trades, PF 1,93, +68,29 % (EMA20 : PF 1,10) ; SOL reste KO (11 trades, PF 0,14).
  BTC et ETH passent PF/payoff/MDD/N, pas SOL, et aucun ne passe sur la fenêtre
  récente.
  *Mesure LLM D1 — réelle, négative* : 144 trades Chandelier rejoués sur tout
  l'historique, **144/144 appels non dégradés** (30 BUY, 114 HOLD). Rétention
  **30,4 %** (7/23 gagnants), filtration **81,0 %** (98/121 perdants), verdict
  `KO rétention<90%`. Le modèle filtre bien les perdants mais rejette 69,6 % des
  gagnants ; la confiance ne les sépare pas (0,65 contre 0,62). Runner Ollama
  réinstallé localement (`~/.local/opt/ollama-0.40.14`, port 11435) car
  `/usr/local/lib/ollama` est vide.
  *Conclusion* : le trend-following D1 à faible taux de réussite est réel sur les cycles
  2014–2021, pas une arête actuelle. Ne pas activer, ne pas figer les seuils. Critères
  révisés : PF ≥ 1,35 net, payoff ≥ 3,5, MDD ≤ 20 %, N ≥ 50 ; le winrate ≥ 40 % est
  abandonné.
  *Reste* : revoir le prompt D1 ou la définition du signal d'entrée (le seuil de
  confiance ne discrimine pas), mesurer un filtre de régime (BTC vs ETH, breadth) ou
  une sortie moins whipsaw ; ne pas empiler un quatrième filtre d'entrée.

- ✅ **2.12 — Mean Reversion Bollinger (laboratoire, D-064)** *(2026-11-05)*
  *Hypothèse* : clôture sous la bande de Bollinger basse (20 ; 2), RSI(14) < 32 et
  volume > SMA(volume, 20) ; sortie au contact de la bande médiane ou stop structurel
  sous le creux des 10 bougies.
  *Fait* : `app/backtest/mean_reversion.py` + `backtest.py meanrev`. Le pare-feu reste
  l'autorité ; une variante `raw` neutralise **uniquement** le filtre EMA200
  (`allow_counter_trend`, laboratoire) pour distinguer l'hypothèse de l'effet du
  filtre de tendance. Aucun chemin de configuration runtime ne pose ce drapeau.
  *Validé par* : **581 tests Python** (dont 4 mean-reversion et 7 plomberie
  d'urgence) ; `backtest.py meanrev --months 60` sur BTC/ETH/SOL, H4 et D1,
  RSI 30/32/35, sorties `touch`/`close`, frais 80/100 pb → **144 scénarios**.
  *Résultat* : **hypothèse rejetée**. 18 scénarios sur 144 ont un net positif, mais
  seulement 5 atteignent PF ≥ 1,30 — tous avec **2 à 6 trades**, donc non
  interprétables. La variante `raw` (sans filtre de tendance) plafonne à **PF 0,85**
  et perd 4 804 EUR cumulés sur l'ensemble : l'arête n'existe pas hors tendance
  haussière. Scénario principal demandé (RSI 32, touche, 80 pb) : BTC H4 4 trades
  PF 0,00, ETH H4 4 trades PF 0,00, SOL H4 6 trades PF 1,28, BTC D1 2 trades,
  ETH D1 1 trade, SOL D1 0 trade. La cadence reste le plafond : 0,2 à 1,2 trade/an
  et par paire.
  *Conclusion* : ne pas activer, ne pas empiler un filtre de plus. Le levier reste
  la cadence (H1) ou la couverture multi-paires, déjà identifié en 2.9.

- ✅ **2.13 — Sensibilité aux frais maker (laboratoire, D-065)** *(2026-11-05)*
  *Hypothèse* : une partie de l'échec vient du tarif taker réel (80 pb/côté) ; à
  25 pb maker, une arête apparaît-elle ?
  *Fait* : `--maker-fees [valeur]` (défaut 25) dans `backtest.py`, cumulable avec
  `--fee-bps` et appliqué aux trois balayages. C'est une sensibilité au coût, pas un
  modèle d'exécution maker : le fill reste clôture + slippage.
  *Validé par* : swing D1 60 mois, 36 scénarios → 80 pb : 3 PF ≥ 1,30, PF max 2,31,
  net cumulé −665,68 EUR ; 25 pb : 3 PF ≥ 1,30, PF max 2,92, net cumulé **−64,98
  EUR**. Mean reversion 144 scénarios → 80 pb : 3 PF ≥ 1,30, PF max 1,95 ; 25 pb :
  7 PF ≥ 1,30, PF max 2,85, aucune poche > 15 trades.
  *Conclusion* : les frais n'expliquent pas l'absence d'arête. Même à 25 pb, le
  trend-following reste négatif en cumulé et le mean reversion ne produit que des
  poches à 2–15 trades. Ne pas activer.

- ✅ **2.14 — Rotation momentum BTC/ETH/SOL/CASH (laboratoire, D-066)** *(2026-11-05)*
  *Hypothèse* : abandonner le choix d'entrée et allouer 100 % au actif le plus fort
  (momentum 30/60 j, filtre EMA200 D1), sinon 100 % cash.
  *Fait* : `app/backtest/rotation.py` + `scripts/lab_rotation.py`, cadence 7 ou 14
  jours, frais et slippage par côté, mark-to-market quotidien ; 6 tests unitaires.
  *Validé par* : 2021-11 → 2026-11, 1 762 D1. 7 j/25 pb : +163,74 %, DD 54,57 %,
  9,9 rotations/an ; 14 j/25 pb : +238,13 %, DD 60,13 %, 7,0 rotations/an. À 80 pb :
  +77,49 % et +151,25 %. Buy & hold : BTC +69,7 %, ETH −39,6 %, SOL −44,4 %.
  *Conclusion* : la rotation bat le buy & hold et tient la cadence visée, mais des
  drawdowns de 55–62 % sont hors du critère de 20 %, et les minimums d'ordre ne sont
  pas modélisés. Laboratoire prometteur, **non activé**. Prochain levier : risque ou
  ciblage de volatilité, pas un déploiement. *Note* : ces chiffres proviennent du
  filtre historique « EMA200 + score > 0 » ; voir 2.15 pour les modes absolus et la
  nouvelle référence `none`.

- ✅ **2.15 — Dual momentum, filtre absolu (laboratoire, D-067)** *(2026-11-05)*
  *Hypothèse* : le relatif pur reste investi dans l'actif le moins baissier pendant
  les purges ; un filtre absolu sur le leader doit couper vers le cash et ramener le
  drawdown sous 25 %, tout en gardant plus de +70 % de rendement.
  *Fait* : `--abs-filter none|ema200|roc60|dual` dans `app/backtest/rotation.py` et
  `scripts/lab_rotation.py`. Le leader est choisi avant le filtre ; s'il échoue, 100 %
  cash, jamais le deuxième. Frais par transaction (0, 1 ou 2 côtés), `--summary-table`,
  temps en cash, rotations cash/crypto et ventilation annuelle. 12 tests unitaires.
  *Validé par* : 16 scénarios (4 filtres × 2 cadences × 2 frais), 2021-11 → 2026-11,
  1 762 D1. Meilleur : `dual` 14 j/25 pb → **+125,56 %**, DD **54,46 %**, 32 rotations
  (6,6/an), 56,4 % de cash. `dual` 7 j/25 pb → +86,00 %, DD 64,55 %. `none` → −1,22 %
  à −56,50 %, DD 77–84 %. En 2022, `dual` 14 j/25 pb est 96,1 % en cash ; le DD vient
  de 2024 (pic 4 674,34 EUR le 2024-04-01 → creux 2 128,82 EUR le 2024-08-06).
  *Conclusion* : rendement cible atteint, **drawdown cible non atteint** (54,46 % contre
  25 %). Lab-only, non activé. Prochain levier : sortie de risque plus rapide ou
  ciblage de volatilité, pas un filtre d'entrée de plus.

- ✅ **2.16 — Control plane étendu et télémétrie (D-068)** *(2026-11-05)*
  *Fait* : routes `kill-switch`, `circuit-breaker/reset`, `reconcile`, `system/health`,
  `portfolio/position`, inspection prompt LLM ; métriques avancées et ventilation du
  capital dans `/api/stats` ; dashboard branché (badges santé, KPIs, modale panic double
  garde, tiroir prompt).
  *Validé par* : 603 tests Python, 26 tests Node, `bash scripts/check.sh` vert.
  *Conclusion* : aucun changement de mode ; `TRADING_MODE=paper`, live verrouillé.

- ✅ **2.17 — Progression à petit budget, mécanique papier** *(2026-11-09)* : profil
  papier à 9,37 EUR, budget de perte frais compris, stop suiveur commun au moteur
  et au rejeu, comparaison des budgets sans relâcher automatiquement le risque.
  Aucun rendement régulier promis ; le cash et l'absence de trade sont acceptables.
  *Vérifié* : 617 tests Python, 26 Node, golden inchangé et comparatif hors ligne.
  La rentabilité multi-années et la base déployée restent non validées dans cette
  étape ; les critères de passage en réel ne changent pas.

- ✅ **2.18 — Capital protégé et croissance par paliers** *(2026-11-09)* :
  journal comptable, surplus global réalisé, réserve immédiate, cagnotte de croissance,
  reprise atomique des clôtures et stops papier, coûts partagés et rejeu de la politique.
  Réservé au papier ; 643 tests Python dont 6 PostgreSQL locaux, 26 Node, golden
  inchangé et contrôle navigateur bureau/mobile. La base déployée et la rentabilité
  multi-années restent non validées. Détails : D-070 et `PROGRESSION.md`.

- ✅ **2.19 — Nettoyage documentaire et briefs des nouvelles idées (D-072)** *(2026-11-09)*
  Retirer les spécifications et le handoff remplacés ; créer le catalogue
  [IDEES_ET_BRIEFS_TECHNIQUES.md](IDEES_ET_BRIEFS_TECHNIQUES.md). Les dix idées
  sont proposées, sans commencer leur implémentation.
  *Validé par* : 43 liens locaux vérifiés, dix briefs complets, IDs uniques et
  différences exclusivement Markdown par rapport à `master` ; code applicatif inchangé.

- ✅ **2.20 — Implémentation des dix briefs I-01 à I-10 (D-073)** *(2026-11-09)*
  Journal/rejeu, diagnostics, suspension des entrées, politiques de position,
  comparaisons papier, coûts et classement en observation, parcours des opportunités,
  surveillance indépendante et analyse des scores. Aucun armement live.
  *Vérifié* : 695 tests Python, dont 24 contrats PostgreSQL 17 locaux, 31 Node,
  golden inchangé, navigateur desktop/mobile et CLI hors ligne. Onze migrations
  appliquées localement ; `db_migrate.py verify` vérifie aussi RLS et droits privés.
  Guide et limites : [OBSERVATION_ET_RECHERCHE.md](OBSERVATION_ET_RECHERCHE.md).
  *Reste hors livraison* : déploiement cible, canal réel du surveillant, rétention,
  mesures multi-années et hors calibration. Aucun armement live.

- ✅ **2.21 — Revue technique indépendante et analyse pré go live** *(2026-11-09)*
  Relecture de `master` 39db447, comparaison avec les correctifs locaux non intégrés,
  suite complète et scénarios de panne sans transport réel.
  *Vérifié* : 695 Python dont 24 SQL, 31 Node, golden et navigateur ; huit
  reproductions mémoire et deux SQL confirment des défauts hors suite existante.
  Lecture seule Supabase : 0001–0009 concordantes, 0010/0011 absentes et fonction
  privilégiée encore exécutable par anon. Aucun correctif ni ordre réel.
  *Conclusion* : **NO-GO**, rapport et portes G0–G6 dans
  [AUDIT_PRE_GO_LIVE_2026-11-09.md](AUDIT_PRE_GO_LIVE_2026-11-09.md). L’audit est
  terminé ; les défauts qu’il identifie restent ouverts.

**Critère de passage en phase 3** : au moins **100 trades simulés**, drawdown maximal
sous le seuil configuré, espérance positive. En dessous, on corrige ou on abandonne —
les deux sont des résultats utiles. **Non atteint** : 2.9 plafonne à 39 trades par
paire, 2.11 est négatif sur la fenêtre récente, 2.12 est rejeté, 2.13 ne crée pas
d'arête, 2.14 reste au-dessus du drawdown maximal et 2.15 échoue la cible de 25 %.

---

## Phase 3 — Pré-live (testnet impossible) 🟨

Objectif : prouver la mécanique avec un vrai exchange et un capital minuscule. Kraken
spot n'a pas de testnet dans CCXT 4.5.91 : le « testnet » est remplacé par un canary
réel, sous-compte dédié, après validation à blanc.

- ✅ **3.1 — Exécuteur réel validé à blanc** *(2026-11-05)*
  `app/execution/live.py` : achat marché + stop bracket, fill réel, stop relu après le
  fill, revente si le stop manque ; monitoring exchange-first pour ne jamais vendre
  deux fois ; `scripts/reconcile.py --apply --confirm` ; `kill_switch.py
  --confirm-live`. S'y ajoutent le **panic-sell réel** (annulation globale + vente de
  tout solde de base, écriture des fills réels au ledger) et la **clé API secondaire**
  du kill-switch (`KILL_SWITCH_API_KEY` / `KILL_SWITCH_SECRET`) pour éviter la
  collision de nonces. D-063.
  *Validé par* : 581 tests Python, aucun ordre réel exécuté ; puis
  `scripts/live_probe.py --confirm-live` sur le compte Kraken → ordre volontairement
  invalide `0.0000007 BTC` (≈ 0,05 EUR) rejeté par l'exchange
  (`EGeneral:Invalid arguments:volume minimum not met`), **0 ordre créé**, aucun
  plantage. Le compte principal a servi de sonde : aucun sous-compte n'existe encore.
  *Reste* : refaire la sonde sur le sous-compte dédié le jour de son ouverture, puis
  3.2.
- ⬜ **3.2 — Premier aller-retour réel minuscule** : sous-compte dédié, montant décidé
  par Seb, achat → stop visible chez Kraken → sortie → sweep. Aucun solde manuel sur
  la paire.
- ⬜ **3.3 — Survie au redémarrage** : couper le bot pendant une position, vérifier que
  le stop exchange déclenche, que la réconciliation enregistre la vente réelle sans
  renvoyer d'ordre, et que le kill-switch fonctionne backend arrêté.

**Critère de passage** : un cycle réel complet, stop visible chez Kraken, aucune
position nue, et aucun double envoi après redémarrage.

---

## Phase 4 — Argent réel ⬜

- ⬜ **4.1 — Choisir le montant** que Seb accepte de perdre **entièrement**
- ⬜ **4.2 — Clés mainnet dédiées** : retraits désactivés, restriction IP, sous-compte
- ⬜ **4.3 — Profil `prudent`, surveillance quotidienne du premier mois**

**Règle** : le montant engagé est celui dont la perte totale ne change rien à la vie de
Seb. Le risque par trade (0,5 %) reste inchangé quel que soit le capital.

---

## Plan de passage en réel — référence unique

La roadmap opérationnelle complète est désormais [GO_LIVE.md](GO_LIVE.md).
Elle remplace les anciennes portes G0–G6 et la checklist d’activation de ce document :
**R0 version unique → R1 isolation/droits → R2 contrats financiers → R3 exploitation
PAPER → R4 économie/faisabilité → R5 recette cible → R6 canary autorisé → R7
stabilisation et décision finale.**

Chaque étape a ses travaux, preuves, responsabilités et règles d’arrêt. Les durées
et effectifs de R5/R7 sont un protocole proposé à valider avant activation ; aucun
n’est déjà acquis. Aucun passage au réel n’est autorisé par la rédaction du plan.
Les phases historiques ci-dessus documentent les travaux passés et ne permettent
pas de contourner les critères du plan actuel.

---

## Ce qui attend une action de Seb

| # | Question | Impact |
|---|---|---|
| 1 | ✅ **Répondu le 2026-11-04** : clés **mainnet**, lecture + trading spot, retraits désactivés, IP restreinte. **Kraken spot n'a pas de testnet** dans CCXT 4.5.91 : la phase 3 devra être redéfinie (ordres minuscules en réel, ou exchange de simulation) | Sans cette redéfinition, la phase 3 telle qu'écrite est infaisable |
| 2 | Budget déjà documenté : **9,37 EUR de perte totale acceptable** ; vérifier sa compatibilité avec les minima et les frais avant un canary | Aucun relèvement automatique du risque ou du budget pour financer un ordre |
| 3 | Modèle déjà documenté : **qwen3.5:9b** ; disponibilité et runner à vérifier sur la machine cible | Les mesures historiques ne prouvent pas la disponibilité actuelle du service |
| 4 | ✅ **Résolu le 2026-11-04** : clé Kraken remplacée puis vérifiée — 7 permissions, aucun droit de retrait, IP restreinte. Le garde-fou passe | — |
| 5 | Créer une **seconde paire de clés Kraken** pour le kill-switch (le code lit déjà `KILL_SWITCH_API_KEY` / `KILL_SWITCH_SECRET` et retombe sur la clé du bot avec un avertissement), ou accepter que le kill-switch ne puisse pas signer en parallèle du bot (nonces strictement croissants par clé) | Sans elle, le kill-switch réel ne peut pas tourner pendant que le bot tourne |
