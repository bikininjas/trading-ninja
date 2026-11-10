# Architecture

État au 10 novembre 2026. Livraison : Python 3.13.17 et dépendances figées ;
Supabase en 0013. Le plan de qualification reste [Roadmap](ROADMAP.md).

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
                          [ Reçu PREPARED + verrou financier ]
                                 │
                                 ▼
                          [ Executor CCXT ]  achat + stop attaché
                                 ▼
                          [ PostgreSQL ]  signals → évaluations → ordres
                                          → positions → écrémages
```

Le point à retenir : **la flèche qui va du LLM vers la gateway ne porte que trois
champs** : `action`, `confidence` et `market_regime`. L'action peut déclencher
un achat, une vente de clôture ou un arrêt ; les garde-fous déterministes valident
ou refusent ces intentions. Un SELL ne peut jamais ouvrir un short. Le LLM ne
choisit ni quantité, ni prix, ni stop : toute la géométrie vient du code.

## 2. Ce qui existe, ce qui n'existe pas

**798 tests** Python (758 hors base et 40 contrats PostgreSQL locaux opt-in)
et **31 tests** Node (775 au total), répartis ci-dessous.

| Composant | Fichier | État |
|---|---|---|
| Pare-feu déterministe | `app/engine/risk_gate.py` | ✅ écrit |
| Contrat schéma ↔ code | `tests/test_schema_contract.py` | ✅ écrit |
| Configuration et presets de risque | `app/core/settings.py` | ✅ écrit |
| Résolution de connexion Postgres | `app/core/db.py` | ✅ écrit |
| Verdict de la sonde exchange | `tests/test_check_exchange.py` | ✅ écrit |
| Passerelle Kraken spot | `app/exchange/kraken.py` | ✅ préparation d'ordre, aucun envoi |
| Exécuteur réel | `app/execution/live.py` | ✅ écrit, aucun ordre réel envoyé |
| Choix du broker | `app/execution/factory.py` | ✅ paper/live, testnet refusé |
| Flux de marché et indicateurs | `app/market/feed.py` | ✅ écrit |
| Client LLM local | `app/llm/client.py` | ✅ écrit |
| Machine à états | `app/engine/machine.py` | ✅ écrit |
| Exécution papier | `app/execution/paper.py` | ✅ écrit |
| Orchestrateur | `app/runtime/orchestrator.py` | ✅ écrit |
| Persistance PostgreSQL | `app/db/repository.py` | ✅ vérifié sur le schéma réel |
| Profit Sweeper | `app/engine/sweep.py` | ✅ écrit |
| Liquidation d'urgence | `app/runtime/recovery.py` | ✅ écrit |
| API — authentification | `app/api/security.py` | ✅ écrit |
| API — routes | `app/api/main.py` | ✅ écrit |
| API — lecture et contrôle | `app/api/service.py` | ✅ écrit |
| Dashboard Node.js | `dashboard/server.js` | ✅ écrit (31 tests Node, zéro dépendance) |
| Gestion des processus | `scripts/manage.sh` | ✅ écrit (start/stop/restart/status/logs) |
| Backtest historique | `app/backtest/engine.py` | ✅ écrit |
| Golden de régression | `tests/fixtures/backtest_golden.json` | ✅ 44 décisions figées |
| Faisabilité avec petit capital | `app/engine/affordability.py` | ✅ écrit |
| Règle d'entrée déterministe | `app/engine/entry_rule.py` | ✅ écrit, deux modes |
| Garde-fou de sortie LLM | `app/engine/exit_guard.py` | ✅ écrit |
| Sonde de clé Kraken | `scripts/check_kraken.py` | ✅ lecture seule |
| Lecture `.env` / `.env.local` | `app/core/env.py` | ✅ écrit |
| Schéma SQL + migrations | `db/migrations/` | ✅ 13 migrations versionnées et appliquées sur trninja, empreintes vérifiées |
| Coureur de migrations | `scripts/db_migrate.py` | ✅ status / apply / verify |
| Démonstration du pare-feu | `scripts/demo_guardrails.py` | ✅ exécutable |
| Sonde de clé exchange (lecture seule) | `scripts/check_exchange.py` | Sonde historique Binance ; utiliser check_kraken.py pour la cible actuelle |
| Réconciliation base ↔ exchange | `app/runtime/reconciliation.py` | ✅ lecture + verdict + application (`--apply`) |
| Shadow trades (achats non pris) | `app/runtime/shadow.py` | ✅ simulation stop/TP + rapport |
| Rapport shadow | `scripts/shadow_report.py` | ✅ résumé + `--evaluate` |
| Mémoire de leçons | `app/llm/lessons.py` | ✅ digest, validation, relecture |
| Relecture CLI | `scripts/lessons.py` | ✅ review / show / history / clear |
| Audit de réconciliation | `scripts/reconcile.py` | ✅ audit ; `--apply --confirm` en live |
| Lecture ZIP distante par plages | `app/market/kraken_archive.py` | ✅ écrit, ZIP64 |
| Historique OHLCVT persistant | `app/market/history.py` | ✅ écrit |
| Ingestion multi-années | `scripts/ingest_history.py` | ✅ 323 374 bougies vérifiées sur cible le 10 novembre |
| Balayage statistique | `backtest.py sweep` | ✅ écrit — 48 scénarios, deux modes |
| Cadence d'évaluation | `app/runtime/scheduler.py` | ✅ écrit |
| Observation et recherche | `app/insights/`, `app/api/insights.py` | ✅ dix briefs, comportements et limites dans le guide |
| Journal/rejeu | `app/insights/replay.py` | ✅ vrai runtime, I/O enregistrées, code et environnement vérifiés |
| Pilotage versionné | `app/insights/policy.py`, `store.py` | ✅ suspension, politiques, révisions demandées/appliquées |
| Laboratoire papier | `app/insights/experiments.py` | ✅ portefeuilles indépendants et données causales |
| Surveillant indépendant | `scripts/insights.py watch` | ✅ lecture seule exchange, HALT en base, installation à qualifier |
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
50 lignes. Détails et contrat API dans [Dashboard](../dashboard/README.md).

La carte ligne par ligne (modules, classes, méthodes, migrations, tests) est dans
[`CODE_INDEX.md`](CODE_INDEX.md). Elle est **générée** par `scripts/index_codebase.py` et
vérifiée par `scripts/check.sh` : un index périmé fait échouer la vérification.

L’API utilise un pool asyncpg borné à quatre connexions et réserve une connexion
par requête/transaction. Le WebSocket la libère entre émissions ; les verrous de
cycle gardent leur connexion dédiée. Les recherches HTTP sont limitées à un calcul
simultané pour préserver la disponibilité administrative.

Le module `insights` observe les frontières du runtime, sans créer de chemin
parallèle d’ordre. La sélection de paire reste en observation par défaut ; seule
l’option PAPER explicite peut changer le candidat, avec revalidation finale.
La stratégie attachée à la position reste immuable ; les limites de sécurité sont
courantes. Le guide [Observation et recherche](OBSERVATION_ET_RECHERCHE.md) décrit
les paquets et contrats d’observation. Le [suivi des corrections](JOURNAL.md#correctifs-pre-go-live)
et la [preuve Supabase](JOURNAL.md#deploiement-supabase) couvrent le durcissement
et les treize migrations appliquées.

Le chemin LIVE partage un verrou financier interprocessus entre moteur, reprise et
urgences. Une intention est commitée avant la mutation exchange ; UNKNOWN bloque
le renvoi. Les fills cumulatifs sont dédupliqués et les reliquats rapprochés. La
suspension interdit de nouvelles autorisations sans annuler une intention déjà
autorisée. Les tests d’arrêt brutal couvrent trois frontières ; la matrice complète
reste une porte R2.

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

Le schéma courant compte **27 tables logiques**, dont `schema_migrations`,
plus 17 partitions physiques (44 relations au total) et six vues. Le coureur gère
l’historique applicatif ; Supabase conserve aussi les quatre applications distantes
0010–0013. Le DDL versionné définit le schéma métier.

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
| `market_candles_history` | Historique OHLCVT pour les simulations | non |
| `capital_accounts`, `capital_events` | Enveloppes et mouvements liés aux clôtures | non |
| `pending_exits` | Intentions de sortie à rapprocher | non |
| `position_policies` | Politiques stratégiques immuables | non |
| `entry_controls` | Permission d’achat révisionnée par mode | non |
| `decision_cycles`, `decision_artifacts`, `decision_cycle_artifacts` | Captures et artefacts dédupliqués | non |
| `opportunity_events` | Parcours causal des opportunités | non |
| `observation_experiments` | Expériences reproductibles | non |
| `protection_observations`, `protection_incidents`, `observation_commands` | Surveillance et commandes HALT | non |
| `execution_requests` | Intentions durables avant mutations exchange | non |
| `schema_migrations` | Migrations appliquées + empreinte SHA-256 | non |

Les lectures financières filtrent `mode` (`PAPER` / `TESTNET` / `LIVE`). Les
politiques et artefacts dédupliqués sont rattachés par identifiant ; l’historique
marché et les registres de migrations ne représentent pas un compte de trading.
Toutes les tables publiques ont la RLS activée et les rôles exposés n’ont pas de
droit de lecture/écriture. Les six vues utilisent `security_invoker`.

La base impose notamment une position ouverte par `(symbol, mode)`, un fill par
`(mode, exchange_order_id)`, la conservation des enveloppes et les types/contraintes
des décisions. La limite d’ordres par heure est contrôlée par le moteur ; l’index
sur les ordres accélère sa lecture et ne constitue pas ce coupe-circuit à lui seul.

## 5. Garde-fous et limites

- Le stop distant réduit l’exposition après un arrêt du moteur ; un gap, le
  glissement ou une panne exchange peuvent dépasser la perte prévue.
- Le pare-feu borne taille, exposition, coûts et risque selon la configuration
  réellement appliquée ; les plafonds dépendent du profil.
- Les drawdowns, pertes consécutives et limites d’ordres peuvent bloquer les
  entrées. Ils ne constituent pas une garantie de capital.
- Les verrous de configuration exigent le mode LIVE, le sandbox désactivé et
  l’acceptation explicite de l’argent réel. Un override de broker ne peut promouvoir
  une configuration PAPER en LIVE.
- Le LLM ne dimensionne pas et n’a aucun avantage statistique démontré. La
  validation économique reste indépendante des tests logiciels.

Nouvelles propositions et briefs techniques : [Améliorations restantes](ROADMAP.md#ameliorations).
