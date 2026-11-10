# Trading Ninja — backend

Moteur d'exécution déterministe. Le LLM propose, la Risk Gateway dispose :
aucun ordre ne part sans franchir un pare-feu mathématique fail-closed.

## Observation et recherche

Les dix briefs sont implémentés en première version : voir
[`OBSERVATION_ET_RECHERCHE.md`](../docs/OBSERVATION_ET_RECHERCHE.md) pour les
contrats JSON, API, CLI et limites. `scripts/insights.py --help` donne les commandes.
Replay, expériences et analyse de confiance par fichier fonctionnent hors ligne.
Les migrations 0001–0013 sont appliquées sur trninja, vérifiées le 10 novembre
2026 : [preuve de déploiement](../docs/JOURNAL.md#deploiement-supabase).
Sur une autre base, vérifier les migrations avant de démarrer l’instrumentation.

Les tests n’utilisent aucune clé du shell et interdisent les connexions externes.
La suite normale collecte 765 tests : 729 passent hors base, 36 contrats SQL sont
ignorés sans base jetable. Pour tout vérifier, démarrer PostgreSQL 17 local avec
les rôles `anon`/`authenticated`, appliquer les treize migrations et utiliser :

```bash
TN_TEST_DATABASE_URL="$TN_REVIEW_DSN" .venv/bin/python -m pytest \
  --postgres-test-url "$TN_REVIEW_DSN"
```

`TN_REVIEW_DSN` doit viser exclusivement `127.0.0.1`/`localhost`, base
`trading_ninja_review` jetable. Les tests SQL annulent leurs écritures ; ils ne
valident pas le schéma Supabase déployé. Le golden est inclus dans la suite.

## Outillage qualifié

| Outil | Référence de livraison |
|---|---|
| Python | **3.13.17**, `.python-version` à la racine et dans `backend/` |
| Dépendances runtime et tests | `requirements.lock`, versions exactes |
| Node.js | **24**, utilisé par la CI ; dashboard sans dépendance npm |
| PostgreSQL | **17**, local jetable pour les tests, Supabase pour le déploiement |
| uv | Création du venv et synchronisation du lock |

L’ancien Python de travail 3.15.2 peut rester installé. Le Python système doit
rester intact ; toutes les commandes projet utilisent le venv.

## Installation

```bash
cd backend
uv venv --python 3.13.17 .venv        # respecte .python-version
uv pip sync --python .venv/bin/python requirements.lock
```

## Commandes

Le profil papier `progressif` et le comparatif de budgets sont décrits dans
[Capital progressif](../docs/OBSERVATION_ET_RECHERCHE.md#capital-progressif-paper). L'exemple utilise 9,37 EUR
virtuels ; il ne modifie pas les réglages locaux existants.

```bash
.venv/bin/python -m pytest                     # 765 tests collectés ; 36 PostgreSQL opt-in, voir ci-dessous
.venv/bin/python -m app.main --once            # un cycle papier complet (données réelles)
.venv/bin/python -m app.main --serve           # une évaluation par bougie H4 clôturée
.venv/bin/python -m app.main --status          # état + 3 dernières lignes par table
.venv/bin/python scripts/backtest.py scenario --stop-atr 3.0 --take-profit-r 2.0
.venv/bin/python scripts/short_lab.py --require-negative-h1  # mandat short, labo sans ordre
.venv/bin/python scripts/reconcile.py          # audit base ↔ exchange (lecture seule)
.venv/bin/python scripts/reconcile.py --mode live --apply --confirm  # liquide les positions non protégées
.venv/bin/python scripts/verify_drawdowns.py   # drawdowns sur jeu figé, transaction annulée
.venv/bin/python scripts/kill_switch.py --dry-run  # ce que ferait un arrêt d'urgence
.venv/bin/python scripts/kill_switch.py            # liquide et halte (papier)
.venv/bin/python scripts/kill_switch.py --confirm-live  # liquide et halte (réel)
.venv/bin/python scripts/hash_password.py          # hash du mot de passe admin
.venv/bin/python scripts/totp_setup.py             # secret TOTP + QR (Authy)
.venv/bin/python scripts/backtest.py record        # golden : décisions LLM (~2 min)
.venv/bin/python scripts/backtest.py replay        # régression déterministe (< 1 s)
.venv/bin/python scripts/compare_budgets.py        # budgets 9,37 / 25 / 50 / 1 000 EUR
.venv/bin/python scripts/backtest.py watch         # veille de dérive
.venv/bin/python scripts/backtest.py measure       # règle + LLM sur les 7 paires (réseau + LLM)
.venv/bin/python scripts/backtest.py swing --months 60   # swing macro D1, sans LLM
.venv/bin/python scripts/backtest.py meanrev --months 60 # mean reversion BB, laboratoire sans LLM
.venv/bin/python scripts/live_probe.py --confirm-live    # validation privée validate=true, aucun ordre
.venv/bin/python scripts/lab_rotation.py --months 60 --abs-filter dual  # rotation + momentum absolu, laboratoire
.venv/bin/python -m uvicorn app.api.main:create_app --factory \
    --host 127.0.0.1 --port 8080                   # API d'administration
.venv/bin/python scripts/db_migrate.py status  # état des migrations
.venv/bin/python scripts/db_migrate.py apply   # applique les migrations
.venv/bin/python scripts/db_migrate.py verify  # schéma + test d'écriture
.venv/bin/python scripts/shadow_report.py      # achats non pris : résumé
.venv/bin/python scripts/shadow_report.py --evaluate  # rejoue les bougies et ferme les OPEN
.venv/bin/python scripts/lessons.py show       # leçons actives
.venv/bin/python scripts/lessons.py review     # un appel LLM, stocke 1 à 5 leçons
.venv/bin/python scripts/check_exchange.py     # sonde historique Binance (lecture seule), pas le contrôle Kraken
.venv/bin/python scripts/check_kraken.py       # marchés, permissions, équité requise (lecture seule)
PYTHONPATH=. .venv/bin/python scripts/demo_guardrails.py
```

## Gestion des processus

Depuis la racine du dépôt :

```bash
scripts/manage.sh start all      # API + dashboard en arrière-plan
scripts/manage.sh status
scripts/manage.sh restart dashboard
scripts/manage.sh logs all
scripts/manage.sh stop all
```

Fichiers PID et journaux dans `scripts/.run/` (ignoré par git). Un PID n'est tué
qu'après vérification de sa ligne de commande.

## Exchange

La cible est **Kraken spot**. Les contrats d’exécution, les clés, la reprise et
les limites de qualification sont dans [`../docs/KRAKEN.md`](../docs/KRAKEN.md).

## Index du code

`docs/CODE_INDEX.md` est la carte ligne par ligne du dépôt. Il est **généré** ;
commandes depuis la racine du dépôt :

```bash
backend/.venv/bin/python scripts/index_codebase.py          # régénère l'index
backend/.venv/bin/python scripts/index_codebase.py --check  # échoue s'il est périmé
```

Après toute modification de code, régénérer l'index — `bash scripts/check.sh` le vérifie.

## Base de données

PostgreSQL 17 (Supabase, eu-west-1). La connexion est résolue depuis
`DATABASE_URL`, sinon dérivée de `NEXT_PUBLIC_SUPABASE_URL` + mot de passe
présents dans `.env.local` à la racine du dépôt.

La connexion directe Supabase est IPv6 uniquement. Sur un réseau IPv4
(Docker sans IPv6, VPS), passer par le pooler :

```bash
SUPABASE_USE_POOLER=true
SUPABASE_POOLER_REGION=eu-west-1
```

## Sécurité

- `.env.local` n'est jamais suivi par git (`.gitignore` + crochet `pre-commit`).
- Le crochet refuse aussi les secrets collés dans un fichier anodin.
- Activer sur un nouveau clone : `git config core.hooksPath .githooks`.
- Le mode réel exige deux gestes explicites : `TRADING_MODE=live` **et**
  `I_UNDERSTAND_REAL_MONEY=true`.


## Vérification des enveloppes et du navigateur

Les 35 contrats PostgreSQL utilisent exclusivement une base locale jetable
`trading_ninja_review`, avec les rôles `anon` et `authenticated` et les treize
migrations. Les anciens contrats d’enveloppes exigent le port 55432.
La commande complète et les garde-fous de connexion figurent en tête de ce guide.

Pour le contrôle navigateur depuis la racine du dépôt, utiliser un Python muni de
Playwright 1.62.0 et Chromium :

```bash
backend/.venv/bin/python scripts/check_dashboard_browser.py /tmp/captures-capital
```

Ce contrôle sert une interface avec réponses factices : aucun serveur de trading,
compte réel ou ordre. Il est exécuté par la CI, séparément de `scripts/check.sh`.
Les règles d’enveloppes sont dans [Capital progressif](../docs/OBSERVATION_ET_RECHERCHE.md#capital-progressif-paper) ;
les évolutions encore ouvertes dans les [briefs](../docs/ROADMAP.md#ameliorations).
