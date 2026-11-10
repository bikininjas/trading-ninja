# Complément de données et mesure exploratoire du 10 novembre

Supabase a reçu **876 bougies publiques** : 543 bougies clôturées récentes et 333
heures reconstruites depuis les transactions Kraken. Les 111 heures par paire
d'octobre 2026 sont vérifiées contre 27 H4 par paire : aucune divergence OHLC,
volume identique en Decimal. Écritures additives et idempotentes, sous transaction,
verrou et budget de stockage ; aucun ordre, position, ledger ou cycle créé.
Total cible : 324 250 bougies, taille mesurée 78 739 123 octets, plancher libre
de 100 Mo respecté. Preuves : `market-completion.json`.

L'export figé depuis septembre 2022 contient **137 767 lignes** et 30 fichiers.
Le nombre et l'empreinte du contenu source sont identiques avant/après pagination.
Ce contrôle ne prétend pas être un snapshot transactionnel partagé par les appels
MCP. Les SHA-256 des fichiers sont dans `market-dataset-manifest.json`. L'archive
portable locale `exports/trading-ninja-market-dataset-20261110.tar.gz` ne contient
que ces données publiques ; elle n'est ni une sauvegarde complète ni une
conservation hors machine. Son empreinte est dans `market-completion.json`.

**77 bougies manquent dans la fenêtre complète 2022–2025** (H1 et H4 additionnés,
trous détaillés dans le manifeste). Aucun prix n'est fabriqué. La fenêtre mécanique
utilise un warmup explicite au 2 novembre 2025, après le dernier ancien trou, et
évalue du 1er janvier au 10 novembre 2026 : ses six séries sont continues après
la réparation sourcée. Cela ne qualifie ni toute l'histoire ni une fenêtre vierge.

Le protocole `economic-plan.json` précède la mesure `economic-results.json`.
Le prudent d'origine et le candidat demandé à 0,75 % / 30 % produisent chacun
**1 882 cycles, 358 propositions mécaniques, 358 refus de risque et zéro achat**,
avec 0,80 % / 5 pb comme avec 1 % / 10 pb par côté. Aucun appel dégradé ni erreur
runtime. Équité finale 9,37 EUR, identique au cash. Chaque allocation de
buy-and-hold égalitaire est trop petite pour le minimum Kraken : elle reste en
cash. Les rendements de marché non finançables sont exposés séparément.

Le diagnostic utilise le vrai Orchestrator, le Risk Gateway et PaperBroker,
avec BUY déterministe de confiance 1 après filtre d'entrée et HOLD en position.
**Aucune inférence Ollama. Aucune performance LLM ni espérance positive hors
calibration prouvée.** Aucune hausse supplémentaire de risque/capital déduite.

Reproduction hors ligne après décompression de l'archive :

```bash
cd backend
.venv/bin/python scripts/market_dataset.py audit --directory ../data/readiness/dataset-20220901
.venv/bin/python scripts/qualify_economics.py \
  --directory ../data/readiness/dataset-20220901 \
  --plan ../docs/audits/2026-11-10/economic-plan.json \
  --out ../state/readiness/economic-rerun.json
```

`market_dataset.py export` produit un nouveau snapshot SQL repeatable-read lorsqu'une
connexion privée est disponible. `repair_history.py` conserve les pages publiques
compressées et leurs empreintes ; il n'écrit jamais en base. Les transactions
brutes de réparation restent locales dans `data/readiness/`.
