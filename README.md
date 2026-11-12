# Trading Ninja

Bot crypto spot : le LLM propose, un pare-feu déterministe valide le risque et
dimensionne les ordres. Le dashboard permet de suivre et piloter le bot.

**Simulation uniquement.** Le chemin LIVE est écrit, mais le passage au réel reste
NO-GO : exploitation, sauvegarde complète et validation économique à qualifier.
Supabase est en `0015` : mémoire PAPER installée et droits vérifiés le 11 novembre
2026. Le service d’apprentissage reste à installer et démarrer sur le WSL cible.

## Démarrage

Python 3.13.17 et uv ; Node.js 24 pour le dashboard.

```bash
cd backend
uv venv --python 3.13.17 .venv
uv pip sync --python .venv/bin/python requirements.lock
.venv/bin/python -m pytest
```

Après configuration de PostgreSQL, Ollama et des secrets d'administration (`JWT_SECRET_KEY`,
`TOTP_SECRET`, `ADMIN_PASSWORD_HASH`) dans `backend/.env`, `.env` ou `.env.local`, lance
l'API, le dashboard et le moteur PAPER ensemble depuis la racine :

```bash
bash scripts/manage.sh start all
bash scripts/manage.sh status
bash scripts/manage.sh logs engine
bash scripts/manage.sh stop all
```

Le moteur reste en simulation PAPER et ignore les clés exchange présentes dans
`.env.local`. Le dashboard local est sur http://127.0.0.1:8081. Les commandes
individuelles acceptent `api`, `dashboard` ou `engine`.

## Documentation

- [Backend](backend/README.md) : installation, configuration et commandes.
- [Dashboard](dashboard/README.md) : démarrage, utilisation et sécurité.
- [Roadmap](docs/ROADMAP.md) : état du projet, travaux restants et critères de go live.

Les secrets vont dans `.env.local`, jamais dans Git. Activer le contrôle des commits
sur un nouveau clone : `git config core.hooksPath .githooks`.
