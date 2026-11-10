# Trading Ninja

Bot de trading déterministe sur crypto spot. Le LLM propose, un pare-feu
mathématique dispose : aucun ordre ne part sans franchir une Risk Gateway
fail-closed.

**État actuel : simulation ; le chemin réel existe mais n'est pas activé.**

**État au 10 novembre 2026 : NO-GO pour le réel.** Les correctifs de sécurité
sont publiés et Supabase trninja est à jour jusqu’à `0013`, droits et intégrité
vérifiés. La sauvegarde complète, l’exploitation et la qualification économique
restent à démontrer dans [le plan R0–R7](docs/GO_LIVE.md).

L'objectif est une progression modeste dans la durée, nette de frais, avec des
pertes contenues. Le profil papier **`progressif`** inclut les coûts dans le risque
et laisse courir les gagnants avec un stop suiveur. Budget d'exemple : **9,37 EUR
virtuels** ; un budget insuffisant peut empêcher tout achat. Règles et limites :
[`docs/PROGRESSION.md`](docs/PROGRESSION.md).

## Où regarder

| Fichier | Contenu |
|---|---|
| [`docs/CORRECTIONS_PRE_GO_LIVE.md`](docs/CORRECTIONS_PRE_GO_LIVE.md) | Correctifs publiés, preuves et limites de qualification |
| [`docs/DEPLOIEMENT_SUPABASE_2026-11-10.md`](docs/DEPLOIEMENT_SUPABASE_2026-11-10.md) | Migrations réellement appliquées, intégrité et droits de la cible |
| [`docs/GO_LIVE.md`](docs/GO_LIVE.md) | Plan unique R0–R7 jusqu’au go live définitif, critères et responsabilités |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | Les étapes, leur statut, et le critère qui permet de passer à la suivante |
| [`docs/JOURNAL.md`](docs/JOURNAL.md) | Ce qui a été fait, quand, et par quelle commande c'est vérifié |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | Pourquoi c'est construit comme ça (une décision par numéro) |
| [`backend/README.md`](backend/README.md) | Installation, commandes, outillage |
| [`dashboard/README.md`](dashboard/README.md) | Dashboard de supervision (Node.js, zéro dépendance) |
| [`docs/DASHBOARD.md`](docs/DASHBOARD.md) | Architecture, panneaux, contrat API et sécurité du dashboard |
| [`docs/BACKTEST.md`](docs/BACKTEST.md) | Backtest historique, golden et tests récurrents |
| [`docs/IDEES_ET_BRIEFS_TECHNIQUES.md`](docs/IDEES_ET_BRIEFS_TECHNIQUES.md) | Dix briefs livrés et besoins complémentaires encore ouverts |
| [`docs/OBSERVATION_ET_RECHERCHE.md`](docs/OBSERVATION_ET_RECHERCHE.md) | Journal/rejeu, diagnostics, suspension, politiques, expériences et surveillance |
| [`docs/PROGRESSION.md`](docs/PROGRESSION.md) | Profil progressif papier, enveloppes de capital et limites |

## Démarrage rapide

```bash
cd backend
uv venv --python 3.13.17 .venv
uv pip sync --python .venv/bin/python requirements.lock
.venv/bin/python -m pytest
.venv/bin/python scripts/compare_budgets.py  # hors ligne, sans argent réel
.venv/bin/python scripts/db_migrate.py status  # vérifier la cible configurée
```

Les tests PostgreSQL exigent une base locale jetable ; procédure complète dans
[backend/README.md](backend/README.md). Le runtime qualifié est Python 3.13.17 ;
la CI utilise Node.js 24.

## Dashboard

Interface de supervision : état, équité, position, jauges de risque, courbe du
capital, parcours des opportunités, diagnostics et rejeu, comparaisons papier,
refus du pare-feu, activité par onglets, réglages versionnés, suspension des achats, pause,
reprise et panic-sell à deux facteurs. Détails dans
[`docs/DASHBOARD.md`](docs/DASHBOARD.md).

```bash
scripts/manage.sh start all      # API + dashboard en arrière-plan
scripts/manage.sh status
scripts/manage.sh logs all
scripts/manage.sh stop all
```

Chaque composant se pilote seul : `start api`, `restart dashboard`, etc. Journaux et
fichiers PID dans `scripts/.run/` (ignoré par git). L'API reste sur `127.0.0.1:8080`
(voir `dashboard/README.md`).
Le jeton de l'API ne quitte jamais le processus Node : le navigateur ne reçoit qu'un
cookie de session `HttpOnly`.

## Les trois règles du projet

1. **Le risque est dans la configuration, jamais dans le code.** Faire évoluer le profil
   est une édition de `.env`.
2. **Une étape n'est terminée que vérifiée par une commande réelle.** Pas d'intention,
   pas de « ça devrait marcher ».
3. **Le montant engagé est celui dont la perte totale ne change rien.** On monte en
   risque après avoir mesuré, pas avant.

## Secrets

`.env.local` (racine) peut contenir les identifiants PostgreSQL et Kraken, ainsi que
les anciennes clés Binance. Il est ignoré par git et bloqué par un crochet
`pre-commit`. Sur un nouveau clone :
`git config core.hooksPath .githooks`.
