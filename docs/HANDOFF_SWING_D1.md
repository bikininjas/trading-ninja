# Handoff — moteur Swing Macro D1 (D-060, D-061)

À lire avant de reprendre. Ce document dit ce qui est fait, ce qui est vérifié, et ce
qui reste à analyser. Les sources d'autorité restent `docs/DECISIONS.md` (D-060 et
D-061), `docs/ROADMAP.md` (2.11) et `docs/JOURNAL.md` (entrées des 2026-11-04 et
2026-11-05).

## 1. État vérifié

- `bash scripts/check.sh` → **TOUT EST VERT** (581 tests Python, 26 tests Node,
  9 migrations appliquées, index à jour).
- `pytest -o addopts=""` → **581 passed**, 1 warning Starlette.
- Golden de régression : `replay --bless` après ajout des champs `RiskConfig` ;
  `replay` a montré des **métriques identiques**, seul le `risk_fingerprint` et
  `created_at` changent. Relire le diff de `backend/tests/fixtures/backtest_golden.json`
  avant de committer.
- Migration `0009_allow_1d_timeframe.sql` appliquée sur Supabase le 2026-11-04.
- Aucun ordre réel, aucun changement de `.env`, aucun commit.

## 2. Ce qui a été implémenté

| Fichier | Changement |
|---|---|
| `backend/app/engine/risk_gate.py` | `EntryMode.SWING_MACRO_D1`, `StopMode.STRUCTURAL_D1`, stop sous le creux 10 D1 plafonné à 5 %, allocation 100 % sous 50 EUR, plancher de viabilité petit compte (≤ 5 %), rejet si solde < min_notional ; `SwingExitMode` (`EMA20` par défaut, `CHANDELIER`), champs et validations Chandelier |
| `backend/app/engine/entry_rule.py` | Filtre D1 : clôture > EMA50 et EMA200, pente EMA50 5 j > 0, cassure Donchian 20 OU rebond EMA20 avec RSI 50–65 |
| `backend/app/engine/exit_rule.py` | `chandelier_stop`, `update_chandelier_stop` (cliquet), `should_exit_chandelier` (clôture stricte sous le niveau) |
| `backend/app/market/feed.py` | `D1Context` avec `highest_high_chandelier`, `build_d1_context(..., chandelier_period=)`, `build_snapshot(..., chandelier_period=)`, propagation de `swing_low_10d` |
| `backend/app/backtest/swing.py` | Rejeu D1 : indicateurs précalculés, stop structurel, breakeven +1 R, sorties EMA20/Bollinger ou Chandelier (`TREND_EXIT_CHANDELIER`), frais et slippage ; `snapshot_at()` et `SwingSeries.highest_high_at()` |
| `backend/scripts/backtest.py` | Commande `swing`, options `--swing-triggers`, `--exit-mode` (liste), `--small-account`, `--months`, `--fee-bps`, levée des coupe-circuits, verdict D-061 |
| `backend/app/core/settings.py` | Preset `swing` (D1, stop structurel, TP 0, allocation 100 % petit compte), validation `DECISION_TIMEFRAME=1d`, parsing `SWING_EXIT_MODE` / `SWING_CHANDELIER_PERIOD` / `SWING_CHANDELIER_ATR_MULTIPLE` |
| `backend/app/llm/client.py` | `D1_SYSTEM_PROMPT` et `v3-d1` séparés du H4 intact ; `propose(..., decision_timeframe=)` ; payload D1 sans fuite H4 ; garde-fou D1 |
| `backend/app/backtest/llm_eval.py` | Métriques rétention / filtration / taux d'approbation, `n/a` si dénominateur vide |
| `backend/scripts/eval_llm_swing.py` | Rejoue les trades D1, interroge le LLM, **ne passe aucun ordre et n'écrit pas en base** |
| `backend/app/runtime/scheduler.py` | `decision_delay_seconds()` : 300 s pour `1d`, 5 s sinon ; `main.py` affiche l'heure d'évaluation |
| `backend/app/execution/live.py` | `update_stop_loss_order()` : `editOrder` si supporté, sinon création du nouveau stop **avant** annulation, vérification du prix, nettoyage si l'ancien ne peut être annulé |
| `backend/app/runtime/orchestrator.py` | Timeframe passé aux deux appels LLM, `chandelier_period` au snapshot, `_move_stop()`, breakeven live, sortie Chandelier |
| `backend/db/migrations/0009_allow_1d_timeframe.sql` | CHECK `('1h', '4h', '1d')`, couverture 86 400 s, vue `security_invoker` |
| `backend/app/db/repository.py` | `update_position_stop()` (papier uniquement) |
| `backend/app/market/history.py` | `1d` accepté par `aggregate()` (mémoire seulement) |

## 3. Mesure réelle (à reproduire avant toute conclusion)

```bash
cd backend && .venv/bin/python scripts/backtest.py swing \
  --symbols BTC/EUR,ETH/EUR,SOL/EUR --months 60 --fee-bps 80 \
  --swing-triggers both --exit-mode ema20,chandelier \
  --max-consecutive-losses 9999 --max-hwm-drawdown 1.0
```

**2021-11 → 2026-11, 80 pb/côté, coupe-circuits levés**

| Paire | EMA20 | Chandelier |
|---|---|---|
| BTC | 31 trades, PF 0,44, −12,69 % | 23 trades, PF 0,77, −4,81 % |
| ETH | 19 trades, PF 0,57, −5,55 % | PF 0,38 |
| SOL | 13 trades, PF 0,19 | 11 trades, PF 0,14 |

**Aucun scénario positif.** Le Chandelier divise la perte BTC par 2,6 mais ne retourne
pas le signe ; ETH et SOL se dégradent.

**Tout l'historique**

- BTC : EMA20 87 trades, PF 2,20, +148,89 % ; **Chandelier 71 trades, PF 2,81,
  +192,47 %, DD 11,99 %**.
- ETH : EMA20 74 trades, PF 1,10, +6,08 % ; **Chandelier 62 trades, PF 1,93, +68,29 %**.
- SOL : 13 → 11 trades, PF 0,19 → 0,14, KO.

BTC et ETH passent les critères D-061 sur l'historique complet (PF ≥ 1,35, payoff
≥ 3,5, MDD ≤ 20 %, N ≥ 50) ; SOL non. Aucun ne les passe sur la fenêtre récente.

- La réussite de 40 % n'est **jamais** atteinte (17–20 % sur BTC) : c'est un
  trend-following à faible taux de réussite et fort payoff. Le critère a été abandonné
  au profit de PF/payoff/MDD/N.
- Durée moyenne : 9,4 j (BTC), 5,8 j (ETH), dans la cible 3–15 j.
- Petit compte : `--equity 20 --small-account` → 17 trades sur 60 mois, −3,53 %.
  9,37 EUR reste sous le minimum notionnel : refus correct.

## 4. Mesure LLM D1 — réelle, négative

```bash
cd backend && LLM_BASE_URL=http://127.0.0.1:11435/v1 .venv/bin/python \
  scripts/eval_llm_swing.py --symbols BTC/EUR,ETH/EUR,SOL/EUR --months 0 \
  --fee-bps 80 --triggers both --exit-mode chandelier --out /tmp/eval_llm_swing.json
```

Résultat : 144 trades, **144/144 appels non dégradés** (30 BUY, 114 HOLD).

| paire | trades | gagnants | approuvés | rétention | filtration |
|---|---|---|---|---|---|
| BTC/EUR | 71 | 14 | 16 | 42,9 % | 82,5 % |
| ETH/EUR | 62 | 8 | 12 | 12,5 % | 79,6 % |
| SOL/EUR | 11 | 1 | 2 | 0,0 % | 80,0 % |
| **global** | **144** | **23** | **30** | **30,4 %** | **81,0 %** |

Verdict `KO rétention<90%`. Le modèle filtre bien les perdants (98/121) mais rejette
**16 des 23 gagnants (69,6 %)** ; la confiance ne les sépare pas (0,65 en moyenne chez
les gagnants, 0,62 chez les perdants). Le critère du brief n'est pas satisfait.

**Infrastructure Ollama.** L'installation système reste cassée : `/usr/local/lib/ollama`
est vide et tout `POST /api/chat` sur `11434` renvoie HTTP 500
`llama-server binary not found`. Un runner officiel 0.40.14 a été extrait **sans sudo**
dans `~/.local/opt/ollama-0.40.14` (2,4 Go) et un second serveur tourne sur
`127.0.0.1:11435` :

```bash
OLLAMA_HOST=127.0.0.1:11435 OLLAMA_MODELS=/mnt/e/models/ollama \
  ~/.local/opt/ollama-0.40.14/bin/ollama serve
```

Le GPU RTX 5070 Ti est utilisé ; premier chargement 88 s, appels suivants 0,2–0,3 s.
Ne pas lancer ce serveur avec `OLLAMA_MODELS=/mnt/e/models` : il ne voit alors aucun
modèle (le vrai dossier est `/mnt/e/models/ollama`). `LLM_BASE_URL` doit pointer sur
`http://127.0.0.1:11435/v1` pour l'évaluation. Ce serveur local est un contournement
documenté, pas une réparation de l'installation système.

## 5. Ce qui reste à analyser (par ordre d'importance)

1. **Revoir le prompt D1 ou la définition du signal d'entrée.** Le LLM filtre 81 % des
   perdants mais rejette 69,6 % des gagnants (rétention 30,4 %) ; le seuil de confiance
   ne sépare pas les deux populations. Tester un prompt qui distingue explicitement
   cassure valide et faux départ, ou un signal d'entrée qui laisse moins de bruit au
   modèle. Le critère reste filtration ≥ 25 %, rétention ≥ 90 %.
2. **La fenêtre récente est négative.** Tester un filtre de régime (BTC au-dessus de son
   EMA200 D1, breadth multi-paires, force relative) ou une sortie moins whipsaw que
   l'EMA20 (EMA50, Chandelier ATR, sortie partielle Bollinger). Ne pas ajouter un
   quatrième filtre d'entrée. Ne pas figer un seuil sur 11 à 71 trades sans le confirmer
   hors échantillon.
3. **Le backtest swing n'appelle pas le LLM.** `run_swing_backtest` force un BUY de
   confiance 1,0. `eval_llm_swing.py` mesure le LLM **après** coup, pas le chemin
   production. Un test d'intégration runtime avec `ENTRY_MODE=SWING_MACRO_D1` et un
   exchange factice reste à écrire.
4. **Breakeven live non activé.** `update_stop_loss_order()` est testé hors réseau mais
   n'est pas branché en production. Ne pas activer le live sans réconciliation propre et
   sans vérifier le stop après chaque déplacement sur le sous-compte Kraken.
5. **Le plancher petit compte peut monter à 5 % de risque.** C'est explicite et
   plafonné, mais à documenter dans le dashboard s'il expose un jour ces réglages.
6. **Migration 0009 appliquée.** Le D1 peut désormais être stocké ; l'ingestion
   `scripts/ingest_history.py` n'écrit toujours que du H1/H4, et le backtest agrège le D1
   depuis le H4. Ne pas supposer que `market_candles_history` contient du `1d`.

## 6. Commandes utiles

```bash
bash scripts/check.sh
cd backend && .venv/bin/python -m pytest -q -o addopts=""
cd backend && .venv/bin/python scripts/backtest.py swing --symbols BTC/EUR --months 60 \
  --fee-bps 80 --swing-triggers both --exit-mode ema20,chandelier \
  --max-consecutive-losses 9999 --max-hwm-drawdown 1.0
cd backend && .venv/bin/python scripts/backtest.py replay
cd backend && LLM_BASE_URL=http://127.0.0.1:11435/v1 .venv/bin/python \
  scripts/eval_llm_swing.py --symbols BTC/EUR,ETH/EUR,SOL/EUR --months 0 \
  --fee-bps 80 --triggers both --exit-mode chandelier
```

## 7. Règles à ne pas contourner

- Aucun ordre réel sans les trois verrous et une réconciliation propre.
- Le LLM ne dimensionne jamais : taille, prix et stop restent dans `risk_gate.py`.
- Toute modification de `backend/app/` passe le golden avant commit.
- Une mesure négative est un résultat : la dire, ne pas activer.
- `SWING_EXIT_MODE` reste `ema20` par défaut ; le Chandelier est une option mesurée,
  pas un nouveau défaut.
