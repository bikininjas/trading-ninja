# AGENTS.md — instructions pour tout agent reprenant ce dépôt

Ce fichier s'adresse à une IA **ou** à un humain qui ouvre le projet sans connaître son
histoire. Il contient ce qui ne se devine pas dans le code. À lire en entier avant
d'agir.

---

## 1. Le projet en trois phrases

Bot de trading **crypto spot, long uniquement**. Un petit modèle local (LLM) propose une
intention d'achat ; un pare-feu déterministe la filtre, la dimensionne ou la refuse ;
l'exécution passe par un exchange via CCXT — aujourd'hui **simulée en papier**. Le LLM
n'a **aucun** pouvoir d'exécution : il ne peut que produire un refus.

## 2. État réel au dernier journal (voir `docs/JOURNAL.md`)

- **Phase 0 terminée**, **phase 1 en cours** : 6 étapes terminées sur 11, 5 en cours.
  La **phase 2** a commencé : 2 étapes sur 4 (une évaluation par bougie, journalisation
  complète des décisions).
- Le moteur **papier** tourne : boucle d'orchestration, flux de marché sur bougies
  clôturées, LLM local, pare-feu, exécution simulée, Profit Sweeper, kill-switch papier,
  API d'administration et dashboard Node.js. Point d'entrée : `python -m app.main --once`
  (un cycle) ou `--serve` (cycles alignés sur les clôtures H4).
- **Rien n'est envoyé à l'exchange.** Aucun chemin d'exécution réelle n'est câblé ;
  `scripts/kill_switch.py` refuse le mode `live`. Aucun argent engagé, aucun ordre réel.
- La base PostgreSQL existe et est à jour (3 migrations appliquées sur Supabase).

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
   levier). Seuls `action`, `confidence`, `market_regime` sont lus, et uniquement pour
   bloquer.
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
  app/engine/              machine à états, sweep, faisabilité
  app/core/settings.py     configuration, presets de risque
  app/core/db.py           résolution de la connexion Postgres
  app/core/env.py          lecture .env / .env.local
  app/market/feed.py       bougies clôturées, EMA/ATR/RSI/MACD
  app/llm/client.py        Ollama natif, sortie contrainte, repli HOLD
  app/execution/paper.py   fills simulés (slippage, frais, stop prioritaire)
  app/runtime/             orchestrateur, réconciliation, cadence, liquidation
  app/api/                 API d'administration (login, status, pause, panic)
  app/backtest/            rejeu historique + golden de régression
  scripts/backtest.py      record / replay / watch
  db/migrations/           schéma SQL (autorité)
  scripts/db_migrate.py    status / apply / verify
  scripts/demo_guardrails.py  démonstration lisible du pare-feu
  scripts/reconcile.py     audit base ↔ exchange, lecture seule
  scripts/verify_drawdowns.py  drawdowns sur jeu figé, transaction annulée
  tests/                   354 tests Python (+ golden de backtest)
```

## 7. Pièges connus de cet environnement

- **Python 3.15.2** vient de l'installation uv, liée dans `~/.pyenv/versions/3.15.2`.
  Le Python système (`/usr/bin/python3`, 3.12.3) est **intact et doit le rester** :
  `apt` en dépend.
- **Supabase, PostgreSQL 17 : TimescaleDB n'existe pas.** C'est pour ça que les tables
  de télémétrie sont partitionnées nativement, avec une partition `DEFAULT`.
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
  phase 3 « testnet » devra être redéfinie (ordres minuscules en réel ou exchange de
  simulation) ; ne jamais supposer qu'un bac à sable existe.

## 8. Prochaine action

`docs/ROADMAP.md`, phase 2 : **2.3 — suivre le résultat des trades refusés** (table
`shadow_trades`, migration 0004), puis 2.4 (rapport de phase). La partie lecture de
1.7 est faite (`scripts/reconcile.py`) ; l'application de son plan attend l'exécuteur
live. Le panic-sell, le kill-switch réels et tout envoi d'ordre attendent un feu vert
explicite de Seb (deux gestes : `TRADING_MODE=live` + `I_UNDERSTAND_REAL_MONEY=true`).

**Décision en attente** : le modèle plafonne à 0,72 de confiance sur les BUY alors que
le profil `micro` exige 0,85 — en production, aucun achat LLM ne passe. Le backtest ne
montre pas d'edge à 0,70 non plus (2 trades, 2 pertes sur un mois). Ne pas baisser le
seuil sans nouvelle mesure : recalibrer le prompt ou accepter que les règles
déterministes portent la stratégie. Voir `docs/BACKTEST.md`.

Décisions déjà prises : Kraken spot, 9,37 EUR (perte totale acceptable), Ollama présent
(qwen3.5:9b, ~1 s avec `think=false`).
