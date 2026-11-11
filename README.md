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

La connexion PostgreSQL et Ollama doivent être configurés avant de lancer le moteur.
Les tests SQL utilisent une base locale jetable, jamais la base déployée.

## Documentation

- [Backend](backend/README.md) : installation, configuration et commandes.
- [Dashboard](dashboard/README.md) : démarrage, utilisation et sécurité.
- [Roadmap](docs/ROADMAP.md) : état du projet, travaux restants et critères de go live.

Les secrets vont dans `.env.local`, jamais dans Git. Activer le contrôle des commits
sur un nouveau clone : `git config core.hooksPath .githooks`.
