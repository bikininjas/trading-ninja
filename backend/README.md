# Trading Ninja — backend

Moteur d'exécution déterministe. Le LLM propose, la Risk Gateway dispose :
aucun ordre ne part sans franchir un pare-feu mathématique fail-closed.

## Observation et recherche

Les dix briefs sont implémentés en première version : voir
[`OBSERVATION_ET_RECHERCHE.md`](../docs/OBSERVATION_ET_RECHERCHE.md) pour les
contrats JSON, API, CLI et limites. `scripts/insights.py --help` donne les commandes.
Replay, expériences et analyse de confiance par fichier fonctionnent hors ligne.
Appliquer 0011 sur la base cible avant de démarrer l’instrumentation.

Les tests n’utilisent aucune clé du shell et interdisent les connexions externes.
La suite normale collecte 703 tests : 677 passent hors base, 26 contrats SQL sont
ignorés sans base jetable. Pour tout vérifier, démarrer PostgreSQL 17 local avec
les rôles `anon`/`authenticated`, appliquer les onze migrations et utiliser :

```bash
TN_TEST_DATABASE_URL="$TN_REVIEW_DSN" .venv/bin/python -m pytest \
  --postgres-test-url "$TN_REVIEW_DSN"
```

`TN_REVIEW_DSN` doit viser exclusivement `127.0.0.1`/`localhost`, base
`trading_ninja_review` jetable. Les tests SQL annulent leurs écritures ; ils ne
valident pas le schéma Supabase déployé. Le golden est inclus dans la suite.

## Outillage

| Outil | Version | Emplacement |
|---|---|---|
| Python | 3.15.2 | `~/.local/share/uv/python/` (lien `~/.pyenv/versions/3.15.2`) |
| Node.js | 26.13.0 LTS | `~/.local/node/current` |
| npm | 11.20.0 | préfixe global `~/.local` |
| uv | 0.13.7 | `~/.local/bin/uv` |

Le Python système (`/usr/bin/python3`, 3.12.3) n'a **pas** été touché :
Ubuntu 24.04 et `apt` en dépendent. Les versions récentes vivent dans
`~/.local`, qui est en tête de `PATH`.

## Installation

```bash
cd backend
uv venv --python 3.15.2 .venv        # respecte .python-version
uv pip install -r requirements.txt -r requirements-dev.txt
```

## Commandes

Le profil papier `progressif` et le comparatif de budgets sont décrits dans
[`../docs/PROGRESSION.md`](../docs/PROGRESSION.md). L'exemple utilise 9,37 EUR
virtuels ; il ne modifie pas les réglages locaux existants.

```bash
.venv/bin/python -m pytest                     # 703 tests collectés ; 24 PostgreSQL opt-in, voir ci-dessous
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
.venv/bin/python scripts/live_probe.py --confirm-live    # sonde 3.1 : ordre réel volontairement rejeté
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

La cible est **Kraken spot** (étape 1.6). Les écarts avec le brief d'origine, les
contraintes mesurées et ce qui reste à écrire sont dans [`../docs/KRAKEN.md`](../docs/KRAKEN.md).

## Index du code

`docs/CODE_INDEX.md` est la carte ligne par ligne du dépôt. Il est **généré** :

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


## Vérification des enveloppes et de PostgreSQL

La suite collecte 643 tests Python, dont 6 contrats SQL opt-in ; sans base locale,
ces six tests sont sautés. Pour les exécuter, utiliser une base PostgreSQL 17
**jetable locale**, migrée par `scripts/db_migrate.py apply`, avec les rôles
`anon` et `authenticated` créés pour le contrôle des droits. Les tests refusent
une cible autre que localhost:55432 et annulent chaque transaction.

```bash
TN_TEST_DATABASE_URL='postgresql://postgres:mot_de_passe_local@127.0.0.1:55432/base_test' .venv/bin/python -m pytest tests/test_capital_postgres.py
# Navigateur optionnel : Python avec Playwright et Chromium installés.
python scripts/check_dashboard_browser.py /tmp/captures-capital  # depuis la racine du dépôt
```

Les règles, l'initialisation d'un compte papier existant et les limites des paliers
sont dans `docs/PROGRESSION.md`. Le plafond actif de l'exemple est 20 EUR ; il ne
force aucune réduction d'une enveloppe initiale plus grande, mais bloque sa promotion.

Nouvelles propositions et briefs techniques : [IDEES_ET_BRIEFS_TECHNIQUES.md](../docs/IDEES_ET_BRIEFS_TECHNIQUES.md).
