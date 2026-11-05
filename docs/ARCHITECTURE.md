# Architecture

## 1. Le flux de décision

```
   [ Exchange ]  ──bougies H4/H1──▶  Market data + indicateurs
                                            │
                                            ▼
                                   [ LLM local ]  → {action, confidence, regime}
                                            │      (rien d'autre n'est lu)
                                            ▼
                          ┌─────────────────────────────────────┐
                          │        RISK GATEWAY (pur)           │
                          │  15 étapes ordonnées, fail-closed   │
                          │  23 codes de rejet, ne lève jamais  │
                          └─────────────────┬───────────────────┘
                                            │ accepté
                                            ▼
                          [ Executor CCXT ]  achat marché
                                 │           puis stop distant, immédiatement
                                 ▼
                          [ PostgreSQL ]  signals → évaluations → ordres
                                          → positions → écrémages
```

Le point à retenir : **la flèche qui va du LLM vers la gateway ne porte que trois
champs**, et tous les trois ne peuvent que provoquer un refus. Toute la géométrie du
système découle de là.

## 2. Ce qui existe, ce qui n'existe pas

**570 tests** Python et **26 tests** Node (596 au total), répartis ci-dessous.

| Composant | Fichier | État |
|---|---|---|
| Pare-feu déterministe | `app/engine/risk_gate.py` | ✅ écrit (129 assertions) |
| Contrat schéma ↔ code | `tests/test_schema_contract.py` | ✅ écrit (15 assertions) |
| Configuration et presets de risque | `app/core/settings.py` | ✅ écrit (25 assertions) |
| Résolution de connexion Postgres | `app/core/db.py` | ✅ écrit (10 assertions) |
| Verdict de la sonde exchange | `tests/test_check_exchange.py` | ✅ écrit (9 assertions) |
| Passerelle Kraken spot | `app/exchange/kraken.py` | ✅ préparation d'ordre, aucun envoi |
| Exécuteur réel | `app/execution/live.py` | ✅ écrit (10 assertions), aucun ordre réel envoyé |
| Choix du broker | `app/execution/factory.py` | ✅ paper/live, testnet refusé |
| Flux de marché et indicateurs | `app/market/feed.py` | ✅ écrit (9 assertions) |
| Client LLM local | `app/llm/client.py` | ✅ écrit (16 assertions) |
| Machine à états | `app/engine/machine.py` | ✅ écrit (4 assertions) |
| Exécution papier | `app/execution/paper.py` | ✅ écrit (9 assertions) |
| Orchestrateur | `app/runtime/orchestrator.py` | ✅ écrit (9 assertions) |
| Persistance PostgreSQL | `app/db/repository.py` | ✅ vérifié sur le schéma réel |
| Profit Sweeper | `app/engine/sweep.py` | ✅ écrit (13 assertions) |
| Liquidation d'urgence | `app/runtime/recovery.py` | ✅ écrit (3 assertions) |
| API — authentification | `app/api/security.py` | ✅ écrit (22 assertions) |
| API — routes | `app/api/main.py` | ✅ écrit (12 assertions) |
| API — lecture et contrôle | `app/api/service.py` | ✅ écrit |
| Dashboard Node.js | `dashboard/server.js` | ✅ écrit (26 tests Node, zéro dépendance) |
| Gestion des processus | `scripts/manage.sh` | ✅ écrit (start/stop/restart/status/logs) |
| Backtest historique | `app/backtest/engine.py` | ✅ écrit (7 assertions) |
| Golden de régression | `tests/fixtures/backtest_golden.json` | ✅ 44 décisions figées |
| Faisabilité avec petit capital | `app/engine/affordability.py` | ✅ écrit (6 assertions) |
| Règle d'entrée déterministe | `app/engine/entry_rule.py` | ✅ écrit (28 assertions), deux modes |
| Garde-fou de sortie LLM | `app/engine/exit_guard.py` | ✅ écrit (9 assertions) |
| Sonde de clé Kraken | `scripts/check_kraken.py` | ✅ lecture seule |
| Lecture `.env` / `.env.local` | `app/core/env.py` | ✅ écrit |
| Schéma SQL + migrations | `db/migrations/` | ✅ 9 migrations appliquées sur Supabase |
| Coureur de migrations | `scripts/db_migrate.py` | ✅ status / apply / verify |
| Démonstration du pare-feu | `scripts/demo_guardrails.py` | ✅ exécutable |
| Sonde de clé exchange (lecture seule) | `scripts/check_exchange.py` | ✅ écrit — mainnet vérifié, testnet sans clé |
| Réconciliation base ↔ exchange | `app/runtime/reconciliation.py` | ✅ lecture + verdict + application (`--apply`) |
| Shadow trades (achats non pris) | `app/runtime/shadow.py` | ✅ simulation stop/TP + rapport |
| Rapport shadow | `scripts/shadow_report.py` | ✅ résumé + `--evaluate` |
| Mémoire de leçons | `app/llm/lessons.py` | ✅ digest, validation, relecture |
| Relecture CLI | `scripts/lessons.py` | ✅ review / show / history / clear |
| Audit de réconciliation | `scripts/reconcile.py` | ✅ audit ; `--apply --confirm` en live |
| Lecture ZIP distante par plages | `app/market/kraken_archive.py` | ✅ écrit (12 assertions), ZIP64 |
| Historique OHLCVT persistant | `app/market/history.py` | ✅ écrit (22 assertions) |
| Ingestion multi-années | `scripts/ingest_history.py` | ✅ 158 600 bougies en base |
| Balayage statistique | `backtest.py sweep` | ✅ écrit — 48 scénarios, deux modes |
| Cadence d'évaluation | `app/runtime/scheduler.py` | ✅ écrit (8 assertions) |
| Vérification des drawdowns | `scripts/verify_drawdowns.py` | ✅ jeu figé, transaction annulée |

Autrement dit : **le corps est assemblé en mode papier, et le chemin réel est écrit mais
non activé.** `python -m app.main --once` exécute un cycle complet (données réelles, LLM
local, pare-feu, fill simulé persisté), et `--serve` aligne les cycles sur les clôtures
H4. Le modèle propose `BUY`, `SELL` ou `HALT` ; la règle d'entrée décide quand le
consulter — repli sur l'EMA50 H4 (`EMA50_H4`) ou repli fin sur l'EMA50 H1
(`PULLBACK_H1`), deux modes mesurés séparément et choisis par `ENTRY_MODE` — le
pare-feu dimensionne et refuse les achats, le garde-fou de sortie interdit
les ventes à découvert. Le chemin live (`app/execution/live.py`) envoie un ordre marché
avec stop bracket, vérifie le stop après le fill, lit les frais réels et enregistre une
vente exchange sans renvoyer d'ordre. Il manque l'activation réelle (sous-compte dédié,
montant, trois verrous), le take-profit exchange (OCO) et le rapport de phase 2.

Le dashboard Node.js n'est pas une couche de décision : il relaie les routes
d'administration, garde le JWT côté serveur et pousse l'état en SSE. Il expose en
lecture seule les drawdowns et les plafonds hebdomadaire / depuis le plus haut / de
pertes consécutives ; seuls les réglages explicitement autorisés sont modifiables.
L'historique détaillé passe par une liste blanche de quatre tables et une limite de
50 lignes. Détails et contrat API dans [`DASHBOARD.md`](DASHBOARD.md).

La carte ligne par ligne (modules, classes, méthodes, migrations, tests) est dans
[`CODE_INDEX.md`](CODE_INDEX.md). Elle est **générée** par `scripts/index_codebase.py` et
vérifiée par `scripts/check.sh` : un index périmé fait échouer la vérification.

## 3. Les 15 étapes de filtrage et les 23 codes de rejet

L'ordre est délibéré : le moins coûteux et le plus protecteur d'abord, les calculs en
dernier. Un échec arrête tout et produit un code, jamais une exception : `evaluate()` est
enveloppé et toute anomalie imprévue devient `INTERNAL_ERROR`.

| # | Filtre | Code(s) de rejet possible(s) |
|---|---|---|
| 1 | Intégrité de l'entrée : énumération, bornes de confiance, fuseau horaire, valeurs finies | `MALFORMED_INTENT`, `INVALID_MARKET_DATA` |
| 2 | Fraîcheur des données : plus de 5 s dans le passé **ou** plus de 5 s dans le futur | `STALE_MARKET_DATA` |
| 3 | Seul `BUY` est actionnable | `ACTION_IS_NOT_BUY` |
| 4 | Bot verrouillé | `BOT_LOCKED` / motif du verrou |
| 5 | Drawdown 24 h | `CIRCUIT_BREAKER_DAILY_DRAWDOWN` |
| 6 | Drawdown 7 j | `CIRCUIT_BREAKER_WEEKLY_DRAWDOWN` |
| 7 | Écart au plus haut d'équité | `CIRCUIT_BREAKER_MAX_DRAWDOWN` |
| 8 | Pertes consécutives | `CIRCUIT_BREAKER_CONSECUTIVE_LOSSES` |
| 9 | Ordres sur 60 min (anti-emballement) | `MAX_ORDERS_PER_HOUR_EXCEEDED` |
| 10 | Plancher de confiance (0,80) | `CONFIDENCE_TOO_LOW` |
| 11 | Le modèle lui-même annonce une tendance baissière | `REJECTED_REGIME_CONFLICT` |
| 12 | Monoposition | `POSITION_ALREADY_OPEN` |
| 13 | Bande de volatilité (marché mort / ATR anormal) | `REJECTED_LOW_VOLATILITY`, `REJECTED_EXTREME_VOLATILITY` |
| 14 | Tendance longue (prix > EMA200 H4 + 0,5 %), puis couteau qui tombe | `REJECTED_COUNTER_TREND_EMA200`, `REJECTED_FALLING_KNIFE` |
| 15 | Dimensionnement, minima exchange, budget de risque final | `INSUFFICIENT_FREE_BALANCE`, `SIZING_BELOW_EXCHANGE_MINIMUM`, `EXCHANGE_MIN_NOTIONAL_FAILED`, `INVALID_STOP_LOSS_CALCULATION`, `RISK_LIMIT_EXCEEDED` |

Deux codes ne peuvent être émis qu'en amont du pipeline, quand aucun calcul n'a lieu :
`MALFORMED_INTENT` (entrée illisible) et `INTERNAL_ERROR` (exception imprévue absorbée).
Le total fait 23 codes, dont `NONE`.

Le détail des calculs est en tête de `risk_gate.py` et dans `docs/DECISIONS.md` (D-004).

## 4. Modèle de données

8 tables créées par les migrations, plus `schema_migrations` créée par le coureur de
migrations lui-même (elle n'est donc pas sous l'autorité du DDL, mais sous celle de
`db_migrate.py`). Les migrations 0005, 0006 et 0007 ajoutent `runtime_settings`,
`shadow_trades` et `lessons`, ce qui porte le total à 11 tables métier.

| Table | Rôle | Partitionnée |
|---|---|---|
| `llm_signals` | Ce que le modèle a répondu, prompt inclus | oui, par mois |
| `risk_evaluations` | Chaque passage dans le pare-feu, accepté **ou refusé** | oui, par mois |
| `positions` | Cycle de vie d'une position, stop et TP inclus | non |
| `orders` | Chaque ordre envoyé à l'exchange | non |
| `profit_sweeps` | Journal des 30 % mis de côté | non |
| `bot_state` | **Une ligne par mode** : état, verrou, cooldown, plus haut d'équité | non |
| `equity_snapshots` | Valorisation du compte dans le temps (drawdowns) | non |
| `system_state_logs` | Journal des transitions d'état | non |
| `runtime_settings` | Réglages du dashboard, un payload JSONB par mode | non |
| `shadow_trades` | Achats non pris (BUY refusé / HOLD sur setup) et issue simulée | oui (par mois) |
| `lessons` | Leçons actives, versionnées, générées à partir des résultats | non |
| `schema_migrations` | Migrations appliquées + empreinte SHA-256 | non |

Toutes les tables de **données** portent une colonne `mode` (`PAPER` / `TESTNET` /
`LIVE`) : les statistiques de simulation et d'argent réel ne se mélangent jamais.
`schema_migrations` fait exception, par nature. Les vues qui agrègent (`v_system_health`,
`v_dashboard_pnl_metrics`, `v_dashboard_pnl_daily`, `v_risk_rejection_stats`) groupent ou
filtrent explicitement par mode.

Quatre contraintes portent des garde-fous en base, pas seulement dans le code :
`ux_positions_one_open_per_symbol` (monoposition), l'index `orders(mode, created_at)`
(coupe-circuit d'ordres), `profit_sweeps.transfer_status` en énumération (un écrémage
raté ne peut pas se faire passer pour réussi), et
`chk_risk_eval_context_when_approved` (une décision acceptée doit documenter son contexte
de marché ; un refus précoce peut ne pas l'avoir).

## 5. Ce qui empêche le système de perdre gros, par ordre d'efficacité

1. **Le stop distant** vit chez l'exchange : il survit à un crash du PC et à une coupure
   réseau. C'est la protection la plus solide de l'ensemble.
2. **Le plafond d'exposition** (20 % de l'équité, plus un plafond absolu) borne les
   dégâts d'un bug de dimensionnement à une somme connue.
3. **Le coupe-circuit à -10 % depuis le plus haut** arrête une stratégie qui a cessé de
   fonctionner — c'est le seul qui protège d'une érosion lente.
4. **La limite d'ordres par heure** arrête un bug de boucle, qui est le scénario
   catastrophe réaliste (le krach, lui, est couvert par le stop).
5. **Le triple verrou d'armement** empêche un déploiement accidentel d'engager de
   l'argent : `live` + sandbox désactivé + `I_UNDERSTAND_REAL_MONEY`, et `paper`/`testnet`
   refusent un sandbox désactivé.

Ce qui **ne** protège de rien : le LLM. Il n'a pas d'avantage statistique démontré. Sa
seule utilité est de dire « je ne vois rien » la plupart du temps.
