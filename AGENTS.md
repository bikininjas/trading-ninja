# AGENTS.md — instructions pour tout agent reprenant ce dépôt

Ce fichier s'adresse à une IA **ou** à un humain qui ouvre le projet sans connaître son
histoire. Il contient ce qui ne se devine pas dans le code. À lire en entier avant
d'agir.

---

## 1. Le projet en trois phrases

Bot de trading **crypto spot, long uniquement**. Un petit modèle local (LLM) propose une
intention d'achat, de vente ou d'arrêt ; un pare-feu déterministe la filtre, la
dimensionne ou la refuse ; l'exécution passe par un exchange via CCXT — aujourd'hui
**simulée en papier**. Le LLM peut déclencher, mais il ne dimensionne rien : taille,
prix et stop sont toujours calculés par le code.

## 2. État réel au dernier journal (voir `docs/JOURNAL.md`)

- **Phase 0 terminée**, **phase 1 en cours** : 6 étapes terminées sur 11, 5 en cours.
  La **phase 2** a commencé : 2.1, 2.2 et 2.11 sont terminées, les étapes 2.3 à 2.9
  restent en cours. Le mode **`SWING_MACRO_D1`** (D-060) est implémenté et mesuré :
  négatif sur 2021-2026, positif seulement sur l'historique complet, non activé.
- **Historique multi-années en base** (migration 0008, étape 2.8) : BTC/EUR depuis
  2013, ETH/EUR depuis 2015, SOL/EUR depuis 2021 en H1 et H4 (~158 600 bougies),
  importé des archives OHLCVT publiques de Kraken **par requêtes HTTP Range**
  (`scripts/ingest_history.py`). Cohérence H1 → H4 vérifiée, trous signalés et jamais
  interpolés.
- Le moteur **papier** tourne par défaut sur une **équité virtuelle de 1 000 EUR**
  (`PAPER_START_EQUITY`), découplée du solde réel : le sizing est éprouvable sans
  engager un centime. Point d'entrée : `python -m app.main --once` (un cycle) ou
  `--serve` (cycles alignés sur les H4).
- **Le chemin réel existe mais n'est pas activé.** `app/execution/live.py` envoie un
  ordre marché avec stop bracket, vérifie le stop après le fill et solde si le stop
  manque. `main.py` exige `TRADING_MODE=live` + `EXCHANGE_SANDBOX_MODE=false` +
  `I_UNDERSTAND_REAL_MONEY=true`, **et** une réconciliation propre au démarrage.
  Aucun ordre réel n'a été envoyé à ce jour ; le sous-compte dédié reste à créer.
- La base PostgreSQL existe et est à jour (8 migrations appliquées sur Supabase).

## 3. Ordre d'autorité — en cas de contradiction

Les documents se contredisent ; c'est normal, ils viennent d'époques différentes. Quand
deux sources s'opposent, celle du haut gagne :

| Rang | Source | Portée |
|---|---|---|
| 1 | `backend/db/migrations/*.sql` | Schéma, énumérations, contraintes |
| 2 | `backend/app/engine/risk_gate.py` | Logique de risque et codes de rejet |
| 3 | `docs/DECISIONS.md` | Le pourquoi de chaque choix |
| 4 | `docs/ROADMAP.md` | Avancement et critères de passage |
| 5 | `PROMPT_*.md` | **Historique et périmé.** Ne jamais appliquer tel quel. |

Le rang 1 et le rang 2 doivent rester **identiques** : le test
`backend/tests/test_schema_contract.py` compare mécaniquement l'énumération
`risk_rejection_code` du SQL aux `StrEnum` Python et échoue sinon.

## 4. Règles non négociables

1. **Ne jamais committer `.env` ni `.env.local`.** Ils contiennent les identifiants
   Supabase et exchange. Trois protections existent (`.gitignore`, crochet `pre-commit`,
   `scripts/check.sh`) — ne jamais en désactiver une.
2. **Ne jamais contourner le Risk Gateway.** Aucun chemin de code ne peut envoyer un
   ordre sans passer par `evaluate()`. Un ordre sans stop distant n'a pas le droit
   d'exister.
3. **Ne jamais activer l'argent réel sans demande explicite de Seb.** Le mode réel
   exige deux gestes (`TRADING_MODE=live` + `I_UNDERSTAND_REAL_MONEY=true`). Ne pas les
   poser de sa propre initiative, même pour « tester ».
4. **Toute modification de schéma = nouvelle migration.** Jamais d'édition d'une
   migration déjà appliquée : `db_migrate.py status` la signalerait comme `MODIFIÉE`.
5. **Une étape commence → 🟨 `en cours` + date dans `docs/ROADMAP.md` + entrée dans
   `docs/JOURNAL.md`.** Une étape ne passe en ✅ que si une commande réelle l'a prouvé.
6. **Argent en `Decimal`, jamais en flottant.** `NUMERIC(28,10)` en base.
7. **Aucune valeur numérique émise par le LLM n'est utilisée** (taille, prix, stop,
   levier). Seuls `action`, `confidence`, `market_regime` sont lus. `action` peut
   déclencher un BUY, un SELL de clôture ou un HALT, mais la taille, le prix et le stop
   restent calculés par le code ; un SELL ne peut jamais créer un short.
8. **Le dashboard est une interface d'administration.** `DASHBOARD_HOST=0.0.0.0` exige
   `DASHBOARD_ALLOW_REMOTE=true` ; sans TLS, le mot de passe et le code Authy circulent
   en clair. Ne jamais l'ouvrir sur Internet.
9. **Toute modification de `backend/app/`, du risque ou du prompt passe le backtest de
   régression avant le commit.** Le golden rejoue des décisions figées ; s'il échoue,
   relire le diff, puis `replay --bless` ou `record` — jamais bénir à l'aveugle.

## 5. Vérifier l'état en une commande

```bash
bash scripts/check.sh
```

Sortie verte = outillage, secrets, tests, migrations et documentation cohérents. Si
c'est rouge, le message dit quoi faire. C'est la première commande à lancer en arrivant.

## 6. Carte du dépôt

```
README.md                  porte d'entrée
AGENTS.md                  ce fichier
PROMPT_*.md                specs d'origine — PÉRIMÉES, voir §3
docs/ROADMAP.md            étapes, statuts, critères de passage
docs/JOURNAL.md            historique daté + commande de vérification
docs/DECISIONS.md          D-001 … D-042, le pourquoi
docs/ARCHITECTURE.md       couches, modules, modèle de données
scripts/check.sh           vérification globale
scripts/manage.sh          start/stop/restart api|dashboard|all
scripts/backtest_watch.sh  veille hebdomadaire (cron, silencieuse)
dashboard/                 dashboard Node.js (zéro dépendance)
backend/
  app/engine/risk_gate.py  LE pare-feu (module pur, sans I/O)
  app/engine/entry_rule.py  quand le LLM est consulté (pullback EMA50)
  app/engine/exit_guard.py  SELL/HALT du LLM : jamais de short
  app/engine/              machine à états, sweep, faisabilité
  app/core/settings.py     configuration, presets de risque
  app/core/db.py           résolution de la connexion Postgres
  app/core/env.py          lecture .env / .env.local
  app/market/feed.py       bougies clôturées, EMA/ATR/RSI/MACD
  app/market/kraken_archive.py  lecture ZIP Kraken par requêtes HTTP Range
  app/market/history.py    historique en base : CSV, trous, agrégation, cohérence
  app/llm/client.py        Ollama natif, sortie contrainte, repli HOLD
  app/execution/paper.py   fills simulés (slippage, frais, stop prioritaire)
  app/execution/live.py    seul appelant de create_order : bracket, stop vérifié
  app/execution/factory.py choix paper/live/testnet refusé
  app/runtime/             orchestrateur, réconciliation, cadence, liquidation
  app/api/                 API d'administration (login, status, pause, panic)
  app/backtest/            rejeu historique + golden de régression
  scripts/backtest.py      record / replay / watch / measure / sweep / swing
  scripts/ingest_history.py  archives Kraken → market_candles_history
  db/migrations/           schéma SQL (autorité)
  scripts/db_migrate.py    status / apply / verify
  scripts/demo_guardrails.py  démonstration lisible du pare-feu
  scripts/reconcile.py     audit base ↔ exchange ; --apply --confirm en live
  scripts/shadow_report.py achats non pris : résumé + évaluation stop/TP
  scripts/lessons.py      relecture des résultats → leçons actives
  scripts/verify_drawdowns.py  drawdowns sur jeu figé, transaction annulée
  tests/                   603 tests Python (+ golden de backtest)
```

## 7. Pièges connus de cet environnement

- **Python 3.15.2** vient de l'installation uv, liée dans `~/.pyenv/versions/3.15.2`.
  Le Python système (`/usr/bin/python3`, 3.12.3) est **intact et doit le rester** :
  `apt` en dépend.
- **Supabase, PostgreSQL 17 : TimescaleDB n'existe pas.** C'est pour ça que les tables
  de télémétrie sont partitionnées nativement, avec une partition `DEFAULT`.
- **Le plan Supabase fait 500 Mo, avec un plancher de 100 Mo libres.** Toute écriture
  d'historique passe par `store_within_budget()` : la taille réelle est mesurée
  (`pg_database_size`), et un lot qui laisserait moins de 100 Mo libres est refusé
  (`SUPABASE_DB_MIN_FREE_MB`) plutôt qu'écrit. Un quota plein ne casse pas seulement
  l'ingestion, il casse *toutes* les écritures, journal du bot compris. Voir D-057.
- **La connexion directe Supabase est IPv6 uniquement.** Sur un réseau IPv4, utiliser
  le pooler : `SUPABASE_USE_POOLER=true` et `SUPABASE_POOLER_REGION=eu-west-1`.
  Derrière un pooler en mode transaction, asyncpg exige `statement_cache_size=0`
  (déjà géré dans `app/core/db.py`).
- **Les vues doivent rester en `security_invoker`.** Sinon elles contournent la RLS et
  exposent les données à la clé publique Supabase (incident déjà survenu, D-011).
- **Les clés exchange vivent dans `.env.local` (racine)** : `KRAKEN_API_KEY` /
  `KRAKEN_SECRET_KEY`, plus les anciennes `BINANCE_API_KEY` / `BINANCE_SECRET_KEY`.
  Le code accepte aussi `EXCHANGE_API_KEY` / `EXCHANGE_SECRET`, prioritaires.
- **Le dashboard écoute sur `0.0.0.0:8081`** (choix de Seb) : HTTP sans TLS, mot de
  passe et code Authy en clair sur le réseau. L'API reste sur `127.0.0.1:8080` ; ne
  jamais ouvrir le port 8081 sur Internet.
- **Ollama** : l'endpoint OpenAI-compatible ignore `think` et `format`. Le client
  utilise `/api/chat` natif avec `think=false`, timeout 8 s (D-028).
- **Kraken spot n'a pas de testnet** dans CCXT 4.5.91 (`urls['test']` absent). La
  phase 3 est donc redéfinie en **pré-live canary** : un aller-retour réel minuscule,
  sous-compte dédié, après validation à blanc. Ne jamais supposer qu'un bac à sable
  existe.

## 8. Prochaine action

`docs/ROADMAP.md`, phase 2 : **2.9 — balayage statistique** est mesuré et négatif,
deux fois. 48 scénarios sur 24 mois (3 paires × 2 modes d'entrée × 4 pentes × 2 niveaux
de frais) : **aucun positif**, PF de 0,11 à 0,62, N de 14 à 39 trades par paire. Le
repli H1 (D-058) ne débloque ni N ni le signe. Deux artefacts ont été corrigés en
route : la bande RSI 38–45 du brief était **vide par construction** (un reclaim
d'EMA50 H1 a un RSI de 45–60), et les coupe-circuits permanents tronquaient la fenêtre
(D-059 — d'où la colonne `gel` et les options qui les lèvent). Le levier n'est ni la
profondeur d'historique (2.8 est fait : 13 ans en base), ni les frais, ni la géométrie
de sortie, ni un troisième filtre : c'est la **cadence de décision** (une clôture H4 ne
voit qu'une opportunité H1 sur quatre) ou la **couverture multi-paires** (95 trades sur
les 3 paires à pente 0, contre 39 au mieux sur une seule). Protocole inchangé :

```bash
cd backend && .venv/bin/python scripts/backtest.py sweep --months 24 \
  --entry-modes EMA50_H4,PULLBACK_H1 --max-consecutive-losses 9999 --max-hwm-drawdown 1.0
```

Ne pas figer un filtre sur 4 à 39 trades. Le chantier 4 (ordres Maker) reste **gelé**
tant que le PF ne dépasse pas 1,30 : optimiser l'exécution d'une stratégie perdante ne
fait que perdre plus lentement. **2.3 est fait** (table `shadow_trades`, rapport
`scripts/shadow_report.py`), **2.5** (LLM autonome BUY/SELL/HALT) et **2.6** (mémoire
de leçons) restent en cours de mesure, **2.7** (short) est un laboratoire isolé.
Ensuite 2.4 (rapport de phase).

**2.11 — swing macro D1** est implémenté (`ENTRY_MODE=SWING_MACRO_D1`,
`STOP_MODE=STRUCTURAL_D1`, preset `swing`, `backtest.py swing`). Mesure 2021-11 →
2026-11, 80 pb/côté, coupe-circuits levés : BTC PF 0,44, ETH 0,57, SOL 0,19 —
**aucun scénario positif sur la fenêtre demandée**. Tout l'historique : BTC `both`
PF 2,20 (87 trades, 7,1/an, réussite 17,2 %), BTC Donchian PF 4,23 (37 trades, 3,0/an),
ETH `value` PF 1,67 — mais la réussite de 40 % n'est jamais atteinte et l'avantage
disparaît sur la fenêtre récente. **Le mode n'est pas activé.** Prochain levier : un
filtre de régime ou une sortie moins whipsaw que l'EMA20, pas un quatrième filtre
d'entrée.

Le chemin live est écrit : `app/execution/live.py` (bracket + stop vérifié + sortie
d'urgence), `app/execution/factory.py`, `main.py` avec réconciliation obligatoire au
démarrage, `scripts/reconcile.py --apply --confirm` et `kill_switch.py --confirm-live`.
**Il n'est pas activé** : aucun ordre réel n'a été envoyé. Pour l'activer il faut un
sous-compte dédié (aucun solde manuel sur les paires), le montant que Seb accepte de
perdre, puis les trois verrous `TRADING_MODE=live` + `EXCHANGE_SANDBOX_MODE=false` +
`I_UNDERSTAND_REAL_MONEY=true`, et une réconciliation propre.

**Décision en attente** : la mesure v3 avec les frais réels (0,80 %/côté) donne 4
trades, 0 gagnant, −0,6951 EUR ; aucun scénario testé n'est positif. Le prochain
levier est l'entrée, pas l'exécution. Ne pas activer le réel avant une mesure positive
nette de frais.

Décisions déjà prises : Kraken spot, 9,37 EUR (perte totale acceptable), Ollama présent
(qwen3.5:9b, ~1 s avec `think=false`).
