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

## 2. État de reprise — 11 novembre 2026

- Le bot reste en **simulation spot long uniquement** ; aucun capital réel engagé.
  Le chemin live exige les trois verrous de configuration et une réconciliation
  propre. Ne pas confondre une sonde de validation et une exécution réelle.
- Les résultats récents ne démontrent pas de rentabilité robuste. La disponibilité
  d'un exécuteur ne constitue pas une autorisation d'activation. Les historiques
  datés et résultats négatifs sont conservés dans le journal et les décisions.
- Le profil `progressif` est réservé au papier. Il inclut un risque prévu net
  de frais/glissement, un trailing et trois enveloppes : capital actif, réserve et
  cagnotte. Seul le capital actif dimensionne ; les pertes ne sont pas rechargées
  depuis la réserve ou la cagnotte. Voir le capital progressif dans
  `docs/OBSERVATION_ET_RECHERCHE.md`, D-069 et D-070.
- Quinze migrations sont versionnées, dont `0010_capital_ledger.sql` et
  `0015_paper_learning.sql`. Les quinze sont appliquées sur Supabase trninja,
  empreintes et droits vérifiés le 11 novembre 2026. Preuve :
  `docs/audits/2026-11-11/learning-migration-verification.json` ; journal et limites
  de sauvegarde : `docs/JOURNAL.md`.
- 906 tests Python sont collectés, dont 62 contrats PostgreSQL locaux opt-in ;
  32 tests Node et un contrôle navigateur complètent les preuves.
- Le statut reste **NO-GO** pour le réel. Les corrections intégrées et leurs
  preuves sont dans `docs/JOURNAL.md` ; le plan de passage est
  `docs/ROADMAP.md` (R0–R7). L’audit historique de 39db447 reste référencé dans
  `docs/audits/2026-11-09/README.md`. La cible est désormais en 0015, droits
  privilégiés fermés ; l’exploitation et l’économie restent à qualifier.
- D-093 : la mémoire PAPER automatique est livrée dans `app/learning/`. Worker
  distinct `scripts/learning.py --serve`, 20 résultats puis 5 nouveaux et 24 h,
  sources prospectives causales et leçons versionnées. Le groupe NOLOGIN
  `tn_paper_learner` n’a aucun droit d’ordre. Après autorisation explicite de Seb,
  la migration 0015 est appliquée sur Supabase le 11 novembre ; les quinze
  empreintes, le rôle NOLOGIN, les droits et RLS PAPER sont vérifiés. Il reste à
  préparer le LOGIN privé et le service WSL ; aucun processus n’a été activé ici.
- Les dix briefs I-01 à I-10 ont une première version implémentée (D-073) :
  journal/rejeu, diagnostics, suspension, politiques, expériences, coûts/classement,
  parcours, surveillance et scores. Guide : `docs/OBSERVATION_ET_RECHERCHE.md`.
  Classement en observation par défaut, sélection PAPER seulement ; aucune
  promotion ni modification automatique de seuil. La qualification économique et
  l’installation du surveillant restent à faire.
- Chaque position nouvelle garde une politique stratégique immuable. Les limites
  de sécurité restent courantes. Une position historique sans politique conserve
  stop/TP ; adoption explicite PAPER + TOTP, aucune substitution silencieuse.
- La capture indisponible suspend les nouveaux achats mais laisse gérer l’existant.
  Un paquet incomplet est non rejouable. Le rejeu appelle le véritable runtime avec
  I/O enregistrées, sans SQL, exchange ni nouvelle inférence.
- Le budget de perte totale de 9,37 EUR et le modèle `qwen3.5:9b` sont déjà
  documentés. Les minima Kraken peuvent empêcher les achats ; ne pas relâcher le
  risque ni augmenter le budget pour les financer sans décision explicite.
- D-088 : Seb a explicitement demandé un prudent un peu assoupli. Le candidat
  PAPER utilise 0,75 % de risque et 30 % d'exposition via surcharges, preset
  d'origine conservé. Révision souhaitée SQL 1, non appliquée par un moteur.
  Les deux profils refusent tous les achats sur la mesure mécanique 2026.
- D-089 : quatre groupes SQL PAPER sans LOGIN sont installés. Les LOGIN et
  secrets distincts restent à créer dans le stockage privé du WSL. Le prévol
  refuse une connexion administrateur ; ne pas remplacer ces groupes par postgres.
- D-090 : trois nouveaux profils PAPER manuels `petit_budget`, `micro_budget`,
  `nano_budget`. Guide `docs/PROFILS_PETITS_BUDGETS.md`. Les exemples de services
  ciblent micro, mais la révision SQL souhaitée reste prudent assoupli : sélectionner
  le profil via l'API avant la recette WSL. Les rejeux mécaniques micro/nano ont
  exécuté deux trades perdants puis déclenché l'arrêt ; aucune rentabilité validée.
- `docs/ROADMAP.md` donne l'avancement ; `docs/JOURNAL.md` les preuves datées.
  `docs/CODE_INDEX.md` est généré depuis le code, jamais rédigé manuellement.

## 3. Ordre d'autorité — en cas de contradiction

Les résultats datés décrivent leur version du code. En cas de contradiction sur
le comportement actuel, appliquer cet ordre :

| Rang | Source | Portée |
|---|---|---|
| 1 | `backend/db/migrations/*.sql` | Schéma, énumérations, contraintes |
| 2 | `backend/app/engine/risk_gate.py` | Logique de risque et codes de rejet |
| 3 | `docs/DECISIONS.md` | Le pourquoi de chaque choix |
| 4 | `docs/ROADMAP.md` | Avancement, portes du go live et améliorations ouvertes |

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
docs/ROADMAP.md            étapes, statuts, critères de passage
docs/JOURNAL.md            historique daté + commande de vérification
docs/DECISIONS.md          décisions datées, le pourquoi
docs/ARCHITECTURE.md       couches, modules, modèle de données
docs/OBSERVATION_ET_RECHERCHE.md  commandes, API et limites des observations
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
  scripts/insights.py     replay / experiment / export / diagnose / confidence / watch
  scripts/verify_drawdowns.py  drawdowns sur jeu figé, transaction annulée
  tests/                   tests Python et golden ; décompte dans CODE_INDEX.md
```

## Runtime de livraison

Python **3.13.17**, indiqué par `.python-version` et `backend/.python-version`,
est le runtime de livraison qualifié avec `backend/requirements.lock`. L’ancien
Python de travail 3.15.2 reste installé ; ne pas modifier le Python système.
La CI exécute les contrats complets sur un PostgreSQL jetable, jamais sur Supabase.

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
- **Le dashboard écoute sur `127.0.0.1:8081` par défaut.** L’ancienne installation
  utilisait `0.0.0.0` à la demande de Seb ; l’écoute effective de la cible reste à
  qualifier. L’API utilise `127.0.0.1:8080`. Un accès distant exige le réglage
  explicite, un accès privé/TLS et la recette R3.
- **Ollama** : l'endpoint OpenAI-compatible ignore `think` et `format`. Le client
  utilise `/api/chat` natif avec `think=false`, timeout 8 s (D-028).
- **Kraken spot n'a pas de testnet** dans CCXT 4.5.91 (`urls['test']` absent). Le
  canary R6 est donc un **pré-live réel** : un aller-retour réel minuscule,
  sous-compte dédié, après validation à blanc. Ne jamais supposer qu'un bac à sable
  existe.

## 8. Prochaine action

La cible retenue par Seb est son WSL domestique, actuellement inaccessible.
`deploy/` fournit le lanceur, les unités et l'installation PAPER à préparer avant
son go. Seb a retiré le 10 novembre le dump complet initial des prérequis bloquants
puisque positions, ordres, intentions et ledger financier sont vides. L'outillage
de sauvegarde reste optionnel ; ne pas présenter sa recette locale comme une
restauration de Supabase. Décisions D-084 à D-086, procédure `deploy/README.md`.

Lire la roadmap et le dernier journal avant d'agir. Les mesures économiques restent
bloquantes pour l'argent réel. Les briefs I-01 à I-10 ont leur version D-073 ; accumuler les preuves économiques
et qualifier le déploiement décrit dans le guide. Ne pas confondre tests locaux,
installation des migrations sur la cible et fonctionnement réel du surveillant.
Ne pas réintroduire de brief périmé comme spécification du code actuel ; les choix
historiques sont conservés dans `docs/DECISIONS.md` et `docs/JOURNAL.md`.
