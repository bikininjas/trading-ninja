# Index du code — Trading Ninja

> Fichier **généré** par `scripts/index_codebase.py`. Ne pas éditer à la main :
> lancer le script. `bash scripts/check.sh` refuse un index périmé.

Empreinte des sources : `f1a7b3f426420e0c`

## 1. Vue d'ensemble

| Mesure | Valeur |
|---|---|
| Modules Python | 102 |
| Lignes Python | 23084 |
| Classes | 122 |
| Fonctions et méthodes | 806 |
| Cas de test collectés par pytest | 587 |
| Fonctions de test (comptage statique) | 464 |
| Migrations SQL | 9 |
| Scripts shell | 3 |

## 2. Carte des fichiers

| Fichier | Lignes | Rôle (première ligne de docstring) |
|---|---|---|
| `backend/app/__init__.py` | 1 |  |
| `backend/app/api/__init__.py` | 1 | API d'administration (lecture, pause, panic-sell). |
| `backend/app/api/main.py` | 302 | API d'administration — observation, pause, reprise, panic-sell. |
| `backend/app/api/security.py` | 227 | Authentification de l'API d'administration. |
| `backend/app/api/service.py` | 112 | Vue « administration » : lecture de l'état et actions de contrôle. |
| `backend/app/api/settings_store.py` | 137 | Réglages pilotables depuis le dashboard, stockés en base. |
| `backend/app/api/stats.py` | 106 | Statistiques du dashboard — lecture seule, aucun calcul de trading. |
| `backend/app/backtest/__init__.py` | 5 | Rejeu historique et régression des décisions. |
| `backend/app/backtest/engine.py` | 809 | Rejeu historique — le vrai pare-feu, le vrai simulateur, aucune écriture en base. |
| `backend/app/backtest/golden.py` | 213 | Golden de backtest : décisions enregistrées + métriques attendues. |
| `backend/app/backtest/llm_eval.py` | 92 | Métriques pures pour l'évaluation du LLM sur les signaux swing D1. |
| `backend/app/backtest/mean_reversion.py` | 349 | Laboratoire Mean Reversion — hypothèse mesurée, jamais activée. |
| `backend/app/backtest/rotation.py` | 305 | Laboratoire de rotation momentum — BTC/ETH/SOL contre cash, sans I/O. |
| `backend/app/backtest/short_lab.py` | 260 | Laboratoire du mandat short — mesure hors production. |
| `backend/app/backtest/swing.py` | 488 | Rejeu du moteur swing Daily — le vrai pare-feu, le vrai broker, aucune I/O. |
| `backend/app/core/__init__.py` | 1 |  |
| `backend/app/core/db.py` | 166 | Résolution de la connexion PostgreSQL (Supabase). |
| `backend/app/core/env.py` | 57 | Lecture des fichiers d'environnement, sans dépendance externe. |
| `backend/app/core/settings.py` | 546 | Configuration centralisée — chargée depuis l'environnement, sans dépendance externe. |
| `backend/app/db/__init__.py` | 1 | app/db |
| `backend/app/db/repository.py` | 856 | Persistance PostgreSQL — état, signaux, décisions, positions, ordres. |
| `backend/app/engine/__init__.py` | 1 |  |
| `backend/app/engine/affordability.py` | 109 | Faisabilité d'un ordre avec un petit capital — calcul pur, sans I/O. |
| `backend/app/engine/entry_rule.py` | 391 | Filtre d'entrée déterministe — le cadre dans lequel le LLM peut acheter. |
| `backend/app/engine/exit_guard.py` | 123 | Garde-fou de sortie — le LLM peut solder une position, jamais vendre à découvert. |
| `backend/app/engine/exit_rule.py` | 72 | Règles de sortie pures, indépendantes de l'exchange et de l'horloge. |
| `backend/app/engine/machine.py` | 68 | Machine à états persistée. |
| `backend/app/engine/risk_gate.py` | 784 | Risk Gateway — pare-feu transactionnel déterministe (fail-closed). |
| `backend/app/engine/sweep.py` | 74 | Profit Sweeper — calcul pur, sans I/O. |
| `backend/app/exchange/__init__.py` | 1 | Package des passerelles exchange. Aucune I/O à l'import. |
| `backend/app/exchange/kraken.py` | 349 | Passerelle Kraken spot — préparation de l'étape 1.6. |
| `backend/app/execution/__init__.py` | 1 | app/execution |
| `backend/app/execution/factory.py` | 43 | Choix du broker selon le mode — un seul endroit, pour ne pas se tromper. |
| `backend/app/execution/live.py` | 762 | Exécution réelle Kraken — un ordre ne part que d'une décision approuvée. |
| `backend/app/execution/paper.py` | 91 | Exécution papier — un fill simulé, jamais un ordre réel. |
| `backend/app/llm/__init__.py` | 1 | Client LLM local. Aucune I/O à l'import. |
| `backend/app/llm/client.py` | 554 | Client LLM local (Ollama) — auditeur de confluences, jamais exécutant. |
| `backend/app/llm/lessons.py` | 190 | Mémoire de leçons — apprendre des résultats, pas du bruit de marché. |
| `backend/app/main.py` | 186 | Point d'entrée du moteur — papier par défaut, live sous verrous explicites. |
| `backend/app/market/__init__.py` | 1 | Couche marché : flux Kraken et indicateurs. Aucune I/O à l'import. |
| `backend/app/market/feed.py` | 404 | Flux de marché Kraken et indicateurs — bougies **clôturées** uniquement. |
| `backend/app/market/history.py` | 479 | Historique de marché persistant — le carburant du backtest multi-années. |
| `backend/app/market/kraken_archive.py` | 313 | Lecture partielle d'une archive ZIP Kraken par requêtes HTTP Range. |
| `backend/app/runtime/__init__.py` | 1 | app/runtime |
| `backend/app/runtime/orchestrator.py` | 966 | Boucle d'exécution : un cycle = une décision, jamais deux. |
| `backend/app/runtime/reconciliation.py` | 416 | Réconciliation au démarrage — la base et l'exchange doivent décrire le même état. |
| `backend/app/runtime/recovery.py` | 273 | Liquidation d'urgence — le seul chemin qui ferme une position sans pare-feu. |
| `backend/app/runtime/scheduler.py` | 62 | Cadence d'évaluation : une décision par bougie clôturée, pas par minute. |
| `backend/app/runtime/shadow.py` | 178 | Mesure des achats que le bot n'a pas pris. |
| `backend/scripts/backtest.py` | 1229 | Backtest historique — enregistrement, rejeu de régression, veille de dérive. |
| `backend/scripts/check_exchange.py` | 280 | Sonde **lecture seule** de la clé Binance — étape 1.1 de la feuille de route. |
| `backend/scripts/check_kraken.py` | 288 | Inspection Kraken spot — lecture seule. |
| `backend/scripts/db_migrate.py` | 265 | Application des migrations SQL sur la base cible. |
| `backend/scripts/demo_guardrails.py` | 79 | Démonstration lisible du pare-feu : ce qu'il refuse, et pourquoi. |
| `backend/scripts/eval_llm_swing.py` | 269 | Évalue le LLM sur les signaux swing D1, sans ordre ni écriture en base. |
| `backend/scripts/hash_password.py` | 35 | Génère un hash de mot de passe administrateur (scrypt). |
| `backend/scripts/ingest_history.py` | 471 | Ingestion de l'historique OHLCVT multi-années de Kraken. |
| `backend/scripts/kill_switch.py` | 121 | Kill-switch autonome — solde la position et halte le bot, backend arrêté ou non. |
| `backend/scripts/lab_rotation.py` | 232 | Laboratoire de rotation momentum D1 — aucun ordre, aucune écriture. |
| `backend/scripts/lessons.py` | 168 | Mémoire de leçons — relecture des résultats, affichage, purge. |
| `backend/scripts/live_probe.py` | 172 | Sonde 3.1 — vérifier qu'un ordre réel rejeté ne fait pas planter le bot. |
| `backend/scripts/probe_backtest.py` | 92 | Sonde : combien de décisions LLM un backtest coûterait-il sur BTC/EUR ? |
| `backend/scripts/reconcile.py` | 123 | Audit de réconciliation — lecture seule, aucun ordre, aucune écriture. |
| `backend/scripts/shadow_report.py` | 93 | Rapport des shadow trades — les achats que le bot n'a pas pris. |
| `backend/scripts/short_lab.py` | 70 | Mesure le mandat short sur le golden, sans réseau ni ordre. |
| `backend/scripts/totp_setup.py` | 51 | Génère un secret TOTP, son QR code et l'URI de provisionnement. |
| `backend/scripts/verify_drawdowns.py` | 136 | Vérifie les drawdowns sur un jeu de données figé, dans une transaction annulée. |
| `backend/tests/__init__.py` | 1 |  |
| `backend/tests/test_affordability.py` | 149 | Tests de la faisabilité d'un ordre avec un petit capital. |
| `backend/tests/test_api.py` | 461 | Tests de l'API d'administration : session, pause, panic-sell, WebSocket. |
| `backend/tests/test_api_security.py` | 105 | Tests de la couche d'authentification : scrypt, JWT, TOTP, anti-rejeu. |
| `backend/tests/test_backtest.py` | 288 | Tests du moteur de backtest : absence de lookahead, stop prioritaire, frais. |
| `backend/tests/test_backtest_golden.py` | 93 | Régression de backtest : le comportement doit rester celui du golden. |
| `backend/tests/test_backtest_metrics.py` | 117 | Métriques de validation : profit factor, espérance, gains et pertes moyens. |
| `backend/tests/test_check_exchange.py` | 90 | Tests du verdict de la sonde exchange — la logique qui protège la clé. |
| `backend/tests/test_db_config.py` | 75 | Tests de la résolution de connexion base (aucun accès réseau). |
| `backend/tests/test_entry_rule.py` | 314 | Tests de la règle d'entrée déterministe — pure, sans réseau ni base. |
| `backend/tests/test_execution_factory.py` | 44 | Tests du choix de broker : papier par défaut, live seulement sur demande. |
| `backend/tests/test_exit_guard.py` | 71 | Tests du garde-fou de sortie — le LLM solde, il ne shorte jamais. |
| `backend/tests/test_exit_rule.py` | 67 | Tests du Chandelier Exit : niveau, cliquet et sortie à la clôture. |
| `backend/tests/test_feed.py` | 167 | Tests du flux de marché et des indicateurs, sur bougies synthétiques. |
| `backend/tests/test_history.py` | 255 | Historique OHLCVT : analyse pure du CSV Kraken, trous, agrégation, cohérence. |
| `backend/tests/test_kraken_adapter.py` | 307 | Tests de la passerelle Kraken, hors réseau. |
| `backend/tests/test_kraken_archive.py` | 145 | Lecture ZIP par plages HTTP : le mécanisme qui évite 9,5 Go de téléchargement. |
| `backend/tests/test_lessons.py` | 172 | Tests de la mémoire de leçons — purs, sans réseau ni base. |
| `backend/tests/test_live_execution.py` | 503 | Tests de l'exécuteur réel — aucun réseau, aucune clé, aucun ordre réel. |
| `backend/tests/test_llm.py` | 288 | Tests du client LLM : parsing strict, repli, garde-fous déterministes. |
| `backend/tests/test_llm_eval.py` | 89 | Métriques de l'évaluation LLM : rétention des gagnants, filtration des perdants. |
| `backend/tests/test_machine.py` | 52 | Tests de la machine à états : le graphe est une contrainte, pas une convention. |
| `backend/tests/test_mean_reversion.py` | 133 | Tests du laboratoire Mean Reversion — aucun réseau, aucun ordre. |
| `backend/tests/test_orchestrator.py` | 736 | Tests de bout en bout de l'orchestrateur, avec des doubles. |
| `backend/tests/test_paper.py` | 78 | Tests de l'exécution papier : frais, slippage, gaps, priorité du stop. |
| `backend/tests/test_reconciliation.py` | 268 | Tests de la réconciliation base ↔ exchange, sans réseau ni base réelle. |
| `backend/tests/test_recovery.py` | 238 | Tests du kill-switch : liquidation, annulation des ordres, halte forcée. |
| `backend/tests/test_risk_gate.py` | 582 | Tests du pare-feu transactionnel. |
| `backend/tests/test_rotation.py` | 127 | Tests du laboratoire de rotation — aucun réseau, aucun ordre. |
| `backend/tests/test_scheduler.py` | 83 | Tests de la cadence : prochaine clôture de bougie, alignée sur l'époque Unix. |
| `backend/tests/test_schema_contract.py` | 173 | Contrat entre le schéma SQL et le code Python. |
| `backend/tests/test_settings.py` | 389 | Tests du chargement de configuration : presets, écrasements, verrous d'armement. |
| `backend/tests/test_shadow.py` | 76 | Tests du simulateur de shadow trades — purs, sans réseau ni base. |
| `backend/tests/test_sweep.py` | 71 | Tests du Profit Sweeper : calcul pur, seuil, absence de ligne sur perte. |
| `backend/tests/test_swing_backtest.py` | 176 | Tests du rejeu swing Daily : entrée, stop structurel, breakeven, sortie. |
| `scripts/backtest_watch.sh` | 27 | Veille de backtest — conçue pour un cron silencieux. |
| `scripts/check.sh` | 211 | Vérification complète de l'état du dépôt en une commande. |
| `scripts/manage.sh` | 307 | ============================================================================= |
| `backend/db/migrations/0001_init.sql` | 550 | migration SQL |
| `backend/db/migrations/0002_lock_public_api.sql` | 56 | migration SQL |
| `backend/db/migrations/0003_audit_fixes.sql` | 82 | migration SQL |
| `backend/db/migrations/0004_llm_autonomy.sql` | 11 | migration SQL |
| `backend/db/migrations/0005_runtime_settings.sql` | 20 | migration SQL |
| `backend/db/migrations/0006_shadow_trades.sql` | 64 | migration SQL |
| `backend/db/migrations/0007_lessons.sql` | 34 | migration SQL |
| `backend/db/migrations/0008_market_candles_history.sql` | 124 | migration SQL |
| `backend/db/migrations/0009_allow_1d_timeframe.sql` | 96 | migration SQL |

## 3. API interne, module par module

Chaque entrée donne la ligne de définition : c'est le point d'entrée pour lire
le code, pas une copie de celui-ci.

### `backend/app/api/main.py`

*API d'administration — observation, pause, reprise, panic-sell.*

- **class LoginRequest(BaseModel)** (l.48)
- **class TotpRequest(BaseModel)** (l.53)
- **class SettingsUpdate(BaseModel)** (l.57) — Réglages du dashboard. Les pourcentages sont des fractions (0,50 = 50 %).
- `async def _default_liquidate(repo: PostgresRepository, config: Settings) -> list` (l.69) — Liquidation d'urgence : ledger en papier, exchange réel en live.
- `def create_app(settings: Settings | None=None, *, repo: Any | None=None, liquidate: Any | None=None, settings_loader: Any | None=None) -> FastAPI` (l.112)

### `backend/app/api/security.py`

*Authentification de l'API d'administration.*

- **class AuthError(RuntimeError)** (l.53) — Échec d'authentification générique.
- **class AuthConfigError(AuthError)** (l.57) — Configuration d'authentification absente ou invalide.
- **class TokenError(AuthError)** (l.61) — Jeton absent, malformé, expiré ou mal signé.
- **class TotpVerifier** (l.145) — Vérifie un code TOTP et refuse sa réutilisation.
  - `def __init__(self, secret: str, *, window: int=1, replay_ttl: int=90)` (l.153)
  - `def verify(self, code: str, *, now: datetime | None=None, consume: bool=True) -> bool` (l.165)
- **class AttemptLimiter** (l.187) — Fenêtre glissante en mémoire, par clé (adresse IP).
  - `def __init__(self, max_attempts: int=5, window_seconds: int=300)` (l.190)
  - `def _recent(self, key: str, epoch: float) -> list[float]` (l.195)
  - `def allow(self, key: str, *, now: float | None=None) -> bool` (l.198)
  - `def fail(self, key: str, *, now: float | None=None) -> None` (l.202)
  - `def reset(self, key: str) -> None` (l.206)
- `def _b64(raw: bytes) -> str` (l.70)
- `def hash_password(password: str, *, salt: bytes | None=None) -> str` (l.74)
- `def verify_password(password: str, stored: str) -> bool` (l.85) — Vrai si le mot de passe correspond. Toute anomalie vaut faux.
- `def create_token(secret: str, *, subject: str='admin', ttl_seconds: int=TOKEN_TTL_SECONDS, now: datetime | None=None) -> str` (l.107)
- `def decode_token(secret: str, token: str) -> dict[str, Any]` (l.124)
- `def require_secrets(jwt_secret: str, totp_secret: str, password_hash: str) -> None` (l.210) — Refuse une configuration incomplète, au démarrage plutôt qu'à l'usage.

### `backend/app/api/service.py`

*Vue « administration » : lecture de l'état et actions de contrôle.*

- **class ControlRefused(RuntimeError)** (l.22) — Commande refusée parce qu'elle laisserait le bot dans un état dangereux.
- `def jsonable(value: Any) -> Any` (l.26)
- `def _position_payload(position, fee_rate: Decimal=Decimal('0')) -> dict | None` (l.40)
- `async def build_status(repo: PostgresRepository, settings: Settings) -> dict` (l.59)
- `async def pause(repo: PostgresRepository, settings: Settings) -> dict` (l.96)
- `async def resume(repo: PostgresRepository, settings: Settings) -> dict` (l.110)

### `backend/app/api/settings_store.py`

*Réglages pilotables depuis le dashboard, stockés en base.*

- **class SettingsRefused(ValueError)** (l.40) — Réglage refusé : valeur hors bornes ou configuration incohérente.
- `def friendly_settings(config: Settings, *, updated_at: str | None=None) -> dict` (l.44) — Vue lisible par le dashboard : labels, valeurs effectives, profils.
- `def _as_text(value) -> str` (l.75)
- `def _mapping(update: dict) -> dict[str, str]` (l.83) — Traduit les noms du dashboard vers les variables de configuration.
- `def build_payload(current: dict, update: dict) -> dict` (l.103) — Fusionne le payload existant et la modification du dashboard.
- `def validate_payload(payload: dict, *, loader=load_settings) -> Settings` (l.118) — Construit la configuration effective et refuse une combinaison dangereuse.

### `backend/app/api/stats.py`

*Statistiques du dashboard — lecture seule, aucun calcul de trading.*

- `async def build_stats(repo, settings: Settings) -> dict` (l.18)

### `backend/app/backtest/engine.py`

*Rejeu historique — le vrai pare-feu, le vrai simulateur, aucune écriture en base.*

- **class TradeRecord** (l.66)
- **class Counterfactual** (l.83) — Ce qu'aurait donné un achat forcé là où le modèle a dit HOLD.
- **class BacktestResult** (l.95)
  - `def wins(self) -> list[TradeRecord]` (l.125)
  - `def losses(self) -> list[TradeRecord]` (l.129)
  - `def net_pnl(self) -> Decimal` (l.133)
  - `def win_rate(self) -> Decimal` (l.137)
  - `def gross_profit(self) -> Decimal` (l.145) — Somme des gains bruts, frais déjà déduits par le broker.
  - `def gross_loss(self) -> Decimal` (l.150) — Somme des pertes brutes, en valeur positive.
  - `def profit_factor(self) -> Decimal | None` (l.155) — Gains bruts / pertes brutes. `None` quand il n'y a pas de perte à opposer.
  - `def profit_factor_label(self) -> str` (l.167)
  - `def expectancy_quote(self) -> Decimal` (l.175) — Espérance par trade, en devise de cotation, nette de frais.
  - `def expectancy_pct(self) -> Decimal` (l.182) — Espérance par trade rapportée à l'équité de départ.
  - `def average_win(self) -> Decimal` (l.189)
  - `def average_loss(self) -> Decimal` (l.193)
  - `def return_pct(self) -> Decimal` (l.197)
  - `def years(self) -> Decimal` (l.203) — Durée de la fenêtre en années (365,25 jours), jamais nulle.
  - `def trades_per_year(self) -> Decimal` (l.209)
  - `def payoff_ratio(self) -> Decimal | None` (l.213) — Gain moyen / perte moyenne. None si l'un des deux camps est vide.
  - `def median_inference_ms(self) -> int` (l.220)
  - `def mean_inference_ms(self) -> int` (l.224)
  - `def summary(self) -> str` (l.227)
  - `def to_dict(self) -> dict[str, Any]` (l.269)
- **class ReplayExchange** (l.337) — Exchange factice : ne sert que des bougies clôturées à l'instant courant.
  - `def __init__(self, h4: list[list], h1: list[list]) -> None` (l.340)
  - `def set_now(self, milliseconds: int) -> None` (l.345)
  - `def milliseconds(self) -> int` (l.348)
  - `def fetch_ohlcv(self, symbol: str, timeframe: str, since: int | None=None, limit: int | None=None) -> list[list]` (l.351)
- `def _close_time(candle: list) -> datetime` (l.367)
- `def _decimal(value: Any) -> Decimal` (l.371)
- `def _drawdowns(curve: list[tuple[datetime, Decimal]], at: datetime) -> tuple[Decimal, Decimal, Decimal]` (l.375)
- `def zone_is_open(snapshot: MarketSnapshot, context: MarketContext, config: RiskConfig) -> bool` (l.387) — Les conditions où un BUY a seulement le droit d'exister (miroir du client LLM).
- `def _simulate_exit(h4: list[list], entry_index: int, decision: RiskDecision, broker: PaperBroker, max_hold_candles: int=0, stop_index: int | None=None) -> tuple[int, Fill, str]` (l.397) — Cherche la première sortie après l'entrée. Stop prioritaire sur le TP.
- `async def _simulate_trade(h4: list[list], h1: list[list], symbol: str, info: Any, entry_index: int, decision: RiskDecision, broker: PaperBroker, propose: Callable[[MarketSnapshot, MarketContext, bool], Awaitable[Any]], config: RiskConfig, max_hold_candles: int=0, stop_index: int | None=None) -> tuple[int, Fill, str, list[Any], bool]` (l.430) — Simule une position bougie par bougie, avec sorties stop/TP et LLM.
- `def _pnl(entry: Fill, exit_fill: Fill) -> Decimal` (l.490)
- `def _max_drawdown(curve: list[tuple[datetime, Decimal]]) -> Decimal` (l.499)
- `async def run_backtest(*, symbol: str, info: Any, h4: list[list], h1: list[list], settings: Any, propose: Callable[[MarketSnapshot, MarketContext, bool], Awaitable[Any]] | None, broker: PaperBroker | None=None, start_index: int | None=None, end_index: int | None=None, max_hold_candles: int=0, mechanical: bool=False, counterfactuals: bool=True, progress: Callable[[int, int], None] | None=None, use_entry_rule: bool=True) -> BacktestResult` (l.509) — Rejoue une fenêtre et rend les métriques. Ne touche ni au réseau ni à la base.

### `backend/app/backtest/golden.py`

*Golden de backtest : décisions enregistrées + métriques attendues.*

- **class GoldenBundle** (l.47)
  - `def to_dict(self) -> dict[str, Any]` (l.62)
- **class RecordedSignal** (l.149) — Rend une décision enregistrée avec l'interface d'un `LLMSignal`.
  - `def __init__(self, row: dict[str, Any]) -> None` (l.152)
- **class ReplayLLM** (l.163) — Sert les décisions du golden. Échoue si une décision manque.
  - `def __init__(self, decisions: list[dict[str, Any]], *, default: str | None=None) -> None` (l.172)
  - `async def __call__(self, snapshot, context, has_position: bool=False) -> RecordedSignal` (l.177)
- `def save_bundle(bundle: GoldenBundle, path: str | Path) -> None` (l.66)
- `def load_bundle(path: str | Path) -> GoldenBundle` (l.75)
- `def prompt_fingerprint(settings: Any) -> str` (l.84) — Identité du couple prompt + modèle. Un changement invalide les décisions.
- `def risk_fingerprint(settings: Any, info: Any) -> str` (l.96) — Identité du risque, des frais et du capital de départ. Change les résultats.
- `def metrics_of(result: BacktestResult) -> dict[str, str]` (l.109) — Métriques comparables, en texte : aucun flottant, aucune tolérance.
- `def diff_metrics(expected: dict[str, str], actual: dict[str, str]) -> list[str]` (l.141)
- `def market_from_bundle(bundle: GoldenBundle) -> KrakenMarketInfo` (l.196) — Reconstruit les contraintes figées, sans réseau.

### `backend/app/backtest/llm_eval.py`

*Métriques pures pour l'évaluation du LLM sur les signaux swing D1.*

- **class EvalRecord** (l.20)
  - `def winner(self) -> bool` (l.34)
- **class EvalMetrics** (l.39)
  - `def verdict_label(self) -> str` (l.51)
- `def _rate(numerator: int, denominator: int) -> Decimal | None` (l.55)
- `def compute_metrics(records: list[EvalRecord]) -> EvalMetrics` (l.61) — Calcule rétention et filtration, sans arrondir avant l'affichage.
- `def verdict(metrics: EvalMetrics, *, min_filtration: Decimal=Decimal('0.25'), min_retention: Decimal=Decimal('0.90')) -> str` (l.80) — Critère du brief : filtrer ≥25 % des perdants sans perdre >10 % des gagnants.

### `backend/app/backtest/mean_reversion.py`

*Laboratoire Mean Reversion — hypothèse mesurée, jamais activée.*

- **class MeanReversionSeries** (l.106) — Indicateurs précalculés une fois pour tout le rejeu.
  - `def __init__(self, candles: list[list]) -> None` (l.109)
- `def _d(value: Any) -> Decimal` (l.76)
- `def _close_time(candle: list, step_ms: int) -> datetime` (l.80) — Heure de clôture réelle : 4 h en H4, 24 h en D1.
- `def _step_ms(candles: Sequence[Sequence[Any]]) -> int` (l.85)
- `def _volume_sma(volumes: Sequence[Decimal], period: int) -> list[Decimal | None]` (l.94)
- `def build_series(candles: list[list]) -> MeanReversionSeries` (l.135)
- `def evaluate_mean_reversion(series: MeanReversionSeries, index: int, *, rsi_max: Decimal=RSI_MAX) -> str | None` (l.139) — Rend le motif d'entrée, ou ``None``. Aucun accès au futur.
- `def snapshot_at(series: MeanReversionSeries, index: int, symbol: str) -> tuple[MarketSnapshot, MarketContext]` (l.161)
- `def run_mean_reversion_backtest(*, symbol: str, info: Any, candles: list[list], settings: Any, broker: PaperBroker | None=None, start_index: int | None=None, end_index: int | None=None, rsi_max: Decimal=RSI_MAX, exit_mode: str='touch', allow_counter_trend: bool=False) -> BacktestResult` (l.192) — Rejoue l'hypothèse moyenne-réversion sur des bougies clôturées.

### `backend/app/backtest/rotation.py`

*Laboratoire de rotation momentum — BTC/ETH/SOL contre cash, sans I/O.*

- **class Momentum** (l.60) — Force relative d'un actif à une date de réévaluation.
  - `def eligible(self) -> bool` (l.70)
- **class Rotation** (l.75) — Un changement d'allocation, frais et slippage compris.
- **class RotationResult** (l.88) — Résultat complet d'un rejeu de rotation.
  - `def net_pnl(self) -> Decimal` (l.106)
  - `def return_pct(self) -> Decimal` (l.110)
  - `def years(self) -> Decimal` (l.116)
  - `def rotations_per_year(self) -> Decimal` (l.121)
  - `def exposure_pct(self) -> Decimal` (l.125)
- `def _d(value: Any) -> Decimal` (l.51)
- `def _close_time(open_ms: int) -> datetime` (l.55)
- `def evaluate_momentum(closes: Sequence[Decimal], ema: Sequence[Decimal], index: int, *, symbol: str, short: int=MOMENTUM_SHORT, long: int=MOMENTUM_LONG) -> Momentum` (l.130) — Rendements 30/60 D1 et position relative à l'EMA200, sans futur.
- `def _series(candles: Sequence[Sequence[Any]]) -> tuple[dict[int, list], list[Decimal], list[Decimal]]` (l.155)
- `def run_rotation_backtest(*, candles_by_symbol: dict[str, Sequence[Sequence[Any]]], equity_start: Decimal, universe: Sequence[str]=DEFAULT_UNIVERSE, fee_bps: Decimal=Decimal('25'), slippage_bps: int=5, cadence_days: int=7, momentum_short: int=MOMENTUM_SHORT, momentum_long: int=MOMENTUM_LONG, start_at: datetime | None=None) -> RotationResult` (l.165) — Rejoue la rotation sur des bougies D1 alignées par date d'ouverture.

### `backend/app/backtest/short_lab.py`

*Laboratoire du mandat short — mesure hors production.*

- **class ShortTrade** (l.37)
- **class ShortResult** (l.55)
  - `def pnl_quote(self) -> Decimal` (l.64)
  - `def return_pct(self) -> Decimal` (l.68)
  - `def wins(self) -> list[ShortTrade]` (l.72)
  - `def win_rate(self) -> Decimal` (l.76)
  - `def summary(self) -> str` (l.81)
- `def _dt(timestamp_ms: int) -> datetime` (l.100)
- `def _decimal(value: object) -> Decimal` (l.104)
- `def _h1_return(h1: list[list], close_ms: int) -> Decimal` (l.108) — Rendement de la dernière H1 clôturée avant `close_ms`.
- `def _short_pnl(entry: Decimal, exit_price: Decimal, amount: Decimal, fee_rate: Decimal) -> tuple[Decimal, Decimal]` (l.118) — PnL net d'un short : on vend à l'entrée, on rachète à la sortie.
- `def simulate_short(bundle: GoldenBundle, *, slope_max: Decimal | None=Decimal('0'), rsi_min: Decimal=Decimal('30'), rsi_max: Decimal=Decimal('65'), require_negative_h1: bool=False, stop_atr: Decimal=Decimal('2.5'), take_profit_r: Decimal=Decimal('1.5'), trend_buffer_pct: Decimal=Decimal('0.005'), pullback_atr: Decimal=Decimal('0.75'), fee_rate: Decimal=DEFAULT_FEE, slippage: Decimal=DEFAULT_SLIPPAGE, start_equity: Decimal=DEFAULT_START_EQUITY, risk_pct: Decimal=DEFAULT_RISK_PCT, max_notional_pct: Decimal=DEFAULT_MAX_NOTIONAL_PCT, min_notional: Decimal=DEFAULT_MIN_NOTIONAL) -> ShortResult` (l.127) — Rejoue une fenêtre avec un mandat short, sans lookahead ni ordre réel.

### `backend/app/backtest/swing.py`

*Rejeu du moteur swing Daily — le vrai pare-feu, le vrai broker, aucune I/O.*

- **class SwingSeries** (l.141) — Séries d'indicateurs D1, calculées une fois pour tout le rejeu.
  - `def __init__(self, candles: list[list]) -> None` (l.144)
  - `def divergence(self, index: int) -> bool` (l.162) — Divergence baissière : nouveau sommet de clôture, RSI plus bas qu'au sommet.
  - `def highest_high_at(self, index: int, period: int) -> Decimal | None` (l.178) — Plus haut des ``period`` bougies jusqu'à ``index`` inclus.
  - `def context(self, index: int, *, chandelier_period: int=D1_DONCHIAN) -> D1Context | None` (l.184)
- `def _d(value: Any) -> Decimal` (l.62)
- `def _ema_series(values: Sequence[Decimal], period: int) -> list[Decimal]` (l.66)
- `def _rsi_series(closes: Sequence[Decimal], period: int=RSI_PERIOD) -> list[Decimal | None]` (l.74)
- `def _atr_series(candles: Sequence[Sequence[Any]], period: int=ATR_PERIOD) -> list[Decimal | None]` (l.101)
- `def _bollinger(closes: Sequence[Decimal], period: int, k: Decimal) -> tuple[list[Decimal | None], list[Decimal | None]]` (l.115)
- `def _rolling_previous_max(values: Sequence[Decimal], window: int) -> list[Decimal | None]` (l.127)
- `def _rolling_min_inclusive(values: Sequence[Decimal], window: int) -> list[Decimal | None]` (l.134)
- `def build_series(candles: list[list]) -> SwingSeries` (l.235)
- `def snapshot_at(series: SwingSeries, index: int, symbol: str, *, chandelier_period: int=D1_DONCHIAN) -> tuple[MarketSnapshot, MarketContext]` (l.239) — Construit l'état D1 exact que voit le pare-feu et le LLM à ``index``.
- `def _close_time(candle: list) -> datetime` (l.279)
- `def _drawdowns(curve: list[tuple[datetime, Decimal]], at: datetime) -> tuple[Decimal, Decimal, Decimal]` (l.283)
- `def _max_drawdown(curve: list[tuple[datetime, Decimal]]) -> Decimal` (l.295)
- `def _pnl(entry: Any, exit_fill: Any) -> Decimal` (l.305)
- `def run_swing_backtest(*, symbol: str, info: Any, d1: list[list], settings: Any, broker: PaperBroker | None=None, start_index: int | None=None, end_index: int | None=None) -> BacktestResult` (l.314) — Rejoue la politique SWING_MACRO_D1 sur des bougies Daily clôturées.

### `backend/app/core/db.py`

*Résolution de la connexion PostgreSQL (Supabase).*

- **class DatabaseConfigError(RuntimeError)** (l.26) — Configuration de base incomplète ou incohérente.
- **class DatabaseConfig** (l.31)
  - `def redacted(self) -> str` (l.41)
  - `def asyncpg_kwargs(self) -> dict[str, object]` (l.44) — Paramètres pour `asyncpg.connect(**kwargs)`.
  - `def sqlalchemy_url(self) -> str` (l.65) — URL SQLAlchemy (asyncpg). Le mot de passe est encodé pour l'URL.
  - `def sqlalchemy_connect_args(self) -> dict[str, object]` (l.72)
  - `def is_pooled(self) -> bool` (l.81)
  - `def is_ipv6_only_direct(self) -> bool` (l.85) — Connexion directe Supabase = AAAA uniquement sur les projets récents.
- `def _pick(env: Mapping[str, str], *names: str) -> str | None` (l.90)
- `def _from_url(url: str) -> DatabaseConfig` (l.98)
- `def from_env(env: Mapping[str, str]) -> DatabaseConfig` (l.118) — Construit la configuration de connexion. Lève si rien d'exploitable.

### `backend/app/core/env.py`

*Lecture des fichiers d'environnement, sans dépendance externe.*

- `def repo_root() -> Path` (l.19) — Racine du dépôt (…/trading-ninja), calculée depuis ce fichier.
- `def read_dotenv(path: Path) -> dict[str, str]` (l.24)
- `def load_env(extra: Mapping[str, str] | None=None) -> dict[str, str]` (l.44) — Fusionne les fichiers .env du dépôt puis l'environnement du processus.

### `backend/app/core/settings.py`

*Configuration centralisée — chargée depuis l'environnement, sans dépendance externe.*

- **class ExchangeSettings** (l.227)
- **class LLMSettings** (l.247)
- **class Settings** (l.258)
  - `def is_live(self) -> bool` (l.278)
  - `def is_sandbox(self) -> bool` (l.282)
  - `def execution_mode(self) -> str` (l.286) — Valeur écrite dans les colonnes `mode` de la base.
  - `def database(self) -> DatabaseConfig` (l.298) — Résolution paresseuse : la base n'est exigée que si on s'en sert.
- `def _env(env: Mapping[str, str], key: str, default: str | None=None) -> str` (l.192)
- `def _env_dec(env: Mapping[str, str], key: str, default: str) -> Decimal` (l.200)
- `def _env_int(env: Mapping[str, str], key: str, default: str) -> int` (l.204)
- `def _env_bool(env: Mapping[str, str], key: str, default: str='false') -> bool` (l.208)
- `def _first(env: Mapping[str, str], *names: str, default: str='') -> str` (l.212) — Première variable non vide parmi plusieurs alias acceptés.
- `def load_settings(env_file: str | Path | None=None, overrides: Mapping[str, str] | None=None) -> Settings` (l.308) — Charge la configuration. Lève RuntimeError si incohérente.

### `backend/app/db/repository.py`

*Persistance PostgreSQL — état, signaux, décisions, positions, ordres.*

- **class StateTransitionError(RuntimeError)** (l.33) — La transition demandée ne correspond pas à l'état réellement stocké.
- **class AccountSnapshot** (l.38)
  - `def peak(self) -> Decimal` (l.51) — Plus haut connu, pour les mises à jour de HWM.
- **class PositionRow** (l.57)
- **class PostgresRepository** (l.74)
  - `def __init__(self, connection: asyncpg.Connection)` (l.75)
  - `async def connect(cls, config: DatabaseConfig) -> 'PostgresRepository'` (l.79)
  - `async def close(self) -> None` (l.83)
  - `def transaction(self)` (l.86) — Transaction explicite, utile aux vérifications qui doivent être annulées.
  - `async def ensure_mode(self, mode: str) -> None` (l.92)
  - `async def state(self, mode: str) -> tuple[str, datetime | None]` (l.99)
  - `async def transition(self, mode: str, expected: str, target: str, reason: str, code: str | None=None, context: dict | None=None) -> None` (l.109)
  - `async def set_cooldown(self, mode: str, until: datetime) -> None` (l.135)
  - `async def note_error(self, mode: str, message: str) -> None` (l.142)
  - `async def account(self, mode: str, starting_equity: Decimal) -> AccountSnapshot` (l.151)
  - `async def snapshot_equity(self, mode: str, *, equity: Decimal, free_quote: Decimal, position_value_quote: Decimal, symbol: str | None=None, source: str='ORCHESTRATOR') -> None` (l.206)
  - `async def insert_signal(self, mode: str, symbol: str, model_name: str, *, action: str, confidence: Decimal, market_regime: str, thesis: str, prompt_payload: dict, raw_response: dict, inference_time_ms: int, prompt_sha256: str | None, temperature: float) -> tuple[uuid.UUID, datetime]` (l.225)
  - `async def last_signal_at(self, mode: str, symbol: str) -> datetime | None` (l.244) — Heure du dernier signal pour une paire — sert à ne pas réévaluer une bougie.
  - `async def insert_evaluation(self, mode: str, symbol: str, *, signal_id: uuid.UUID, signal_created_at: datetime, decision: Any, snapshot: Any, account: AccountSnapshot, evaluation_time_ms: int, decision_timeframe: str) -> tuple[uuid.UUID, datetime]` (l.257)
  - `async def insert_shadow_trade(self, mode: str, symbol: str, *, kind: str, action: str, signal_id: uuid.UUID, evaluation_id: uuid.UUID | None, rejection_code: str, rejection_details: str | None, decision_price: Decimal, amount: Decimal | None, notional_quote: Decimal | None, stop_loss_price: Decimal | None, take_profit_price: Decimal | None, risk_amount_quote: Decimal | None, plan_status: str) -> uuid.UUID` (l.291)
  - `async def open_shadow_trades(self, mode: str, limit: int=200) -> list[dict]` (l.318)
  - `async def close_shadow_trade(self, shadow_id: uuid.UUID, *, status: str, exit_price: Decimal, pnl_quote: Decimal, outcome_reason: str, evaluated_at: datetime) -> None` (l.328)
  - `async def shadow_summary(self, mode: str) -> dict` (l.340)
  - `async def active_lessons(self, mode: str) -> list[str]` (l.395)
  - `async def insert_lessons(self, mode: str, *, lessons: list[str], source_trade_ids: list[uuid.UUID], source_count: int, model_name: str, prompt_version: str, valid_until: datetime | None, review_sha256: str) -> tuple[uuid.UUID, int]` (l.404)
  - `async def recent_lessons(self, mode: str, limit: int=10) -> list[dict]` (l.435)
  - `async def deactivate_lessons(self, mode: str) -> None` (l.444)
  - `async def closed_positions_for_lessons(self, mode: str, limit: int=30) -> list[dict]` (l.451)
  - `async def shadow_outcomes_for_lessons(self, mode: str, limit: int=30) -> list[dict]` (l.463)
  - `async def open_positions(self, mode: str) -> list[PositionRow]` (l.476)
  - `async def open_position(self, mode: str) -> PositionRow | None` (l.494)
  - `async def insert_order(self, mode: str, *, client_order_id: str, symbol: str, side: str, order_type: str, purpose: str, status: str, requested_amount: Decimal, filled_amount: Decimal, requested_price: Decimal | None, stop_price: Decimal | None, average_fill_price: Decimal, fee_amount: Decimal, fee_currency: str, slippage_quote: Decimal | None, position_id: uuid.UUID | None=None, risk_evaluation_id: uuid.UUID | None=None, error_message: str | None=None) -> uuid.UUID` (l.498)
  - `async def create_position(self, mode: str, *, symbol: str, entry_price: Decimal, amount: Decimal, notional_quote: Decimal, stop_loss_price: Decimal, take_profit_price: Decimal | None, risk_amount_quote: Decimal, r_multiple_at_tp: Decimal | None, signal_id: uuid.UUID, evaluation_id: uuid.UUID, protective_order_id: str) -> uuid.UUID` (l.521)
  - `async def update_position_stop(self, position_id: uuid.UUID, stop_loss_price: Decimal) -> None` (l.539) — Déplace le stop local (seuil de rentabilité). Jamais au-dessus du prix.
  - `async def close_position(self, position_id: uuid.UUID, *, exit_price: Decimal, closed_amount: Decimal, gross_pnl_quote: Decimal, total_fees_quote: Decimal, net_pnl_quote: Decimal, exit_reason: str, status: str='CLOSED') -> None` (l.548)
  - `async def entry_fee(self, position_id: uuid.UUID) -> Decimal` (l.561) — Frais taker réellement enregistrés sur l'ordre d'entrée d'une position.
  - `async def insert_sweep(self, mode: str, *, position_id: uuid.UUID, quote_currency: str, realized_net_pnl: Decimal, sweep_ratio: Decimal, computed_sweep_quote: Decimal, swept_amount: Decimal, retained_trading_capital: Decimal, status: str, next_retry_at: datetime | None=None, error_message: str | None=None) -> uuid.UUID` (l.575)
  - `async def deferred_sweep_quote(self, mode: str) -> Decimal` (l.593) — Somme des écréma­ges reportés depuis le dernier écrémage exécuté.
  - `async def open_orders(self, mode: str) -> list[dict]` (l.613)
  - `async def update_order_status(self, order_id: uuid.UUID, status: str, error_message: str | None=None) -> None` (l.623)
  - `async def force_lock(self, mode: str, reason: str) -> None` (l.631) — Pause administrative : le bot cesse d'évaluer jusqu'à reprise explicite.
  - `async def force_resume(self, mode: str, reason: str, who: str='admin') -> None` (l.651) — Reprise humaine : uniquement depuis un état bloqué, jamais depuis IDLE.
  - `async def force_halt(self, mode: str, reason: str, code: str | None=None, auto_resume_at: datetime | None=None) -> None` (l.681) — Arrêt d'urgence déclenché par un humain, depuis n'importe quel état.
  - `async def halt_details(self, mode: str) -> str | None` (l.713) — Raison textuelle du dernier arrêt, pour distinguer LLM et humain.
  - `async def runtime_settings(self, mode: str) -> tuple[dict, datetime | None]` (l.721) — Payload de réglages et date de dernière modification, depuis la base.
  - `async def update_runtime_settings(self, mode: str, payload: dict, *, updated_by: str='dashboard') -> datetime` (l.735) — Écrit les réglages du dashboard. Une ligne par mode, jamais partagée.
  - `async def update_after_close(self, mode: str, *, consecutive_losses: int, equity: Decimal) -> None` (l.748)
  - `async def equity_curve(self, mode: str, limit: int=200) -> list[dict]` (l.760)
  - `async def dashboard_metrics(self, mode: str) -> dict` (l.769)
  - `async def daily_pnl(self, mode: str, days: int=14) -> list[dict]` (l.783)
  - `async def rejection_stats(self, mode: str) -> list[dict]` (l.796)
  - `async def llm_stats(self, mode: str, hours: int=24) -> dict` (l.805)
  - `async def recent(self, table: str, mode: str, limit: int=5) -> list[dict]` (l.847)
- `def _drawdown(peak: Decimal, equity: Decimal) -> Decimal` (l.68)

### `backend/app/engine/affordability.py`

*Faisabilité d'un ordre avec un petit capital — calcul pur, sans I/O.*

- **class Affordability** (l.34) — Verdict chiffré pour une paire, à une équité donnée.
  - `def shortfall(self) -> Decimal` (l.47) — Ce qui manque pour que le plus petit ordre tienne. Zéro si c'est bon.
- `def assess(symbol: str, *, price: Decimal, atr_14: Decimal, constraints: ExchangeConstraints, config: RiskConfig, equity: Decimal, risk_pct: Decimal | None=None) -> Affordability` (l.54) — Calcule le minimum d'ordre et l'équité qu'il exige.

### `backend/app/engine/entry_rule.py`

*Filtre d'entrée déterministe — le cadre dans lequel le LLM peut acheter.*

- **class EntrySetup** (l.41) — Verdict lisible sur la structure de marché au moment T.
- `def _verdict(triggered: bool, reason: str, rsi: Decimal, distance: Decimal, atr_pct: Decimal, setup: str=SETUP_H4) -> EntrySetup` (l.57)
- `def _pullback_h4(snapshot: MarketSnapshot, context: MarketContext, config: RiskConfig) -> EntrySetup` (l.70) — Setup historique : repli sur l'EMA50 H4 dans une tendance haussière.
- `def _pullback_h1(snapshot: MarketSnapshot, context: MarketContext, config: RiskConfig) -> EntrySetup` (l.133) — Repli fin : l'EMA50 H1 est touchée, puis refermée par le haut.
- `def _swing_macro_d1(snapshot: MarketSnapshot, context: MarketContext, config: RiskConfig) -> EntrySetup` (l.208) — Tendance de fond D1 : cassure de compression ou rebond de valeur.
- `def evaluate_entry(snapshot: MarketSnapshot, context: MarketContext, config: RiskConfig) -> EntrySetup` (l.323) — Répond à : « la structure permet-elle un achat, et pourquoi ? ».

### `backend/app/engine/exit_guard.py`

*Garde-fou de sortie — le LLM peut solder une position, jamais vendre à découvert.*

- **class ExitRejectionCode(StrEnum)** (l.26)
- **class OpenPosition** (l.36) — La seule information dont le garde-fou a besoin. Pas de prix, pas de stop.
- **class ExitDecision** (l.45)
- `def _reject(code: ExitRejectionCode, details: str) -> ExitDecision` (l.53)
- `def evaluate_exit(intent: TradeIntent, symbol: str, position: OpenPosition | None, config: RiskConfig) -> ExitDecision` (l.57) — Valide une sortie proposée par le modèle. **Ne lève jamais.**
- `def _evaluate_exit(intent: TradeIntent, symbol: str, position: OpenPosition | None, config: RiskConfig) -> ExitDecision` (l.70)

### `backend/app/engine/exit_rule.py`

*Règles de sortie pures, indépendantes de l'exchange et de l'horloge.*

- `def _positive(value: Decimal, name: str) -> Decimal` (l.23)
- `def chandelier_stop(highest_high: Decimal, atr: Decimal, atr_multiple: Decimal=Decimal('3.0')) -> Decimal` (l.31) — Niveau Chandelier brut : plus haut récent moins un multiple d'ATR.
- `def update_chandelier_stop(current_stop: Decimal | None, highest_high: Decimal, atr: Decimal, atr_multiple: Decimal=Decimal('3.0')) -> Decimal` (l.50) — Fait monter le stop Chandelier, jamais descendre.
- `def should_exit_chandelier(close: Decimal, stop: Decimal) -> bool` (l.68) — Vrai si la clôture passe **strictement** sous le stop Chandelier.

### `backend/app/engine/machine.py`

*Machine à états persistée.*

- **class StateTransitionRefused(RuntimeError)** (l.39) — Transition absente du graphe.
- **class _Repository(Protocol)** (l.43)
  - `async def transition(self, mode: str, expected: str, target: str, reason: str, code: str | None=None, context: dict | None=None) -> None` (l.44)
- **class StateMachine** (l.48)
  - `def __init__(self, repo: _Repository, mode: str, current: str)` (l.49)
  - `def can(self, target: str) -> bool` (l.56)
  - `async def go(self, target: str, reason: str, *, code: str | None=None, context: dict | None=None) -> 'StateMachine'` (l.59)

### `backend/app/engine/risk_gate.py`

*Risk Gateway — pare-feu transactionnel déterministe (fail-closed).*

- **class Action(StrEnum)** (l.51)
- **class MarketRegime(StrEnum)** (l.58)
- **class EntryMode(StrEnum)** (l.65) — Où la règle d'entrée cherche le repli.
- **class StopMode(StrEnum)** (l.78) — Comment le stop initial est calculé.
- **class SwingExitMode(StrEnum)** (l.90) — Comment une position swing D1 est soldée hors stop protecteur.
- **class RejectionCode(StrEnum)** (l.104)
- **class TradeIntent** (l.145) — Ce que le LLM a répondu. Non fiable par construction.
- **class MarketSnapshot** (l.155) — État de marché au moment de l'évaluation.
- **class AccountState** (l.180) — Photographie du compte. La réserve (funding) est exclue par construction.
- **class ExchangeConstraints** (l.196) — Lues dynamiquement via CCXT `market[...]`. Jamais codées en dur.
- **class RiskConfig** (l.211) — Réglages du pare-feu.
  - `def __post_init__(self) -> None` (l.307)
  - `def effective_risk_pct(self, consecutive_losses: int) -> Decimal` (l.382) — Risque unitaire après étranglement progressif par série de pertes.
- **class RiskDecision** (l.399)
  - `def ok(self) -> bool` (l.417)
- `def _reject(code: RejectionCode, details: str, passed: list[str]) -> RiskDecision` (l.421)
- `def _floor_to(value: Decimal, step: Decimal) -> Decimal` (l.430) — Tronque vers le bas au pas autorisé par l'exchange (jamais d'arrondi au-dessus).
- `def _as_decimal(value: float | Decimal | int) -> Decimal` (l.437)
- `def _is_finite(value: Decimal) -> bool` (l.441) — `Decimal('NaN')` et `Decimal('Infinity')` sont des valeurs valides pour
- `def evaluate(intent: TradeIntent, market: MarketSnapshot, account: AccountState, constraints: ExchangeConstraints, config: RiskConfig=DEFAULT_CONFIG) -> RiskDecision` (l.452) — Point d'entrée public. **Ne lève jamais.**
- `def _evaluate(intent: TradeIntent, market: MarketSnapshot, account: AccountState, constraints: ExchangeConstraints, config: RiskConfig=DEFAULT_CONFIG) -> RiskDecision` (l.476) — Filtre une intention de trade. Entonnoir strictement ordonné, fail-closed.

### `backend/app/engine/sweep.py`

*Profit Sweeper — calcul pur, sans I/O.*

- **class SweepPlan** (l.31)
- `def plan_sweep(net_pnl_quote: Decimal, ratio: Decimal, minimum_quote: Decimal, *, deferred_quote: Decimal=Decimal(0), simulated: bool=True) -> SweepPlan` (l.39) — Calcule l'écrémage, reports antérieurs compris.

### `backend/app/exchange/kraken.py`

*Passerelle Kraken spot — préparation de l'étape 1.6.*

- **class KrakenAdapterError(RuntimeError)** (l.50) — Toute anomalie de la passerelle qui doit arrêter le bot, pas être ignorée.
- **class KeyPermissionError(KrakenAdapterError)** (l.54) — La clé n'a pas les droits requis, ou ses droits n'ont pas pu être vérifiés.
- **class OrderPlanError(KrakenAdapterError)** (l.58) — La décision approuvée ne se traduit pas en ordre sans casser le budget.
- **class KeyAudit** (l.107) — Ce que Kraken dit de la clé utilisée, au-delà des seules permissions.
- **class KrakenMarketInfo** (l.180) — Ce que le pare-feu a besoin de savoir d'un marché, en `Decimal`.
- `def _tokens(permission: str) -> frozenset[str]` (l.88) — Découpe un libellé en jetons comparables.
- `def verify_key_permissions(exchange: Any) -> KeyAudit` (l.115) — Lit `GetApiKeyInfo` et refuse tout ce qui n'est pas explicitement autorisé.
- `def build_exchange(*, api_key: str='', secret: str='', enable_rate_limit: bool=True) -> Any` (l.193) — Instancie CCXT pour Kraken.
- `def _decimal(value: Any) -> Decimal` (l.210)
- `def resolve_taker_fee(exchange: Any, symbol: str, fallback: Decimal) -> Decimal` (l.214) — Tarif taker **du compte** pour ce marché, ou le repli si l'API privée échoue.
- `def market_info(exchange: Any, symbol: str, *, taker_fee: Decimal | None=None, maker_fee: Decimal | None=None) -> KrakenMarketInfo` (l.232) — Traduit un marché CCXT en contraintes utilisables par `evaluate()`.
- `def to_exchange_amount(exchange: Any, symbol: str, amount: Decimal) -> str` (l.267) — Formate une quantité au pas de l'exchange, sans jamais dépasser l'entrée.
- `def to_exchange_price(exchange: Any, symbol: str, price: Decimal) -> str` (l.283) — Formate un prix au tick de l'exchange. Utilisé pour l'entrée ou le TP.
- `def to_exchange_stop_price(exchange: Any, symbol: str, stop_price: Decimal) -> str` (l.292) — Formate le stop sans jamais le rapprocher de l'entrée.
- `def bracket_params(stop_price: str) -> dict[str, Any]` (l.313) — Paramètre `close` de Kraken : stop-loss au marché attaché à l'ordre d'entrée.
- `def build_bracket_entry(exchange: Any, decision: RiskDecision, symbol: str) -> dict[str, Any]` (l.324) — Arguments prêts pour `exchange.create_order(**args)`.

### `backend/app/execution/factory.py`

*Choix du broker selon le mode — un seul endroit, pour ne pas se tromper.*

- `def make_broker(settings: Settings, exchange: Any, symbols: Sequence[str] | None=None, *, mode: str | None=None) -> Any` (l.21)

### `backend/app/execution/live.py`

*Exécution réelle Kraken — un ordre ne part que d'une décision approuvée.*

- **class LiveExecutionError(RuntimeError)** (l.50) — L'ordre réel n'a pas abouti dans un état sûr. Le bot doit s'arrêter.
- **class PanicExit** (l.55) — Résultat d'une liquidation d'urgence sur un symbole.
- **class PanicReport** (l.69) — Vue complète d'un panic-sell : ventes, annulations et erreurs.
  - `def complete(self) -> bool` (l.81)
- **class RemotePositionState** (l.86) — Ce que l'exchange dit de la position, avant toute décision locale.
- **class LiveBroker** (l.123) — Broker réel, asynchrone, volontairement pessimiste.
  - `def __init__(self, exchange: Any, *, quote_currency: str, taker_fee: Decimal, poll_interval: float=1.0, timeout_seconds: float=30.0, sleep: Callable[[float], Awaitable[None]]=asyncio.sleep) -> None` (l.130)
  - `async def _call(self, function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any` (l.155) — CCXT est synchrone ; on ne bloque jamais la boucle asyncio.
  - `def fee(self, notional_quote: Decimal) -> Decimal` (l.159) — Estimation utilisée seulement si l'exchange n'a pas renvoyé ses frais.
  - `def exit_trigger(stop_loss_price: Decimal, take_profit_price: Decimal | None, candle: list) -> tuple[Decimal, str] | None` (l.164) — Le stop et le TP restent surveillés par la boucle, comme en papier.
  - `async def _wait_for_fill(self, order: dict, symbol: str) -> dict` (l.169)
  - `async def _cancel_quietly(self, order_id: str, symbol: str) -> None` (l.199)
  - `async def _open_orders(self, symbol: str) -> list[dict]` (l.205)
  - `def _stop_price(order: dict) -> Decimal | None` (l.215)
  - `async def _find_stop(self, symbol: str, amount: Decimal, stop_price: Decimal) -> dict | None` (l.227)
  - `async def _wait_for_stop(self, symbol: str, amount: Decimal, stop_price: Decimal) -> dict` (l.240)
  - `def _fill_from_order(self, order: dict, market_price: Decimal, side: str) -> Fill` (l.254)
  - `def _fee_from_order(self, order: dict, notional: Decimal) -> Decimal` (l.274)
  - `async def entry(self, decision: RiskDecision, market_price: Decimal, *, symbol: str) -> Fill` (l.299) — Achat marché + stop attaché, puis vérification du stop chez l'exchange.
  - `async def cancel_open_orders(self, *, symbol: str) -> int` (l.343) — Annule tous les ordres ouverts du symbole. Retourne le nombre annulé.
  - `async def cancel_all_orders(self, symbols: Sequence[str]) -> int` (l.364) — Annule **tous** les ordres ouverts, pas seulement ceux d'un symbole.
  - `def _held_balances(self, balance: dict[str, dict[str, Decimal]], symbols: Sequence[str]) -> list[tuple[str, Decimal]]` (l.400) — Symboles réellement détenus, y compris hors univers configuré.
  - `async def panic_sell(self, symbols: Sequence[str]) -> PanicReport` (l.428) — Liquidation d'urgence : annule tout, vend tout, vérifie le solde.
  - `async def _rearm_stop(self, symbol: str, amount: Decimal, stop_price: Decimal) -> str` (l.467)
  - `async def _read_order(self, order_id: str, symbol: str) -> dict` (l.483)
  - `async def _edit_stop(self, symbol: str, amount: Decimal, old_stop_id: str, new_stop_price: Decimal) -> str | None` (l.495) — Tente ``edit_order`` seulement si CCXT annonce le support natif.
  - `async def update_stop_loss_order(self, symbol: str, amount: Decimal, old_stop_id: str, new_stop_price: Decimal) -> str` (l.533) — Remplace un stop sans jamais laisser la position nue.
  - `async def exit(self, amount: Decimal, market_price: Decimal, *, symbol: str, stop_price: Decimal | None=None) -> Fill` (l.625) — Annule les protections, vend au marché, et réarme si la vente échoue.
  - `async def emergency_exit(self, amount: Decimal, *, symbol: str, stop_price: Decimal | None=None, market_price: Decimal | None=None) -> Fill` (l.650) — Liquidation d'urgence : annule tout, vend, et ne réarme rien.
  - `async def position_state(self, symbol: str, amount: Decimal, stop_price: Decimal) -> RemotePositionState` (l.681) — Lit le solde de base et le stop **avant** toute décision locale.
  - `async def last_sell_fill(self, symbol: str, *, since: int | None=None, limit: int=20) -> Fill | None` (l.697) — Dernière vente exécutée sur le symbole, lue dans l'historique réel.
  - `async def balance(self) -> dict[str, dict[str, Decimal]]` (l.736)
  - `async def free_quote(self) -> Decimal` (l.749)
  - `async def free_base(self, symbol: str) -> Decimal` (l.753)
  - `async def total_base(self, symbol: str) -> Decimal` (l.758) — Solde total de base : un stop armé peut réserver le solde « free ».
- `def _decimal(value: Any, default: Decimal=Decimal(0)) -> Decimal` (l.95)
- `def _status(order: dict) -> str` (l.107)
- `def _is_final(order: dict) -> bool` (l.111)
- `def _is_dead(order: dict) -> bool` (l.119)

### `backend/app/execution/paper.py`

*Exécution papier — un fill simulé, jamais un ordre réel.*

- **class Fill** (l.26)
- **class PaperBroker** (l.36)
  - `def __init__(self, taker_fee: Decimal, slippage_bps: int=5)` (l.37)
  - `def fee(self, notional_quote: Decimal) -> Decimal` (l.45) — Frais taker d'un notionnel donné, pour reconstruire un coût d'entrée.
  - `def entry(self, decision: RiskDecision, market_price: Decimal, *, symbol: str | None=None) -> Fill` (l.49) — Achat au marché simulé. La quantité vient de la décision, jamais d'ici.
  - `def exit(self, amount: Decimal, market_price: Decimal, *, symbol: str | None=None, stop_price: Decimal | None=None) -> Fill` (l.64)
  - `def exit_trigger(stop_loss_price: Decimal, take_profit_price: Decimal | None, candle: list) -> tuple[Decimal, str] | None` (l.78) — Rend (prix de sortie, raison) si la bougie clôturée a déclenché une sortie.

### `backend/app/llm/client.py`

*Client LLM local (Ollama) — auditeur de confluences, jamais exécutant.*

- **class LLMError(RuntimeError)** (l.160) — Le modèle n'a pas produit d'intention exploitable.
- **class ParsedSignal** (l.165)
- **class LLMSignal** (l.175)
- **class SignalClient** (l.239) — Appelle Ollama et rend une intention validée, ou un HOLD de repli.
  - `def __init__(self, settings: LLMSettings, *, transport: httpx.AsyncBaseTransport | None=None)` (l.242)
  - `def _payload(self, symbol: str, snapshot: MarketSnapshot, context: MarketContext, has_position: bool, trend_buffer_pct: Decimal, lessons: list[str] | None=None, decision_timeframe: str='4h') -> tuple[dict, dict]` (l.248)
  - `def _root(self) -> str` (l.363)
  - `async def _post(self, payload: dict, timeout: float) -> dict` (l.367)
  - `def _fallback(self, snapshot: MarketSnapshot, reason: str) -> LLMSignal` (l.384)
  - `async def propose(self, symbol: str, snapshot: MarketSnapshot, context: MarketContext, *, has_position: bool=False, trend_buffer_pct: Decimal=Decimal('0.005'), lessons: list[str] | None=None, decision_timeframe: str='4h') -> LLMSignal` (l.406)
  - `async def review(self, digest: str, *, timeout_seconds: float=30.0) -> list[str]` (l.469) — Une seule inférence de relecture : rend des leçons brutes à valider.
  - `def _enforce_rules(self, signal: LLMSignal, snapshot: MarketSnapshot, trend_buffer_pct: Decimal) -> LLMSignal` (l.501) — Rend inopérant un BUY que les règles interdisent.
  - `async def warmup(self, timeout_seconds: float=180.0) -> bool` (l.538) — Charge le modèle en mémoire avant la première décision.
- `def _extract_json(content: str) -> dict` (l.190) — Récupère le premier objet JSON, même entouré de texte ou de balises.
- `def _confidence(value: Any) -> Decimal` (l.205)
- `def parse_signal(content: str) -> ParsedSignal` (l.215) — Valide la réponse du modèle. Ne lit que les cinq champs autorisés.

### `backend/app/llm/lessons.py`

*Mémoire de leçons — apprendre des résultats, pas du bruit de marché.*

- **class LessonsError(ValueError)** (l.43) — Leçons inutilisables : trop nombreuses, vides ou dangereuses.
- **class LessonsRefused(RuntimeError)** (l.47) — Pas assez de résultats pour une relecture honnête.
- **class LessonsReview** (l.52)
- `def _money(value: Any) -> str` (l.60)
- `def _number(value: Any, digits: int=2) -> str` (l.66)
- `def _clean(text: Any, limit: int) -> str` (l.72)
- `def build_digest(positions: list[dict], shadows: list[dict]) -> str` (l.76) — Journal compact des résultats récents, sans solde ni identifiant de compte.
- `def sanitize_lessons(raw: Any) -> list[str]` (l.121) — Valide et nettoie la sortie du modèle. Refuse les consignes dangereuses.
- `def lessons_block(lessons: list[str] | None) -> str | None` (l.150) — Bloc injecté dans le prompt. `None` si aucune leçon active.
- `async def review_lessons(repo, client, settings, *, force: bool=False, max_samples: int=30) -> LessonsReview` (l.164) — Lit les résultats, appelle le modèle une fois, rend les leçons validées.

### `backend/app/main.py`

*Point d'entrée du moteur — papier par défaut, live sous verrous explicites.*

- `async def _status(repo: PostgresRepository, mode: str) -> None` (l.31)
- `async def _serve(orchestrator: Orchestrator, timeframe: str, *, max_cycles: int=0) -> None` (l.59) — Boucle alignée sur les clôtures de bougies, pas sur un intervalle fixe.
- `async def _run(args: argparse.Namespace) -> int` (l.92)
- `def main(argv: list[str] | None=None) -> int` (l.158)

### `backend/app/market/feed.py`

*Flux de marché Kraken et indicateurs — bougies **clôturées** uniquement.*

- **class FeedError(RuntimeError)** (l.59) — Historique insuffisant ou données inexploitables.
- **class D1Context** (l.64) — Indicateurs Daily, consommés par la règle ``SWING_MACRO_D1``.
- **class MarketContext** (l.97) — Contexte qualitatif transmis au modèle. Aucun de ces champs ne dimensionne.
- **class SymbolState** (l.117) — Instantané complet d'une paire au moment T.
- `def _decimal(value: Any) -> Decimal` (l.127)
- `def closed_candles(exchange: Any, symbol: str, timeframe: str, limit: int, now_ms: int | None=None) -> list[list]` (l.131) — Bougies dont la fenêtre est terminée, la plus ancienne d'abord.
- `def ema(values: list, period: int=EMA_PERIOD) -> Decimal` (l.142) — EMA classique, alpha = 2/(n+1). `values` du plus ancien au plus récent.
- `def atr(candles: list[list], period: int=ATR_PERIOD) -> Decimal` (l.155) — ATR simple sur les `period` dernières variations vraies.
- `def rsi(candles: list[list], period: int=RSI_PERIOD) -> Decimal` (l.168) — RSI de Wilder sur les clôtures. 100 si aucune perte sur la fenêtre.
- `def _ema_series(values: list[Decimal], period: int) -> list[Decimal]` (l.187)
- `def macd_histogram(candles: list[list], fast: int=12, slow: int=26, signal: int=9) -> Decimal` (l.195) — Histogramme MACD = MACD - signal(EMA9 du MACD).
- `def order_book_imbalance(exchange: Any, symbol: str, depth: int=20) -> Decimal` (l.212) — (volume acheteur - volume vendeur) / total, sur les `depth` premiers niveaux.
- `def _close_time(candle: list, timeframe: str) -> datetime` (l.227)
- `def build_d1_context(candles: list[list], *, chandelier_period: int=D1_DONCHIAN) -> D1Context` (l.231) — Calcule les indicateurs Daily sur des bougies clôturées, la plus ancienne d'abord.
- `def build_snapshot(exchange: Any, symbol: str, *, decision_timeframe: str='4h', entry_timeframe: str='1h', now: datetime | None=None, info: KrakenMarketInfo | None=None, with_order_book: bool=True, with_d1: bool=False, chandelier_period: int=D1_DONCHIAN) -> SymbolState` (l.304) — Construit l'instantané que consomment le pare-feu et le modèle.
- `def last_price(exchange: Any, symbol: str) -> Decimal` (l.398) — Prix courant, pour la vérification de fraîcheur juste avant `evaluate()`.

### `backend/app/market/history.py`

*Historique de marché persistant — le carburant du backtest multi-années.*

- **class HistoryError(RuntimeError)** (l.51) — Historique inexploitable : format, timeframe ou cohérence.
- **class CandleGap** (l.56) — Un trou entre deux bougies consécutives.
- **class Coverage** (l.66) — Ce que la base contient réellement pour une paire et un timeframe.
  - `def gap_count(self) -> int` (l.81)
- **class StorageVerdict** (l.86) — Décision d'écriture face au quota de la base.
  - `def free_now_bytes(self) -> int` (l.104)
- `def _decimal(text: str) -> Decimal` (l.112)
- `def parse_ohlcvt(text: str, timeframe: str) -> list[list]` (l.119) — CSV Kraken (`ts,open,high,low,close,volume,trades`) → bougies CCXT.
- `def merge(*series: Sequence[Sequence[Any]]) -> list[list]` (l.158) — Fusionne plusieurs sources par timestamp ; la dernière gagne.
- `def detect_gaps(candles: Sequence[Sequence[Any]], timeframe: str) -> list[CandleGap]` (l.167) — Trous entre bougies consécutives, en unités du timeframe.
- `def aggregate(candles: Sequence[Sequence[Any]], source: str, target: str) -> list[list]` (l.195) — Agrège H1 → H4 en n'acceptant que les fenêtres complètes.
- `def reconcile_aggregation(h1: Sequence[Sequence[Any]], h4: Sequence[Sequence[Any]], *, max_examples: int=5) -> tuple[int, int, list[str]]` (l.234) — Compare le H4 publié à l'agrégation du H1.
- `def storage_verdict(*, db_bytes: int, row_bytes: int, rows_to_add: int, quota_bytes: int, min_free_bytes: int) -> StorageVerdict` (l.266) — Faut-il écrire `rows_to_add` lignes sans passer sous le plancher libre ?
- `async def store_candles(conn: Any, symbol: str, timeframe: str, candles: Sequence[Sequence[Any]], source: str, *, chunk_size: int=5000) -> int` (l.346) — Écrit les bougies ; rend le nombre réellement inséré (les doublons sont ignorés).
- `async def load_candles(conn: Any, symbol: str, timeframe: str, *, start: datetime | None=None, end: datetime | None=None, limit: int | None=None) -> list[list]` (l.383) — Bougies au format CCXT, de la plus ancienne à la plus récente.
- `async def coverage(conn: Any, symbol: str, timeframe: str, *, max_gaps: int=20) -> Coverage` (l.420) — Couverture mesurée en base, trous détaillés inclus.
- `def iter_rows(candles: Iterable[Sequence[Any]]) -> Iterable[tuple]` (l.459) — Itère (open_ms, o, h, l, c, v) — utile aux scripts, sans ouvrir la base.
- `async def storage_state(conn: Any) -> tuple[int, int]` (l.465) — (taille de la base, octets par ligne de `market_candles_history`).

### `backend/app/market/kraken_archive.py`

*Lecture partielle d'une archive ZIP Kraken par requêtes HTTP Range.*

- **class KrakenArchiveError(RuntimeError)** (l.50) — Archive illisible, membre absent ou compression non gérée.
- **class RangeSource(Protocol)** (l.54) — Une source d'octets adressable, sans téléchargement complet.
  - `def size(self) -> int` (l.57)
  - `def read(self, start: int, end: int) -> bytes` (l.59) — Octets de `start` à `end` **inclus**.
  - `def close(self) -> None` (l.63)
- **class ArchiveMember** (l.67)
- **class HttpRangeSource** (l.75) — Source HTTP ; toute réponse autre que 206 est une erreur explicite.
  - `def __init__(self, url: str, *, timeout: float=60.0, retries: int=3) -> None` (l.78)
  - `def size(self) -> int` (l.86)
  - `def read(self, start: int, end: int) -> bytes` (l.96)
  - `def close(self) -> None` (l.115)
- **class BytesRangeSource** (l.119) — Source en mémoire, pour les tests : même contrat que HTTP.
  - `def __init__(self, data: bytes) -> None` (l.122)
  - `def size(self) -> int` (l.125)
  - `def read(self, start: int, end: int) -> bytes` (l.128)
  - `def close(self) -> None` (l.133)
- **class MultiPartRangeSource** (l.137) — Plusieurs fichiers concaténables vus comme un seul espace d'octets.
  - `def __init__(self, parts: list[str | RangeSource], *, timeout: float=90.0, retries: int=3) -> None` (l.147)
  - `def _layout(self) -> list[tuple[int, RangeSource]]` (l.158)
  - `def size(self) -> int` (l.168)
  - `def read(self, start: int, end: int) -> bytes` (l.171)
  - `def close(self) -> None` (l.188)
- **class KrakenArchive** (l.247) — Archive ZIP distante, lue membre par membre.
  - `def __init__(self, source: RangeSource) -> None` (l.250)
  - `def _read_end_of_central_directory(self) -> tuple[int, int, int]` (l.256) — Rend (entries, cd_size, cd_offset), en gérant le ZIP64.
  - `def members(self) -> dict[str, ArchiveMember]` (l.276)
  - `def read(self, name: str) -> bytes` (l.287)
  - `def read_text(self, name: str, encoding: str='utf-8') -> str` (l.312)
- `def _apply_zip64_extra(extra: bytes, uncompressed: int, compressed: int, offset: int) -> tuple[int, int, int]` (l.197) — Applique le champ supplémentaire ZIP64 (id 0x0001) aux valeurs 32 bits.
- `def parse_central_directory(directory: bytes) -> dict[str, ArchiveMember]` (l.224) — Répertoire central d'un ZIP → {nom: membre}. Pur, testable hors réseau.

### `backend/app/runtime/orchestrator.py`

*Boucle d'exécution : un cycle = une décision, jamais deux.*

- **class CycleReport** (l.60)
  - `def lines(self) -> list[str]` (l.75)
- **class Orchestrator** (l.91)
  - `def __init__(self, settings: Settings, repo: PostgresRepository, exchange: Any, signal_client: SignalClient, broker: Any, *, now: Any=None, settings_loader: Any=None)` (l.92)
  - `async def _maybe_await(value: Any) -> Any` (l.109) — Le broker papier est synchrone, le broker réel est asynchrone.
  - `async def _entry_fee(self, position: Any) -> Decimal` (l.119) — Frais d'entrée réellement enregistrés, sinon estimation du broker.
  - `def _account_state(self, account: AccountSnapshot, open_symbols: tuple[str, ...]=()) -> AccountState` (l.133)
  - `def _client_id(self, symbol: str, purpose: str) -> str` (l.145)
  - `async def _starting_equity(self) -> Decimal` (l.148)
  - `async def _refresh_settings(self) -> tuple[bool, str | None]` (l.162) — Relit les réglages en base et les applique si le payload a changé.
  - `async def _log_signal(self, symbol: str, signal: LLMSignal, report: CycleReport) -> tuple[uuid.UUID, datetime]` (l.190) — Persiste toute décision du modèle, y compris HOLD, SELL et HALT.
  - `async def _record_shadow(self, *, kind: str, symbol: str, signal: LLMSignal, signal_id: uuid.UUID, evaluation_id: uuid.UUID | None, decision: Any | None, snapshot: Any, account_state: AccountState, constraints: Any) -> None` (l.208) — Enregistre un achat non pris et son plan contrefactuel.
  - `async def _force_halt(self, machine: StateMachine, report: CycleReport, reason: str) -> None` (l.259) — Arrêt demandé par le modèle : même chemin que le bouton rouge, sans 2FA.
  - `async def run_once(self) -> CycleReport` (l.281)
  - `async def _monitor(self, machine: StateMachine, position: PositionRow, account: AccountSnapshot, report: CycleReport) -> None` (l.380)
  - `async def _move_stop(self, position: PositionRow, new_stop: Decimal, report: CycleReport, *, live_state: Any, label: str) -> Decimal` (l.629) — Déplace le stop local, et le stop distant en live, sans jamais le baisser.
  - `async def _close_trade(self, machine: StateMachine, position: PositionRow, account: AccountSnapshot, exit_price: Decimal, reason: str, report: CycleReport, *, purpose: str, halt: bool=False, fill: Any=None) -> None` (l.664) — Séquence unique de clôture : stop, TP, SELL LLM et HALT LLM.
  - `async def _evaluate_cycle(self, machine: StateMachine, account: AccountSnapshot, report: CycleReport) -> None` (l.766)
  - `async def _enter(self, machine: StateMachine, state: SymbolState, decision: Any, signal_id: uuid.UUID, evaluation_id: uuid.UUID, account: AccountSnapshot, report: CycleReport) -> None` (l.897)
- `def position_initial_notional(position: PositionRow) -> Decimal` (l.964) — Notionnel initial d'une position relue depuis la base.

### `backend/app/runtime/reconciliation.py`

*Réconciliation au démarrage — la base et l'exchange doivent décrire le même état.*

- **class RemoteOrder** (l.58) — Un ordre ouvert chez l'exchange, réduit à ce que la réconciliation lit.
- **class Finding** (l.72) — Un constat, avec un code stable et une phrase lisible à 3 h du matin.
- **class ReconciliationPlan** (l.82) — Constat + actions minimales. `liquidate` contient des symboles, pas des prix.
  - `def critical(self) -> tuple[Finding, ...]` (l.95)
  - `def clean(self) -> bool` (l.99)
  - `def lines(self) -> list[str]` (l.102)
- `def _decimal(value: Any) -> Decimal` (l.126)
- `def _optional_decimal(value: Any) -> Decimal | None` (l.132)
- `def _finding(code: str, severity: str, symbol: str | None, details: str) -> Finding` (l.142)
- `def plan_reconciliation(positions: Sequence[PositionRow], remote_orders: Sequence[RemoteOrder], balances: Mapping[str, Decimal], *, mode: str, quote_currency: str, quote_balance: Decimal | None=None) -> ReconciliationPlan` (l.146) — Construit le constat et le plan, sans I/O et sans horloge.
- `def read_remote_orders(exchange: Any, symbols: Sequence[str]) -> list[RemoteOrder]` (l.293) — Lit les ordres ouverts de l'exchange, limités à l'univers du bot.
- `def read_account(exchange: Any, symbols: Sequence[str], quote_currency: str) -> tuple[dict[str, Decimal], Decimal | None]` (l.332) — Solde de base par symbole et solde de cotation, en `Decimal`.
- `async def inspect_exchange(repo: Any, exchange: Any, mode: str, symbols: Sequence[str], *, quote_currency: str) -> ReconciliationPlan` (l.353) — Lit base et exchange, puis délègue le verdict à `plan_reconciliation`.
- `async def apply_reconciliation(plan: ReconciliationPlan, repo: Any, exchange: Any, broker: Any, *, starting_equity: Decimal, now: Any=None) -> list[str]` (l.371) — Applique le plan minimal : liquider les positions signalées, sinon halter.

### `backend/app/runtime/recovery.py`

*Liquidation d'urgence — le seul chemin qui ferme une position sans pare-feu.*

- **class PanicLiquidationError(RuntimeError)** (l.35) — Une liquidation d'urgence a laissé une exposition ou une erreur.
  - `def __init__(self, message: str, *, liquidated: list['PanicRecord'], errors: list[str]) -> None` (l.42)
- **class Liquidation** (l.56)
- **class PanicRecord** (l.66) — Vente d'urgence constatée chez l'exchange, écrite au ledger.
- `async def _maybe_await(value: Any) -> Any` (l.49)
- `async def liquidate_open_positions(repo: PostgresRepository, exchange: Any, broker: Any, mode: str, *, quote_currency: str, starting_equity: Decimal, reason: str=EMERGENCY_REASON, now: datetime | None=None, only_symbols: set[str] | None=None) -> list[Liquidation]` (l.82) — Solde toutes les positions ouvertes, annule leurs ordres, puis halte le bot.
- `async def record_panic_exits(repo: PostgresRepository, exits: Any, mode: str, *, quote_currency: str, starting_equity: Decimal, free_quote_after: Decimal | None, taker_fee: Decimal, errors: list[str] | None=None, reason: str=EMERGENCY_REASON, now: datetime | None=None) -> list[PanicRecord]` (l.171) — Écrit au ledger les ventes réelles d'un panic-sell, puis halte le bot.

### `backend/app/runtime/scheduler.py`

*Cadence d'évaluation : une décision par bougie clôturée, pas par minute.*

- `def next_candle_close(now: datetime, timeframe: str) -> datetime` (l.36) — Prochaine clôture de bougie strictement après `now`, en UTC.
- `def seconds_until_next_close(now: datetime, timeframe: str) -> float` (l.48) — Secondes à attendre avant la prochaine clôture (toujours > 0).
- `def decision_delay_seconds(timeframe: str) -> float` (l.53) — Marge après la clôture avant d'évaluer.

### `backend/app/runtime/shadow.py`

*Mesure des achats que le bot n'a pas pris.*

- **class ShadowOutcome** (l.37)
- `def _close_time(candle: list, duration_ms: int) -> datetime` (l.45)
- `def simulate_shadow(candles: list[list], *, entry_at: datetime, entry_price: Decimal, amount: Decimal, stop_loss_price: Decimal, take_profit_price: Decimal | None, broker: PaperBroker, timeframe: str='4h') -> ShadowOutcome | None` (l.49) — Rejoue les bougies postérieures à `entry_at` et rend le résultat simulé.
- `async def evaluate_open_shadow_trades(repo, exchange: Any, settings: Settings, *, limit: int=200) -> int` (l.118) — Ferme les shadow trades `OPEN` en rejouant les bougies disponibles.

### `backend/scripts/backtest.py`

*Backtest historique — enregistrement, rejeu de régression, veille de dérive.*

- **class _MechanicalSignal** (l.510) — Réponse fixe qui remplace le LLM dans le balayage.
- `def fetch_recent(exchange, symbol: str, timeframe: str) -> list[list]` (l.53) — Les 720 bougies les plus récentes, clôturées uniquement.
- `def first_index(h4: list[list], h1: list[list]) -> int` (l.66) — Premier indice où l'EMA200 et les 120 H1 nécessaires existent.
- `def with_overrides(settings, args)` (l.77) — Scénario de backtest : ne modifie jamais `.env`, seulement cette exécution.
- `def fee_grid(args) -> list[Decimal]` (l.93) — Grille de frais par côté, en points de base.
- `async def record(args) -> int` (l.115)
- `async def replay(args) -> int` (l.205)
- `async def scenario(args) -> int` (l.255) — Rejoue le golden avec un autre stop/take-profit, sans le modifier.
- `def _snapshot_at(bundle: GoldenBundle, info, index: int)` (l.300)
- `async def watch(args) -> int` (l.312)
- `def read_lessons(path: str | None) -> list[str] | None` (l.419) — Lit un fichier de leçons (une par ligne) pour un scénario A/B.
- `async def measure(args) -> int` (l.432) — Mesure la politique règle + LLM sur plusieurs paires, sans golden.
- `async def _always_buy(snapshot, context, has_position: bool=False)` (l.530)
- `async def sweep(args) -> int` (l.534) — Balaye le filtre de pente EMA200 sur l'historique multi-années, sans LLM.
- `async def swing_sweep(args) -> int` (l.683) — Mesure SWING_MACRO_D1 sur l'historique multi-années, sans LLM.
- `def _swing_verdict(row: dict) -> str` (l.845) — Critères swing D-061 : N, payoff, PF et drawdown, jamais le winrate.
- `def _print_swing(rows: list[dict]) -> None` (l.867)
- `def _verdict(row: dict, *, min_trades: int, min_pf: Decimal, max_dd: Decimal) -> str` (l.901) — Verdict par scénario, critères du brief : N, PF, espérance, drawdown.
- `def _print_sweep(rows: list[dict]) -> None` (l.919)
- `async def mean_reversion_sweep(args) -> int` (l.955) — Mesure MEAN_REVERSION_BB, sans LLM, sur H4 et D1 agrégé.
- `def _meanrev_verdict(row: dict) -> str` (l.1092) — Verdict de laboratoire : PF ≥ 1,30, N ≥ 30, net > 0, DD ≤ 20 %.
- `def _print_mean_reversion(rows: list[dict]) -> None` (l.1111)
- `def main() -> int` (l.1142)

### `backend/scripts/check_exchange.py`

*Sonde **lecture seule** de la clé Binance — étape 1.1 de la feuille de route.*

- **class BinanceError(RuntimeError)** (l.48)
  - `def __init__(self, http_status: int, code: int | None, message: str) -> None` (l.49)
- `def _http(url: str, headers: dict[str, str] | None=None) -> dict` (l.56)
- `def server_offset_ms(base: str) -> int` (l.73) — Décalage entre l'horloge locale et celle de l'exchange (les deux en ms).
- `def signed_get(base: str, path: str, api_key: str, secret: str, offset_ms: int) -> dict` (l.79)
- `def probe(network: str, api_key: str, secret: str) -> dict` (l.89)
- `def symbol_constraints(symbol: str) -> dict` (l.126) — Filtres de l'exchange pour un symbole — les entrées de `ExchangeConstraints`.
- `def verdict(result: dict) -> tuple[bool, str]` (l.155) — Verdict fail-closed.
- `def main() -> int` (l.189)

### `backend/scripts/check_kraken.py`

*Inspection Kraken spot — lecture seule.*

- `def _closed_candles(exchange, symbol: str, timeframe: str='4h', limit: int=80) -> list[list]` (l.51) — Bougies **clôturées** uniquement.
- `def _atr_14(candles: list[list]) -> Decimal` (l.64)
- `def _print_affordability(exchange, symbols, equity: Decimal, risk_config, quote: str) -> int` (l.75)
- `def _print_scan(exchange, equity: Decimal, risk_config, quote: str, top: int, min_volume: Decimal=Decimal('100000')) -> int` (l.107) — Cherche dans tout l'univers les paires réellement tradables avec cette équité.
- `def _credentials(env: dict[str, str]) -> tuple[str, str]` (l.172)
- `def _resolve_equity(exchange, quote: str, api_key: str, secret: str, explicit: str | None)` (l.180) — Équité à utiliser : explicite, sinon lue en lecture seule sur le compte.
- `def main() -> int` (l.193)

### `backend/scripts/db_migrate.py`

*Application des migrations SQL sur la base cible.*

- `def discover() -> list[Path]` (l.42)
- `def checksum(path: Path) -> str` (l.46)
- `async def connect(cfg: DatabaseConfig) -> asyncpg.Connection` (l.50)
- `async def applied_versions(conn: asyncpg.Connection) -> dict[str, str]` (l.54)
- `async def cmd_status(conn: asyncpg.Connection) -> int` (l.62)
- `async def cmd_apply(conn: asyncpg.Connection) -> int` (l.90)
- `async def cmd_verify(conn: asyncpg.Connection) -> int` (l.115) — Contrôle structurel + test d'écriture annulé (rien ne reste en base).
- `async def main_async(argv: list[str]) -> int` (l.229)
- `def main() -> int` (l.260)

### `backend/scripts/demo_guardrails.py`

*Démonstration lisible du pare-feu : ce qu'il refuse, et pourquoi.*

- `def show(label, *, market=None, account=None, intent=None)` (l.33)

### `backend/scripts/eval_llm_swing.py`

*Évalue le LLM sur les signaux swing D1, sans ordre ni écriture en base.*

- `def _close_time(candle: list) -> datetime` (l.40)
- `def _format_rate(value: Decimal | None) -> str` (l.44)
- `async def evaluate(args: argparse.Namespace) -> int` (l.48)
- `def main() -> int` (l.234)

### `backend/scripts/hash_password.py`

*Génère un hash de mot de passe administrateur (scrypt).*

- `def main() -> int` (l.21)

### `backend/scripts/ingest_history.py`

*Ingestion de l'historique OHLCVT multi-années de Kraken.*

- **class BudgetExceeded(RuntimeError)** (l.75) — L'écriture ferait passer la base sous le plancher de 100 Mo libres.
- `async def store_within_budget(conn, symbol: str, timeframe: str, candles: list[list], source: str) -> int` (l.79) — Écrit après avoir vérifié le quota ; refuse au lieu de remplir la base.
- `def archive_code(symbol: str) -> str` (l.101)
- `def quarter_index(year: int, quarter: int) -> int` (l.106)
- `def quarters_since(months: int, today: date | None=None) -> list[tuple[int, int]]` (l.110) — Les trimestres **complets** couvrant au moins `months` mois avant aujourd'hui.
- `def member_name(symbol: str, timeframe: str) -> str` (l.124)
- `async def ingest_archives(conn, pairs: list[str], timeframes: list[str], months: int, dry_run: bool) -> tuple[int, int]` (l.132) — Importe les ZIP trimestriels. Rend (échecs, trimestres indisponibles).
- `async def ingest_full_archive(conn, pairs: list[str], timeframes: list[str], dry_run: bool) -> int` (l.205) — Importe l'archive complète (5 parties concaténées) par lecture Range.
- `async def ingest_rest(conn, pairs: list[str], timeframes: list[str], dry_run: bool) -> int` (l.264) — Complète la fin de série avec les 720 dernières bougies de l'API REST.
- `def print_coverage(rows: list[Coverage]) -> None` (l.302)
- `async def run(args: argparse.Namespace) -> int` (l.332)
- `def parse_args(argv: list[str] | None=None) -> argparse.Namespace` (l.415)
- `def main(argv: list[str] | None=None) -> int` (l.444)
- `async def _rest_only(args: argparse.Namespace) -> int` (l.451)

### `backend/scripts/kill_switch.py`

*Kill-switch autonome — solde la position et halte le bot, backend arrêté ou non.*

- `async def _run(dry_run: bool, confirm_live: bool) -> int` (l.37)
- `def main() -> int` (l.107)

### `backend/scripts/lab_rotation.py`

*Laboratoire de rotation momentum D1 — aucun ordre, aucune écriture.*

- `def _decimal_list(raw: str) -> list[Decimal]` (l.38)
- `def _int_list(raw: str) -> list[int]` (l.45)
- `async def _load(args) -> tuple[dict[str, list[list]], datetime | None, datetime]` (l.52)
- `def _print_result(result, *, fee_bps: Decimal, cadence: int, slippage: int, verbose: bool) -> None` (l.86)
- `async def _run(args) -> int` (l.134)
- `def main() -> int` (l.197)

### `backend/scripts/lessons.py`

*Mémoire de leçons — relecture des résultats, affichage, purge.*

- `async def cmd_show(repo, settings) -> int` (l.40)
- `async def cmd_history(repo, settings, limit: int) -> int` (l.52)
- `async def cmd_clear(repo, settings) -> int` (l.71)
- `async def cmd_review(repo, settings, args) -> int` (l.77)
- `async def main(args) -> int` (l.138)
- `def parse_args() -> argparse.Namespace` (l.153)

### `backend/scripts/live_probe.py`

*Sonde 3.1 — vérifier qu'un ordre réel rejeté ne fait pas planter le bot.*

- `def _decimal(value) -> Decimal` (l.40)
- `def _invalid_amount(exchange, symbol: str, price: Decimal) -> Decimal | None` (l.44) — Plus petite quantité strictement sous les deux minimums du marché.
- `def _run(symbol: str | None) -> int` (l.75)
- `def main() -> int` (l.150)

### `backend/scripts/probe_backtest.py`

*Sonde : combien de décisions LLM un backtest coûterait-il sur BTC/EUR ?*

- `def fetch_all(exchange, timeframe: str, days: int) -> list[list]` (l.28)
- `def main() -> None` (l.49)

### `backend/scripts/reconcile.py`

*Audit de réconciliation — lecture seule, aucun ordre, aucune écriture.*

- `async def _run(args: argparse.Namespace) -> int` (l.41)
- `def main(argv: list[str] | None=None) -> int` (l.95)

### `backend/scripts/shadow_report.py`

*Rapport des shadow trades — les achats que le bot n'a pas pris.*

- `async def report(args) -> int` (l.33)
- `def main() -> int` (l.83)

### `backend/scripts/short_lab.py`

*Mesure le mandat short sur le golden, sans réseau ni ordre.*

- `def main() -> int` (l.26)

### `backend/scripts/totp_setup.py`

*Génère un secret TOTP, son QR code et l'URI de provisionnement.*

- `def main() -> int` (l.25)

### `backend/scripts/verify_drawdowns.py`

*Vérifie les drawdowns sur un jeu de données figé, dans une transaction annulée.*

- **class _Rollback(Exception)** (l.45)
- `def _frozen_rows(now: datetime) -> list[tuple[datetime, Decimal]]` (l.49) — Cinq instantanés : HWM hors fenêtre, pic 7 j, pic du jour, valeur courante.
- `async def _run() -> int` (l.65)
- `def main() -> int` (l.128)

### `backend/tests/test_affordability.py`

*Tests de la faisabilité d'un ordre avec un petit capital.*

- `def config(risk: str='0.005', notional: str='0.20') -> RiskConfig` (l.41)
- `def test_prudent_profile_cannot_buy_the_cheapest_pair()` (l.45)
- `def test_raising_risk_alone_is_not_enough_when_the_exposure_cap_binds()` (l.65)
- `def test_micro_profile_both_knobs_together()` (l.81)
- `def test_absolute_cap_blocks_regardless_of_equity()` (l.97)
- `def test_non_decimal_and_non_finite_inputs_are_refused()` (l.111)
- `def test_the_prediction_matches_the_gate_actual_decision()` (l.123) — Le préflight doit prédire le refus réel du pare-feu, pas un refus théorique.

### `backend/tests/test_api.py`

*Tests de l'API d'administration : session, pause, panic-sell, WebSocket.*

- **class FakeRepo** (l.27)
  - `def __init__(self) -> None` (l.28)
  - `async def state(self, mode)` (l.37)
  - `async def account(self, mode, starting_equity)` (l.40)
  - `async def open_position(self, mode)` (l.48)
  - `async def recent(self, table, mode, limit)` (l.51)
  - `async def force_lock(self, mode, reason)` (l.56)
  - `async def force_resume(self, mode, reason, who='admin')` (l.60)
  - `async def halt_details(self, mode)` (l.66)
  - `async def runtime_settings(self, mode)` (l.69)
  - `async def update_runtime_settings(self, mode, payload, *, updated_by='dashboard')` (l.72)
  - `async def equity_curve(self, mode, limit=200)` (l.78)
  - `async def dashboard_metrics(self, mode)` (l.86)
  - `async def daily_pnl(self, mode, days=14)` (l.93)
  - `async def rejection_stats(self, mode)` (l.97)
  - `async def llm_stats(self, mode, hours=24)` (l.100)
  - `async def shadow_summary(self, mode)` (l.111)
  - `async def active_lessons(self, mode)` (l.119)
  - `async def recent_lessons(self, mode, limit=10)` (l.122)
- `def api(tmp_path)` (l.131)
- `def login(client, password: str=PASSWORD, code: str | None=None)` (l.170)
- `def token_of(client) -> str` (l.177)
- `def test_login_requires_both_factors(api)` (l.188)
- `def test_login_returns_a_seven_day_token(api)` (l.195)
- `def test_status_requires_a_valid_session(api)` (l.204)
- `def test_status_describes_the_bot(api)` (l.213)
- `def test_too_many_failed_logins_are_blocked(api)` (l.224)
- `def test_pause_then_resume(api)` (l.236)
- `def test_pause_is_refused_with_an_open_position(api)` (l.251)
- `def test_resume_is_refused_when_the_bot_is_not_stopped(api)` (l.265)
- `def test_panic_requires_a_fresh_totp(api)` (l.272)
- `def test_history_rejects_unknown_tables(api)` (l.290)
- `def test_websocket_refuses_a_bad_token(api)` (l.302)
- `def test_websocket_streams_the_status(api)` (l.310)
- `def test_stats_describes_pnl_model_and_equity(api)` (l.325)
- `def test_stats_requires_a_session(api)` (l.346)
- `def test_settings_are_exposed_without_jargon(api)` (l.351)
- `def test_settings_update_writes_to_the_database(api, tmp_path)` (l.368)
- `def test_settings_update_refuses_an_incoherent_daily_loss(api)` (l.395)
- `def test_position_payload_shows_the_net_risk_at_the_stop()` (l.407)
- `def test_position_payload_without_a_position_is_none()` (l.422)
- `def test_panic_is_wired_in_live_mode(tmp_path)` (l.426) — Le 409 « non câblé » a disparu : le panic réel passe par la liquidation.

### `backend/tests/test_api_security.py`

*Tests de la couche d'authentification : scrypt, JWT, TOTP, anti-rejeu.*

- `def test_password_roundtrip()` (l.19)
- `def test_password_hash_is_salted()` (l.26)
- `def test_malformed_hash_is_refused(stored)` (l.31)
- `def test_token_roundtrip_and_expiry()` (l.35)
- `def test_token_signed_with_another_key_is_refused()` (l.47)
- `def test_short_jwt_secret_is_refused()` (l.53)
- `def test_totp_accepts_the_current_code_once()` (l.58)
- `def test_totp_accepts_the_neighbouring_step_but_not_a_far_one()` (l.65)
- `def test_totp_refuses_malformed_or_wrong_codes(code)` (l.74)
- `def test_totp_invalid_secret_fails_closed()` (l.78)
- `def test_limiter_blocks_then_releases()` (l.83)
- `def test_require_secrets_accepts_a_complete_configuration()` (l.92)
- `def test_require_secrets_refuses_incomplete_configuration(jwt_secret, totp_secret, password_hash)` (l.102)

### `backend/tests/test_backtest.py`

*Tests du moteur de backtest : absence de lookahead, stop prioritaire, frais.*

- **class StubSignal** (l.84)
  - `def __init__(self, action='HOLD', confidence='0.9', regime='TRENDING_UP', thesis='stub', degraded=False, reason='')` (l.85)
- **class StubClient** (l.97) — Répond BUY à un index donné, HOLD ailleurs. Enregistre ce qu'il a vu.
  - `def __init__(self, buy_at: datetime | None=None, price_spy: list | None=None)` (l.100)
  - `async def __call__(self, snapshot, context, has_position: bool=False)` (l.105)
- **class ExitStub** (l.183) — BUY à l'entrée, puis SELL/HALT à une bougie de sortie choisie.
  - `def __init__(self, entry_at: datetime | None, exit_at: datetime | None, action: str='SELL', confidence: str='0.90')` (l.186)
  - `async def __call__(self, snapshot, context, has_position: bool=False)` (l.194)
- `def candles(start: datetime, count: int, step_ms: int) -> list[list]` (l.25) — Série qui monte, avec une oscillation pour éviter un RSI à 100.
- `def h1_series(count: int=2000) -> list[list]` (l.39)
- `def market_info() -> KrakenMarketInfo` (l.52)
- `def settings() -> SimpleNamespace` (l.70)
- `def close_time(candle: list) -> datetime` (l.113)
- `async def test_no_lookahead_the_snapshot_stops_at_the_decision_candle()` (l.118)
- `async def test_take_profit_is_reached_and_fees_are_paid()` (l.139)
- `async def test_stop_wins_when_a_candle_touches_both_levels()` (l.164)
- `async def test_llm_sell_closes_a_losing_or_winning_trade_before_stop()` (l.204)
- `async def test_llm_halt_closes_the_trade_and_stops_the_replay()` (l.223)
- `async def test_hold_does_not_open_a_position_but_feeds_the_counterfactual()` (l.242)
- `async def test_zone_rejects_below_ema200()` (l.257)
- `def test_zone_is_open_matches_the_client_rules()` (l.273)
- `def test_replay_exchange_only_serves_closed_candles()` (l.282)

### `backend/tests/test_backtest_golden.py`

*Régression de backtest : le comportement doit rester celui du golden.*

- `def _bundle_and_settings()` (l.49)
- `def test_le_prompt_et_le_modele_sont_ceux_du_golden()` (l.55)
- `def test_le_profil_de_risque_est_celui_du_golden()` (l.63)
- `async def test_le_rejeu_produit_les_metriques_attendues()` (l.74)

### `backend/tests/test_backtest_metrics.py`

*Métriques de validation : profit factor, espérance, gains et pertes moyens.*

- `def trade(pnl: str) -> TradeRecord` (l.19)
- `def result(trades: list[TradeRecord], equity_start: str='1000') -> BacktestResult` (l.36)
- `def test_profit_factor_oppose_gains_et_pertes_bruts()` (l.63)
- `def test_esperance_est_le_pnl_net_par_trade()` (l.72)
- `def test_gain_et_perte_moyens_sont_separes()` (l.80)
- `def test_sans_perte_le_profit_factor_est_non_interpretable()` (l.87) — Un PF sans perte ne vaut pas l'infini statistique : il ne prouve rien.
- `def test_sans_trade_toutes_les_metriques_sont_nulles_ou_absentes()` (l.96)
- `def test_une_seule_perte_donne_un_profit_factor_nul()` (l.105)
- `def test_le_resume_affiche_les_nouvelles_metriques()` (l.113)

### `backend/tests/test_check_exchange.py`

*Tests du verdict de la sonde exchange — la logique qui protège la clé.*

- `def compte(**kwargs) -> dict` (l.23)
- `def test_cle_avec_retraits_refusee()` (l.35)
- `def test_restrictions_saines_acceptees()` (l.41)
- `def test_can_withdraw_du_compte_n_est_pas_la_permission_de_la_cle()` (l.47)
- `def test_restrictions_incompletes_refusees_fail_closed()` (l.53)
- `def test_lecture_desactivee_refusee()` (l.61)
- `def test_trading_desactive_refuse()` (l.67)
- `def test_endpoint_restrictions_absent_mais_can_withdraw_refuse()` (l.75)
- `def test_cle_invalide_refusee()` (l.81)
- `def test_panne_reseau_refusee_fail_closed()` (l.87)

### `backend/tests/test_db_config.py`

*Tests de la résolution de connexion base (aucun accès réseau).*

- `def test_explicit_database_url_wins()` (l.15)
- `def test_defaults_filled_from_url()` (l.20)
- `def test_sslmode_read_from_query()` (l.26)
- `def test_password_with_special_characters_is_url_encoded()` (l.32)
- `def test_password_never_leaks_in_text_representations()` (l.39)
- `def test_derived_from_supabase_project()` (l.45)
- `def test_pooler_switch_uses_project_scoped_user()` (l.54)
- `def test_missing_password_raises()` (l.63)
- `def test_missing_everything_raises()` (l.68)
- `def test_malformed_project_url_raises()` (l.73)

### `backend/tests/test_entry_rule.py`

*Tests de la règle d'entrée déterministe — pure, sans réseau ni base.*

- `def snapshot(price: str='100', ema200: str='90', atr: str='1', h1_return: str='0', slope: str='10') -> MarketSnapshot` (l.19)
- `def context(ema50: str='99.5', rsi: str='55', *, ema50_h1: str='0', rsi_h1: str='0', atr_h1: str='0', low: str='0') -> MarketContext` (l.28)
- `def h1_context(ema50_h1: str='99.6', rsi_h1: str='42', atr_h1: str='0.5', low: str='99.0', ema50_h4: str='99.5', rsi_h4: str='55') -> MarketContext` (l.40) — Contexte d'un repli H1 valide sur l'EMA50 H1 (100 touche 99,6 et referme).
- `def test_pullback_in_an_uptrend_triggers()` (l.48)
- `def test_price_below_ema200_is_refused()` (l.55)
- `def test_ema50_below_ema200_is_refused()` (l.61)
- `def test_overbought_rsi_is_refused()` (l.67)
- `def test_weak_momentum_is_refused()` (l.73)
- `def test_dead_market_is_refused()` (l.79)
- `def test_abnormal_volatility_is_refused()` (l.85)
- `def test_falling_knife_is_refused()` (l.91)
- `def test_price_too_far_above_ema50_is_refused()` (l.97)
- `def test_price_too_far_below_ema50_is_refused()` (l.103)
- `def test_non_finite_price_is_refused()` (l.109)
- `def test_ema50_requirement_can_be_disabled()` (l.117)
- `def d1(**kw) -> D1Context` (l.130)
- `def swing_context(**kw) -> MarketContext` (l.146)
- `def test_swing_breakout_triggers()` (l.154)
- `def test_swing_value_rebound_triggers_when_no_breakout()` (l.161)
- `def test_swing_requires_close_above_ema200()` (l.169)
- `def test_swing_requires_positive_ema50_slope()` (l.175)
- `def test_swing_refuses_when_no_trigger()` (l.183)
- `def test_swing_refuses_without_d1_context()` (l.194)
- `def test_swing_trigger_flags_can_isolate_donchian()` (l.200)
- `def test_negative_last_h1_is_refused_when_the_filter_is_enabled()` (l.215)
- `def test_negative_last_h1_is_allowed_by_default()` (l.222)
- `def test_a_flat_ema200_is_not_an_uptrend()` (l.227)
- `def test_a_rising_ema200_passes_the_trend_gate()` (l.234)
- `def test_h1_pullback_that_touches_and_reclaims_triggers()` (l.245)
- `def test_h1_mode_still_requires_price_above_ema200()` (l.253)
- `def test_h1_mode_still_refuses_a_falling_knife()` (l.259)
- `def test_h1_mode_does_not_require_ema50_h4_above_ema200()` (l.265) — Le régime est porté par le prix au-dessus de l'EMA200 et la pente.
- `def test_h1_that_never_touched_the_ema50_is_refused()` (l.271)
- `def test_h1_that_closed_below_the_ema50_is_refused()` (l.277)
- `def test_h1_reclaim_too_far_above_the_ema50_is_refused()` (l.283)
- `def test_h1_rsi_below_the_band_is_refused()` (l.289)
- `def test_h1_rsi_above_the_band_is_not_a_pullback()` (l.295)
- `def test_h1_mode_refuses_when_the_h1_context_is_missing()` (l.301)
- `def test_unknown_entry_mode_is_refused_at_construction()` (l.307)
- `def test_incoherent_h1_rsi_bounds_are_refused_at_construction()` (l.312)

### `backend/tests/test_execution_factory.py`

*Tests du choix de broker : papier par défaut, live seulement sur demande.*

- **class FeeExchange** (l.15)
  - `def fetch_trading_fee(self, symbol)` (l.16)
- `def settings(mode: str='paper')` (l.20)
- `def test_paper_mode_builds_the_paper_broker()` (l.32)
- `def test_live_mode_builds_the_real_broker()` (l.37)
- `def test_testnet_is_refused_because_kraken_has_no_testnet_in_ccxt()` (l.42)

### `backend/tests/test_exit_guard.py`

*Tests du garde-fou de sortie — le LLM solde, il ne shorte jamais.*

- `def intent(action: str='SELL', confidence: str='0.9') -> TradeIntent` (l.14)
- `def test_sell_closes_the_whole_position()` (l.18)
- `def test_sell_without_position_is_refused()` (l.25)
- `def test_symbol_mismatch_is_refused()` (l.31)
- `def test_buy_is_not_an_exit()` (l.37)
- `def test_low_confidence_sell_is_refused()` (l.43)
- `def test_halt_requires_a_higher_confidence_than_sell()` (l.49)
- `def test_zero_amount_is_refused()` (l.58)
- `def test_nan_confidence_is_refused()` (l.65)

### `backend/tests/test_exit_rule.py`

*Tests du Chandelier Exit : niveau, cliquet et sortie à la clôture.*

- `def test_chandelier_stop_is_high_minus_multiple_atr()` (l.21)
- `def test_chandelier_stop_accepts_a_custom_multiple()` (l.25)
- `def test_chandelier_stop_refuses_a_non_positive_level()` (l.29)
- `def test_chandelier_stop_refuses_invalid_inputs(high, atr, multiple)` (l.40)
- `def test_the_trailing_stop_only_ratchets_up()` (l.45)
- `def test_the_initial_structural_stop_is_never_loosened()` (l.57)
- `def test_exit_only_on_a_close_strictly_below_the_level()` (l.64)

### `backend/tests/test_feed.py`

*Tests du flux de marché et des indicateurs, sur bougies synthétiques.*

- **class FakeExchange** (l.27)
  - `def __init__(self, h4: list[list], h1: list[list], now_ms: int, book: dict | None=None, d1: list[list] | None=None)` (l.28)
  - `def fetch_ohlcv(self, symbol, timeframe, limit=None)` (l.34)
  - `def milliseconds(self)` (l.39)
  - `def fetch_order_book(self, symbol, limit=20)` (l.42)
- `def candles(n: int, start: float=100.0, step: float=0.1, spread: float=0.5, t0: int=1700000000000) -> list[list]` (l.18)
- `def test_ema_rises_in_an_uptrend()` (l.46)
- `def test_ema_refuses_short_history()` (l.52)
- `def test_atr_matches_a_constant_range()` (l.57)
- `def test_rsi_is_maximal_on_a_pure_uptrend()` (l.63)
- `def test_rsi_is_minimal_on_a_pure_downtrend()` (l.67)
- `def test_macd_is_positive_in_an_uptrend()` (l.72)
- `def test_closed_candles_drop_the_unfinished_one()` (l.76)
- `def test_snapshot_with_d1_exposes_the_structural_swing_low()` (l.86)
- `def test_d1_context_exposes_the_chandelier_highest_high()` (l.112)
- `def test_d1_context_refuses_an_absurd_chandelier_period()` (l.130)
- `def test_build_snapshot_uses_only_closed_candles()` (l.139)
- `def test_build_snapshot_refuses_insufficient_history()` (l.158)

### `backend/tests/test_history.py`

*Historique OHLCVT : analyse pure du CSV Kraken, trous, agrégation, cohérence.*

- `def at(hour: int, minute: int=0) -> int` (l.29) — Timestamp ms d'un instant fixe (2024-01-01) pour des tests lisibles.
- `def candle(open_ms: int, o: str='100', h: str='110', low: str='90', c: str='105', v: str='1') -> list` (l.35)
- `def test_parse_ohlcvt_lit_le_format_kraken_et_trie()` (l.44)
- `def test_parse_ohlcvt_ignore_entete_et_lignes_vides()` (l.57)
- `def test_parse_ohlcvt_refuse_une_ligne_incomplete()` (l.64)
- `def test_parse_ohlcvt_refuse_un_timestamp_illisible()` (l.69)
- `def test_parse_ohlcvt_garde_la_derniere_occurrence_d_un_timestamp()` (l.74)
- `def test_parse_ohlcvt_refuse_un_timeframe_non_gere()` (l.84)
- `def test_detect_gaps_ne_signale_rien_sur_une_serie_continue()` (l.93)
- `def test_detect_gaps_mesure_un_trou_en_bougies_et_en_heures()` (l.98)
- `def test_detect_gaps_ignore_un_duplicata_ou_un_desordre()` (l.112)
- `def test_detect_gaps_travaille_aussi_en_h4()` (l.117)
- `def test_aggregate_fusionne_quatre_heures_en_une_bougie()` (l.128)
- `def test_aggregate_ignore_une_fenetre_incomplete()` (l.146) — Trois heures sur quatre ne font pas une bougie : elle serait fausse.
- `def test_aggregate_refuse_une_conversion_impossible()` (l.152)
- `def test_merge_dedoublonne_par_timestamp_et_garde_la_derniere()` (l.161)
- `def test_reconcile_aggregation_valide_une_archive_coherente()` (l.170)
- `def test_reconcile_aggregation_signale_un_prix_divergent()` (l.183)
- `def test_reconcile_aggregation_ne_compare_que_les_fenetres_communes()` (l.195)
- `def test_storage_verdict_autorise_une_ecriture_qui_laisse_le_plancher()` (l.212)
- `def test_storage_verdict_refuse_une_ecriture_qui_passe_sous_le_plancher()` (l.222)
- `def test_storage_verdict_refuse_une_base_deja_sous_le_plancher()` (l.232) — Même une écriture vide est refusée : le plancher est une règle, pas un budget.
- `def test_storage_verdict_accepte_l_egalite_au_plancher()` (l.241)
- `def test_storage_verdict_mesure_la_marge_actuelle()` (l.250)

### `backend/tests/test_kraken_adapter.py`

*Tests de la passerelle Kraken, hors réseau.*

- **class FakeKraken** (l.48) — Surface minimale de CCXT utilisée par l'adaptateur, sans réseau.
  - `def __init__(self, market: dict | None=None, permissions: list[str] | None=None, boom: Exception | None=None, extra: dict | None=None)` (l.51)
  - `def market(self, symbol: str) -> dict` (l.60)
  - `def amount_to_precision(self, symbol: str, amount) -> str` (l.65)
  - `def price_to_precision(self, symbol: str, price) -> str` (l.70)
  - `def privatePostGetApiKeyInfo(self) -> dict` (l.76)
  - `def create_order(self, **kwargs)` (l.82)
- **class FeeExchange** (l.122)
  - `def __init__(self, taker: str | None=None, *, fail: bool=False)` (l.123)
  - `def fetch_trading_fee(self, symbol)` (l.127)
- `def decision(**kwargs) -> RiskDecision` (l.96)
- `def test_market_info_maps_limits_to_decimal_constraints()` (l.111)
- `def test_resolve_taker_fee_reads_the_account_tier()` (l.133)
- `def test_resolve_taker_fee_falls_back_when_the_private_call_fails()` (l.138)
- `def test_market_info_accepts_the_real_fee_as_an_override()` (l.143)
- `def test_eur_pair_is_spot_and_active()` (l.148)
- `def test_non_spot_market_is_refused()` (l.154)
- `def test_amount_is_never_rounded_above_the_validated_quantity()` (l.165)
- `def test_float_amount_is_refused()` (l.172)
- `def test_stop_rounded_below_the_validated_price_is_refused()` (l.177)
- `def test_stop_rounded_up_is_accepted()` (l.183)
- `def test_bracket_uses_the_close_stop_loss_parameter()` (l.192)
- `def test_bracket_entry_shape_is_kraken_compatible()` (l.197)
- `def test_unapproved_decision_cannot_produce_an_order()` (l.210)
- `def test_approved_decision_without_stop_is_refused()` (l.216)
- `def test_healthy_key_passes()` (l.226)
- `def test_ui_style_labels_are_normalised()` (l.231)
- `def test_ip_allowlist_is_read_in_snake_case()` (l.246)
- `def test_ip_allowlist_is_read_in_camel_case_too()` (l.255)
- `def test_withdraw_permission_is_fatal()` (l.262)
- `def test_withdraw_label_in_ui_style_is_also_caught()` (l.267)
- `def test_missing_cancel_permission_is_fatal()` (l.274)
- `def test_old_cancel_label_is_still_accepted()` (l.280)
- `def test_unreadable_permissions_fail_closed()` (l.287)
- `def test_empty_permissions_fail_closed()` (l.292)
- `def test_build_exchange_sets_the_two_non_negotiable_options()` (l.302)

### `backend/tests/test_kraken_archive.py`

*Lecture ZIP par plages HTTP : le mécanisme qui évite 9,5 Go de téléchargement.*

- `def build_zip(members: dict[str, bytes], compression: int=zipfile.ZIP_DEFLATED) -> bytes` (l.26)
- `def test_lit_un_membre_deflate_sans_telecharger_l_archive()` (l.41)
- `def test_lit_un_membre_stored()` (l.48)
- `def test_liste_tous_les_membres_avec_leurs_tailles()` (l.55)
- `def test_un_membre_absent_est_une_erreur_explicite()` (l.64)
- `def test_un_entete_local_corrompu_est_refuse()` (l.72)
- `def test_une_source_hors_bornes_est_refusee()` (l.82)
- `def test_multi_parties_reconstitue_l_archive_concatenee()` (l.93)
- `def test_multi_parties_refuse_une_plage_hors_bornes()` (l.107)
- `def test_multi_parties_exige_au_moins_une_partie()` (l.114)
- `def test_parse_central_directory_lit_l_offset_zip64()` (l.123) — Le répertoire central d'une grosse archive met l'offset dans l'extra.
- `def test_parse_central_directory_ignore_les_octets_non_reconnus()` (l.144)

### `backend/tests/test_lessons.py`

*Tests de la mémoire de leçons — purs, sans réseau ni base.*

- **class FakeRepo** (l.113)
  - `def __init__(self, positions, shadows)` (l.114)
  - `async def closed_positions_for_lessons(self, mode, limit=30)` (l.118)
  - `async def shadow_outcomes_for_lessons(self, mode, limit=30)` (l.121)
- **class FakeClient** (l.125)
  - `def __init__(self, lessons)` (l.126)
  - `async def review(self, digest)` (l.129)
- `def position() -> dict` (l.29)
- `def shadow() -> dict` (l.39)
- `def test_digest_contains_trades_and_shadows()` (l.48)
- `def test_digest_handles_no_data()` (l.59)
- `def test_sanitize_lessons_deduplicates_and_caps()` (l.64)
- `def test_sanitize_lessons_rejects_empty()` (l.73)
- `def test_sanitize_lessons_rejects_a_bypass()` (l.80)
- `def test_lessons_block_is_none_without_lessons()` (l.85)
- `def test_payload_includes_the_lessons_block()` (l.92)
- `def test_review_refuses_under_the_minimum()` (l.133)
- `def test_review_can_be_forced()` (l.139)
- `def test_review_parses_the_model_response()` (l.149)
- `def test_review_rejects_a_malformed_response()` (l.164)

### `backend/tests/test_live_execution.py`

*Tests de l'exécuteur réel — aucun réseau, aucune clé, aucun ordre réel.*

- **class FakeExchange** (l.32)
  - `def __init__(self, *, stop_missing: bool=False, order_status: str='closed', create_raises: bool=False, sell_raises: bool=False, base_free: str='0', support_edit: bool=False, stop_create_raises: bool=False, fail_cancel_call: int | None=None)` (l.33)
  - `def amount_to_precision(self, symbol, amount)` (l.54)
  - `def price_to_precision(self, symbol, price)` (l.57)
  - `def _new_id(self, prefix: str) -> str` (l.60)
  - `def create_order(self, symbol, type, side, amount, price=None, params=None)` (l.64)
  - `def edit_order(self, order_id, symbol, type, side, amount, price=None, params=None)` (l.119)
  - `def fetch_order(self, order_id, symbol=None)` (l.130)
  - `def fetch_open_orders(self, symbol=None)` (l.133)
  - `def fetch_my_trades(self, symbol=None, since=None, limit=None)` (l.136)
  - `def cancel_order(self, order_id, symbol=None)` (l.139)
  - `def fetch_balance(self)` (l.152)
- **class PanicExchange(FakeExchange)** (l.435) — Exchange avec soldes mutables : une vente au marché les consomme.
  - `def __init__(self, balances: dict[str, str], markets: dict | None=None, sell_raises: bool=False) -> None` (l.438)
  - `def fetch_balance(self)` (l.444)
  - `def create_order(self, symbol, type, side, amount, price=None, params=None)` (l.448)
- `def decision(**overrides) -> RiskDecision` (l.16)
- `def broker(exchange: FakeExchange, **overrides) -> LiveBroker` (l.159)
- `async def _noop_sleep(_seconds: float) -> None` (l.171)
- `async def test_entry_places_a_bracket_and_reads_the_actual_fee()` (l.176)
- `async def test_entry_without_a_stop_unwinds_the_position()` (l.192)
- `async def test_entry_is_refused_when_the_exchange_rejects_the_buy()` (l.203)
- `async def test_an_unfilled_order_is_canceled_and_raises()` (l.217)
- `async def test_exit_cancels_protections_before_selling()` (l.227)
- `async def test_exit_rearms_the_stop_when_the_sale_fails()` (l.247)
- `async def test_balance_exposes_free_quote_and_base()` (l.265)
- `async def test_position_state_reads_the_base_and_the_stop()` (l.273)
- `async def test_last_sell_fill_reads_the_real_trade_and_its_fee()` (l.290)
- `async def test_entry_refuses_a_shared_base_balance()` (l.309)
- `async def _entered(exchange: FakeExchange)` (l.321)
- `async def test_stop_replacement_creates_the_new_stop_before_cancelling_the_old()` (l.328)
- `async def test_supported_native_edit_keeps_the_same_stop()` (l.348)
- `async def test_an_unchanged_stop_price_is_a_noop()` (l.363)
- `async def test_a_lower_stop_is_refused()` (l.378)
- `async def test_a_failed_replacement_keeps_the_old_stop()` (l.392)
- `async def test_a_failed_cancel_of_the_old_stop_removes_the_new_one()` (l.407)
- `async def test_a_terminated_old_stop_is_not_replaced()` (l.422)
- `async def test_panic_sell_cancels_every_order_and_flattens_every_balance()` (l.459)
- `async def test_panic_sell_reports_a_failed_sale_without_raising()` (l.485)
- `async def test_panic_sell_without_balance_is_a_clean_noop()` (l.497)

### `backend/tests/test_llm.py`

*Tests du client LLM : parsing strict, repli, garde-fous déterministes.*

- `def settings(**kwargs) -> LLMSettings` (l.36)
- `def client(content: str=VALID, status: int=200) -> SignalClient` (l.42)
- `def test_valid_response_is_parsed()` (l.58)
- `def test_halt_response_is_parsed()` (l.66)
- `def test_schema_allows_the_halt_action()` (l.75)
- `def test_json_wrapped_in_prose_is_recovered()` (l.81)
- `def test_invalid_responses_are_refused(payload)` (l.93)
- `def test_numeric_fields_from_the_model_are_ignored()` (l.98)
- `def test_thesis_is_truncated_to_200_characters()` (l.106)
- `async def test_propose_returns_a_signal_and_hides_account_data()` (l.118)
- `async def test_timeout_falls_back_to_hold_with_zero_confidence()` (l.131)
- `async def test_http_error_falls_back_to_hold()` (l.144)
- `async def test_garbage_response_falls_back_to_hold()` (l.151)
- `async def test_buy_below_ema200_is_downgraded_even_if_the_model_insists()` (l.163)
- `async def test_buy_with_rsi_above_70_is_downgraded()` (l.176)
- `async def test_sell_and_hold_are_not_downgraded()` (l.187)
- `def capturing_client(content: str=VALID) -> tuple[SignalClient, dict]` (l.216)
- `async def test_daily_uses_the_d1_prompt_and_indicator_payload()` (l.227)
- `async def test_h4_keeps_its_historical_prompt_and_version()` (l.248)
- `async def test_an_unknown_decision_timeframe_is_refused()` (l.258)
- `async def test_daily_without_context_falls_back_to_hold()` (l.266)
- `async def test_daily_buy_below_ema200_is_downgraded()` (l.277)

### `backend/tests/test_llm_eval.py`

*Métriques de l'évaluation LLM : rétention des gagnants, filtration des perdants.*

- `def record(pnl: str, *, approved: bool, symbol: str='BTC/EUR') -> EvalRecord` (l.18)
- `def test_retention_and_filtration_are_computed_on_both_populations()` (l.34)
- `def test_a_model_that_keeps_every_winner_and_filters_half_the_losers_passes()` (l.56)
- `def test_filtration_below_the_floor_is_refused()` (l.66)
- `def test_empty_populations_stay_not_applicable()` (l.75)
- `def test_a_flat_trade_counts_as_a_loss_for_the_model()` (l.83)

### `backend/tests/test_machine.py`

*Tests de la machine à états : le graphe est une contrainte, pas une convention.*

- **class FakeRepo** (l.13)
  - `def __init__(self) -> None` (l.14)
  - `async def transition(self, mode, expected, target, reason, code=None, context=None)` (l.17)
- `async def test_expected_transition_is_written()` (l.22)
- `async def test_illegal_transition_is_refused_before_writing()` (l.31)
- `async def test_locked_and_halted_states_have_no_automatic_exit()` (l.41)
- `def test_unknown_state_is_refused_at_construction()` (l.50)

### `backend/tests/test_mean_reversion.py`

*Tests du laboratoire Mean Reversion — aucun réseau, aucun ordre.*

- `def flat_candles(count: int=WARMUP + 40) -> list[list]` (l.23) — Marché plat : ATR 2, aucune bande franchie, volume constant.
- `def dip_candles(*, recovery_close: str='99.9') -> list[list]` (l.34) — Une seule entrée : chute à 97 sous la bande basse, puis retour à la médiane.
- `def settings_for(tmp_path, **overrides)` (l.47)
- `def info() -> SimpleNamespace` (l.58)
- `def test_the_setup_requires_all_three_conditions()` (l.70)
- `def test_the_gate_refuses_a_counter_trend_entry_and_the_lab_can_measure_it(tmp_path)` (l.84)
- `def test_the_exit_can_wait_for_a_close_above_the_middle_band(tmp_path)` (l.105)
- `def test_an_invalid_exit_mode_is_refused(tmp_path)` (l.128)

### `backend/tests/test_orchestrator.py`

*Tests de bout en bout de l'orchestrateur, avec des doubles.*

- **class FakeRepo** (l.65)
  - `def __init__(self, state: str=IDLE, position: PositionRow | None=None, free_quote: str='9.37')` (l.66)
  - `async def ensure_mode(self, mode)` (l.94)
  - `async def state(self, mode)` (l.96)
  - `async def transition(self, mode, expected, target, reason, code=None, context=None)` (l.98)
  - `async def account(self, mode, starting)` (l.104)
  - `async def open_position(self, mode)` (l.106)
  - `async def snapshot_equity(self, mode, **kwargs)` (l.108)
  - `async def insert_signal(self, mode, symbol, model_name, **kwargs)` (l.110)
  - `async def last_signal_at(self, mode, symbol)` (l.115)
  - `async def insert_evaluation(self, mode, symbol, **kwargs)` (l.118)
  - `async def insert_shadow_trade(self, mode, symbol, **kwargs)` (l.122)
  - `async def create_position(self, mode, **kwargs)` (l.126)
  - `async def insert_order(self, mode, **kwargs)` (l.135)
  - `async def close_position(self, position_id, **kwargs)` (l.139)
  - `async def update_after_close(self, mode, **kwargs)` (l.143)
  - `async def insert_sweep(self, mode, **kwargs)` (l.145)
  - `async def deferred_sweep_quote(self, mode)` (l.149)
  - `async def set_cooldown(self, mode, until)` (l.152)
  - `async def force_halt(self, mode, reason, code=None, auto_resume_at=None)` (l.154)
  - `async def halt_details(self, mode)` (l.160)
  - `async def force_resume(self, mode, reason, who='admin')` (l.163)
  - `async def runtime_settings(self, mode)` (l.168)
  - `async def active_lessons(self, mode)` (l.171)
- **class FakeLLM** (l.175)
  - `def __init__(self, signal: LLMSignal)` (l.176)
  - `async def propose(self, symbol, snapshot, context, **kwargs)` (l.181)
- **class LiveFakeBroker** (l.407) — Broker live minimal : l'exchange dit si la position existe encore.
  - `def __init__(self, *, base_free: Decimal, stop_ok: bool, fill: Fill)` (l.410)
  - `def fee(self, notional)` (l.417)
  - `def exit_trigger(stop_loss_price, take_profit_price, candle)` (l.421)
  - `async def position_state(self, symbol, amount, stop_price)` (l.424)
  - `async def last_sell_fill(self, symbol, *, since=None, limit=20)` (l.430)
  - `async def exit(self, amount, market_price, *, symbol, stop_price=None)` (l.433)
  - `async def emergency_exit(self, amount, *, symbol, stop_price=None, market_price=None)` (l.437)
  - `async def free_quote(self)` (l.442)
- `def symbol_state(price: str='100', ema: str='90', ema50: str | None=None) -> SymbolState` (l.46)
- `def llm_signal(action: str='BUY', confidence: str='0.9', *, degraded: bool=False, reason: str | None=None) -> LLMSignal` (l.187)
- `def settings(tmp_path: Path, **extra)` (l.197)
- `def orchestrator(tmp_path: Path, repo: FakeRepo, llm: FakeLLM, settings_=None) -> Orchestrator` (l.208)
- `def _patch_feed(monkeypatch)` (l.219)
- `async def test_approved_intent_opens_a_paper_position(tmp_path)` (l.227)
- `async def test_hold_never_reaches_the_risk_gate(tmp_path)` (l.241)
- `async def test_active_lessons_are_passed_to_the_model(tmp_path)` (l.253)
- `async def test_rejected_buy_is_recorded_as_a_shadow_trade(tmp_path)` (l.262)
- `async def test_degraded_llm_produces_no_evaluation(tmp_path)` (l.278)
- `async def test_counter_trend_buy_is_refused_by_the_gate(tmp_path, monkeypatch)` (l.289)
- `async def test_cooldown_is_resumed_after_a_restart(tmp_path)` (l.310)
- `async def test_expired_cooldown_returns_to_idle(tmp_path)` (l.323)
- `async def test_a_closed_candle_is_evaluated_only_once(tmp_path)` (l.333)
- `async def test_open_position_is_recovered_after_a_restart(tmp_path, monkeypatch)` (l.352)
- `def test_drawdown_maths_are_exact()` (l.372)
- `async def test_candle_hitting_the_stop_closes_the_position_and_cools_down(tmp_path, monkeypatch)` (l.382)
- `def live_position() -> PositionRow` (l.446)
- `def stop_fill() -> Fill` (l.455)
- `async def test_a_live_stop_already_triggered_is_recorded_without_another_sell(tmp_path)` (l.464)
- `async def test_a_live_position_without_a_stop_is_sold_then_halted(tmp_path)` (l.484)
- `async def test_winning_trade_sweeps_thirty_percent(tmp_path, monkeypatch)` (l.505)
- `async def test_small_win_is_recorded_as_skipped(tmp_path, monkeypatch)` (l.542)
- `async def test_deferred_sweeps_accumulate_until_the_threshold(tmp_path, monkeypatch)` (l.565)
- `def open_position(opened_hours_ago: int=5) -> PositionRow` (l.599)
- `def flat_candle() -> list` (l.609)
- `async def test_llm_sell_closes_the_position(tmp_path, monkeypatch)` (l.614)
- `async def test_llm_sell_without_a_position_is_ignored(tmp_path)` (l.629)
- `async def test_llm_halt_liquidates_and_halts(tmp_path, monkeypatch)` (l.642)
- `async def test_llm_halt_when_flat_halts_without_trade(tmp_path)` (l.656)
- `async def test_low_confidence_halt_is_ignored(tmp_path)` (l.668)
- `async def test_entry_rule_blocks_a_far_pullback(tmp_path, monkeypatch)` (l.680)
- `async def test_llm_halt_sets_a_24h_auto_resume(tmp_path)` (l.701)
- `async def test_llm_halt_resumes_automatically_after_the_deadline(tmp_path)` (l.710)
- `async def test_human_halt_is_not_resumed_automatically(tmp_path)` (l.725)

### `backend/tests/test_paper.py`

*Tests de l'exécution papier : frais, slippage, gaps, priorité du stop.*

- `def decision(amount: str='1', **kwargs) -> RiskDecision` (l.13)
- `def broker() -> PaperBroker` (l.25)
- `def test_entry_pays_the_market_plus_slippage_and_fees()` (l.29)
- `def test_exit_receives_the_market_minus_slippage_and_fees()` (l.37)
- `def test_entry_refuses_an_unapproved_decision()` (l.44)
- `def test_stop_triggers_at_the_stop_price()` (l.49)
- `def test_gap_below_the_stop_fills_at_the_open()` (l.54)
- `def test_take_profit_triggers_at_the_level()` (l.60)
- `def test_gap_above_the_take_profit_fills_at_the_open()` (l.65)
- `def test_when_both_levels_are_touched_the_stop_wins()` (l.70)
- `def test_no_trigger_inside_the_range()` (l.77)

### `backend/tests/test_reconciliation.py`

*Tests de la réconciliation base ↔ exchange, sans réseau ni base réelle.*

- **class FakeExchange** (l.178)
  - `def __init__(self, orders, balance)` (l.179)
  - `def fetch_open_orders(self)` (l.183)
  - `def fetch_balance(self)` (l.186)
- **class FakeRepo** (l.242)
  - `def __init__(self, positions)` (l.243)
  - `async def open_positions(self, mode)` (l.247)
- `def position(amount: str='0.010', stop: str='96') -> PositionRow` (l.32)
- `def order(*, amount: str='0.010', stop: str | None='96', side: str='sell', symbol: str='BTC/EUR', order_id: str='O-1', order_type: str='stop-loss', client_order_id: str | None=None) -> RemoteOrder` (l.41)
- `def plan(positions, orders=(), balances=None, mode='LIVE')` (l.53)
- `def codes(result) -> list[str]` (l.62)
- `def test_paper_mode_never_liquidates_an_internal_position()` (l.69)
- `def test_an_order_found_in_paper_mode_is_only_a_warning()` (l.77)
- `def test_live_position_with_an_armed_stop_is_clean()` (l.90)
- `def test_live_position_without_a_stop_must_be_liquidated()` (l.100)
- `def test_a_stop_covering_less_than_the_position_is_insufficient()` (l.109)
- `def test_a_stop_below_the_validated_price_is_insufficient()` (l.117)
- `def test_a_stop_above_the_validated_price_is_accepted()` (l.125)
- `def test_a_buy_order_is_not_a_protective_stop()` (l.132)
- `def test_missing_base_balance_halts_without_selling()` (l.139)
- `def test_unmatched_orders_are_reported_but_never_cancelled()` (l.149)
- `def test_an_orphan_stop_without_a_position_halts_the_bot()` (l.164)
- `def test_read_remote_orders_uses_stop_loss_price_and_ignores_foreign_symbols()` (l.190)
- `def test_read_remote_orders_accepts_trigger_price_but_not_a_percentage()` (l.207)
- `def test_read_account_prefers_total_and_falls_back_to_free()` (l.227)
- `async def test_inspect_exchange_reads_positions_orders_and_balance()` (l.253)

### `backend/tests/test_recovery.py`

*Tests du kill-switch : liquidation, annulation des ordres, halte forcée.*

- **class FakeRepo** (l.29)
  - `def __init__(self, pos: PositionRow | None)` (l.30)
  - `async def account(self, mode, starting)` (l.44)
  - `async def open_position(self, mode)` (l.52)
  - `async def open_positions(self, mode)` (l.56)
  - `async def open_orders(self, mode)` (l.60)
  - `async def update_order_status(self, order_id, status, error_message=None)` (l.62)
  - `async def insert_order(self, mode, **kwargs)` (l.65)
  - `async def close_position(self, position_id, **kwargs)` (l.69)
  - `async def update_after_close(self, mode, **kwargs)` (l.72)
  - `async def snapshot_equity(self, mode, **kwargs)` (l.75)
  - `async def force_halt(self, mode, reason, code=None)` (l.78)
- `def position(symbol: str='BTC/EUR') -> PositionRow` (l.21)
- `def _price(monkeypatch)` (l.83)
- `async def test_a_position_is_liquidated_and_the_bot_halted()` (l.88)
- `async def test_the_kill_switch_halts_even_without_a_position()` (l.110)
- `async def test_emergency_exit_pays_fees_and_slippage()` (l.122)
- `async def test_apply_reconciliation_liquidates_the_planned_symbol()` (l.133)
- `async def test_apply_reconciliation_halts_without_liquidating_on_a_simple_alert()` (l.151)
- `def test_apply_reconciliation_refuses_paper_mode()` (l.168)
- `async def test_panic_exits_write_the_real_fill_to_the_ledger()` (l.182)
- `async def test_panic_exits_record_an_untracked_balance_and_surface_errors()` (l.214)

### `backend/tests/test_risk_gate.py`

*Tests du pare-feu transactionnel.*

- `def market(price='100', ema='90', atr='2', h1='-0.2', age_s=0.0) -> MarketSnapshot` (l.40)
- `def account(**kw) -> AccountState` (l.52)
- `def intent(action=Action.BUY, confidence=0.9, regime=MarketRegime.TRENDING_UP) -> TradeIntent` (l.58)
- `def run(m=None, a=None, i=None, c=CONSTRAINTS, cfg=RiskConfig())` (l.62)
- `def test_happy_path_sizing_is_exact()` (l.71)
- `def test_determinism()` (l.86)
- `def test_full_check_chain_reported()` (l.90)
- `def test_only_buy_is_actionable(action)` (l.102)
- `def test_garbage_action_rejected()` (l.108)
- `def test_confidence_out_of_bounds_rejected()` (l.114)
- `def test_low_confidence_rejected()` (l.120)
- `def test_llm_bearish_regime_blocks_entry()` (l.126)
- `def test_string_enums_accepted_from_json()` (l.132)
- `def test_stale_data_rejected()` (l.142)
- `def test_future_timestamp_rejected()` (l.147)
- `def test_invalid_market_data_rejected(price, ema, atr)` (l.153)
- `def test_zero_equity_rejected()` (l.158)
- `def test_locked_bot_rejected()` (l.168)
- `def test_circuit_breakers(field, value, code)` (l.182)
- `def test_circuit_breakers_below_threshold_pass()` (l.188)
- `def test_order_rate_limit()` (l.192)
- `def test_counter_trend_rejected_at_ema()` (l.202)
- `def test_trend_buffer_matters()` (l.208)
- `def test_falling_knife_rejected()` (l.214)
- `def test_falling_knife_threshold_is_inclusive()` (l.219)
- `def test_dead_market_rejected()` (l.226)
- `def test_abnormal_volatility_rejected()` (l.231)
- `def test_position_already_open()` (l.241)
- `def test_other_symbol_fills_max_positions()` (l.246)
- `def test_notional_cap_clamps_size_and_lowers_effective_risk()` (l.256)
- `def test_cash_cap_clamps_size()` (l.267)
- `def test_absolute_notional_cap()` (l.274)
- `def test_min_notional_rejected()` (l.281)
- `def test_min_amount_rejected()` (l.286)
- `def test_zero_free_balance_rejected()` (l.295)
- `def test_loss_streak_halves_risk()` (l.300)
- `def test_stop_is_truncated_down_never_up()` (l.307)
- `def test_a_single_buy_cannot_engage_more_than_half_the_capital()` (l.321)
- `def test_risk_budget_never_exceeded(atr, equity, cash_ratio)` (l.329)
- `def test_dangerous_config_rejected(kwargs)` (l.370)
- `def test_naive_datetime_is_rejected_not_raised()` (l.381)
- `def test_non_finite_price_is_rejected(bad)` (l.398)
- `def test_non_finite_atr_is_rejected()` (l.404)
- `def test_unexpected_type_becomes_internal_error_not_exception()` (l.409)
- `def test_evaluate_never_raises_on_hostile_input()` (l.417)
- `def swing_market(price='100', swing_low='96', **kw) -> MarketSnapshot` (l.435)
- `def test_structural_stop_uses_the_d1_swing_low()` (l.452)
- `def test_structural_stop_is_capped_at_five_percent()` (l.459)
- `def test_structural_stop_without_swing_low_is_refused()` (l.466)
- `def test_small_account_full_allocation_requires_the_flag()` (l.472)
- `def test_small_account_floor_reaches_the_exchange_minimum()` (l.501)
- `def test_small_account_floor_refuses_when_above_the_cap()` (l.521)
- `def test_free_balance_below_min_notional_blocks_new_entry()` (l.547)
- `def test_swing_exit_mode_defaults_to_ema20()` (l.559)
- `def test_swing_exit_mode_can_be_chandelier()` (l.563)
- `def test_unknown_swing_exit_mode_is_refused()` (l.568)
- `def test_chandelier_period_must_stay_in_bounds(period)` (l.574)
- `def test_chandelier_multiple_must_be_positive_and_finite(multiple)` (l.580)

### `backend/tests/test_rotation.py`

*Tests du laboratoire de rotation — aucun réseau, aucun ordre.*

- `def candles(price, count: int=320) -> list[list]` (l.16)
- `def universe(btc, eth, sol) -> dict[str, list[list]]` (l.24)
- `def test_all_assets_below_their_ema_stay_in_cash()` (l.28)
- `def test_the_strongest_asset_above_its_ema_takes_the_whole_allocation()` (l.42)
- `def test_fees_reduce_the_final_equity()` (l.61)
- `def test_a_crash_below_the_ema_sends_the_book_to_cash()` (l.81)
- `def test_momentum_is_a_plain_average_of_the_two_windows()` (l.104)
- `def test_an_invalid_window_is_refused()` (l.117)

### `backend/tests/test_scheduler.py`

*Tests de la cadence : prochaine clôture de bougie, alignée sur l'époque Unix.*

- `def at(hour: int, minute: int=0, second: int=0, day: int=4) -> datetime` (l.18)
- `def test_next_h4_close_from_mid_candle()` (l.22)
- `def test_exactly_on_the_close_returns_the_following_one()` (l.26)
- `def test_next_h4_close_crosses_midnight()` (l.32)
- `def test_next_h1_close()` (l.36)
- `def test_next_daily_close()` (l.41)
- `def test_a_paris_timestamp_is_converted_to_utc()` (l.45)
- `def test_seconds_until_next_close_is_positive()` (l.51)
- `def test_naive_datetime_is_refused()` (l.55)
- `def test_unknown_timeframe_is_refused()` (l.60)
- `def test_daily_decisions_wait_five_minutes_after_the_close()` (l.70)
- `def test_intraday_decisions_keep_the_short_margin(timeframe)` (l.77)
- `def test_delay_refuses_an_unknown_timeframe()` (l.81)

### `backend/tests/test_schema_contract.py`

*Contrat entre le schéma SQL et le code Python.*

- `def enum_labels(type_name: str) -> set[str]` (l.26) — Libellés d'un ENUM, à travers **toutes** les migrations.
- `def test_python_codes_all_exist_in_sql()` (l.55)
- `def test_sql_codes_all_exist_in_python()` (l.64)
- `def test_rejection_code_has_no_empty_label()` (l.74)
- `def test_system_state_includes_executing()` (l.83)
- `def test_order_type_supports_binance_spot_stop()` (l.89)
- `def test_order_type_never_used_for_entry_is_declared()` (l.97)
- `def test_signal_action_supports_halt()` (l.101)
- `def test_order_purpose_supports_llm_exit()` (l.107)
- `def test_migration_files_are_ordered_and_named()` (l.118)
- `def test_no_migration_uses_timescaledb()` (l.125)
- `def test_every_partitioned_table_declares_a_default_partition()` (l.135)
- `def test_guardrails_are_enforced_in_the_database(guard)` (l.155)
- `def test_execution_mode_separates_paper_from_live()` (l.159)
- `def test_migration_0009_allows_daily_candles()` (l.163)

### `backend/tests/test_settings.py`

*Tests du chargement de configuration : presets, écrasements, verrous d'armement.*

- `def test_defaults_are_paper_and_do_not_touch_real_money(tmp_path: Path)` (l.14)
- `def test_profile_preset_changes_several_knobs_at_once(tmp_path: Path)` (l.24)
- `def test_individual_override_beats_preset(tmp_path: Path)` (l.30)
- `def test_swing_preset_enables_d1_and_small_account_allocation(tmp_path: Path)` (l.39)
- `def test_swing_mode_refuses_a_non_daily_decision_timeframe(tmp_path: Path)` (l.49)
- `def test_live_mode_requires_two_deliberate_gestures(tmp_path: Path)` (l.60)
- `def test_live_mode_accepted_only_when_fully_configured(tmp_path: Path)` (l.84)
- `def test_bad_values_fail_loudly(tmp_path: Path)` (l.99)
- `def test_dotenv_file_is_read(tmp_path: Path)` (l.108)
- `def test_binance_key_aliases_are_accepted(tmp_path: Path)` (l.116)
- `def test_explicit_exchange_names_win_over_aliases(tmp_path: Path)` (l.127)
- `def test_secrets_never_appear_in_repr(tmp_path: Path)` (l.141)
- `def test_paper_and_testnet_refuse_a_disabled_sandbox(tmp_path: Path, mode)` (l.159)
- `def test_execution_mode_follows_the_declared_mode(tmp_path: Path)` (l.167)
- `def test_kraken_key_aliases_are_accepted(tmp_path: Path)` (l.181)
- `def test_kraken_does_not_borrow_binance_keys(tmp_path: Path)` (l.195)
- `def test_explicit_exchange_key_still_wins_on_kraken(tmp_path: Path)` (l.210)
- `def test_fee_buffer_default_matches_the_exchange_taker_fee(tmp_path: Path)` (l.222)
- `def test_taker_fee_defaults_to_the_current_kraken_tier(tmp_path: Path)` (l.234)
- `def test_an_absurd_taker_fee_is_refused(tmp_path: Path)` (l.247)
- `def test_quote_currency_is_derived_from_the_symbol(tmp_path: Path)` (l.255)
- `def test_declared_symbols_keep_the_primary_symbol_first(tmp_path: Path)` (l.263)
- `def test_unknown_exchange_is_refused(tmp_path: Path)` (l.275)
- `def test_micro_profile_loads_for_small_accounts(tmp_path: Path)` (l.285)
- `def test_daily_breaker_leaves_room_for_more_than_one_loss(tmp_path: Path, profile: str)` (l.294)
- `def test_invalid_sweep_ratio_is_refused_at_startup(tmp_path: Path)` (l.301)
- `def test_negative_sweep_minimum_is_refused(tmp_path: Path)` (l.306)
- `def test_negative_paper_slippage_is_refused(tmp_path: Path)` (l.314)
- `def test_entry_mode_defaults_to_the_historical_setup(tmp_path: Path)` (l.319)
- `def test_entry_mode_can_be_switched_by_environment(tmp_path: Path)` (l.324)
- `def test_unknown_entry_mode_is_refused_at_startup(tmp_path: Path)` (l.331)
- `def test_swing_exit_defaults_to_ema20(tmp_path: Path)` (l.341)
- `def test_swing_exit_mode_accepts_chandelier_case_insensitively(tmp_path: Path)` (l.348)
- `def test_swing_chandelier_parameters_are_configurable(tmp_path: Path)` (l.356)
- `def test_unknown_swing_exit_mode_is_refused_at_startup(tmp_path: Path)` (l.369)
- `def test_kill_switch_keys_are_a_separate_optional_pair(tmp_path: Path)` (l.377)

### `backend/tests/test_shadow.py`

*Tests du simulateur de shadow trades — purs, sans réseau ni base.*

- `def candle(offset: int, open_: str, high: str, low: str, close: str) -> list` (l.20)
- `def simulate(candles: list[list])` (l.24)
- `def test_stop_loss_is_detected_before_the_take_profit()` (l.36)
- `def test_take_profit_is_detected()` (l.47)
- `def test_end_of_data_closes_at_the_last_close()` (l.58)
- `def test_no_forward_candle_returns_none()` (l.68)
- `def test_a_candle_closing_after_the_decision_is_included()` (l.72)

### `backend/tests/test_sweep.py`

*Tests du Profit Sweeper : calcul pur, seuil, absence de ligne sur perte.*

- `def test_a_loss_produces_no_sweep_row()` (l.12)
- `def test_a_break_even_trade_produces_no_sweep_row()` (l.18)
- `def test_a_gain_above_the_minimum_is_swept()` (l.22)
- `def test_a_gain_below_the_minimum_is_recorded_as_skipped()` (l.29)
- `def test_a_sweep_exactly_at_the_minimum_is_executed()` (l.38)
- `def test_live_plan_is_pending_not_simulated()` (l.44)
- `def test_an_invalid_ratio_is_refused(ratio)` (l.50)
- `def test_deferred_amounts_accumulate_until_the_threshold()` (l.55)
- `def test_a_loss_ignores_deferred_amounts()` (l.62)
- `def test_a_negative_deferred_amount_is_refused()` (l.68)

### `backend/tests/test_swing_backtest.py`

*Tests du rejeu swing Daily : entrée, stop structurel, breakeven, sortie.*

- `def _candle(index: int, open_: float, high: float, low: float, close: float) -> list` (l.25)
- `def d1_series(crash: bool=False) -> list[list]` (l.30) — Tendance longue, consolidation, puis cassure Donchian au jour 340.
- `def market_info() -> KrakenMarketInfo` (l.54)
- `def settings(exit_mode: SwingExitMode=SwingExitMode.EMA20) -> SimpleNamespace` (l.65)
- `def test_swing_enters_on_donchian_breakout_and_exits_in_profit()` (l.82)
- `def test_swing_stop_wins_over_take_profit_in_the_same_candle()` (l.101)
- `def test_swing_breakeven_moves_the_stop_after_one_r()` (l.111)
- `def test_swing_does_not_enter_before_the_requested_start()` (l.125)
- `def test_swing_refuses_a_series_too_short()` (l.136)
- `def chandelier_series() -> list[list]` (l.146) — Après la cassure, une dérive baissière lente : les mèches restent au-dessus
- `def test_swing_chandelier_exits_on_a_close_below_the_trailing_stop()` (l.156)
- `def test_swing_default_mode_does_not_use_the_chandelier()` (l.170)

## 4. Tests

Cas réellement collectés par pytest (les `parametrize` sont comptés à leur juste nombre, pas comme une seule fonction).

| Fichier | Cas collectés |
|---|---|
| `backend/tests/__init__.py` | 0 |
| `backend/tests/test_affordability.py` | 6 |
| `backend/tests/test_api.py` | 20 |
| `backend/tests/test_api_security.py` | 22 |
| `backend/tests/test_backtest.py` | 9 |
| `backend/tests/test_backtest_golden.py` | 3 |
| `backend/tests/test_backtest_metrics.py` | 7 |
| `backend/tests/test_check_exchange.py` | 9 |
| `backend/tests/test_db_config.py` | 10 |
| `backend/tests/test_entry_rule.py` | 35 |
| `backend/tests/test_execution_factory.py` | 3 |
| `backend/tests/test_exit_guard.py` | 8 |
| `backend/tests/test_exit_rule.py` | 10 |
| `backend/tests/test_feed.py` | 12 |
| `backend/tests/test_history.py` | 22 |
| `backend/tests/test_kraken_adapter.py` | 25 |
| `backend/tests/test_kraken_archive.py` | 11 |
| `backend/tests/test_lessons.py` | 11 |
| `backend/tests/test_live_execution.py` | 20 |
| `backend/tests/test_llm.py` | 23 |
| `backend/tests/test_llm_eval.py` | 5 |
| `backend/tests/test_machine.py` | 4 |
| `backend/tests/test_mean_reversion.py` | 4 |
| `backend/tests/test_orchestrator.py` | 26 |
| `backend/tests/test_paper.py` | 9 |
| `backend/tests/test_reconciliation.py` | 15 |
| `backend/tests/test_recovery.py` | 8 |
| `backend/tests/test_risk_gate.py` | 147 |
| `backend/tests/test_rotation.py` | 6 |
| `backend/tests/test_scheduler.py` | 13 |
| `backend/tests/test_schema_contract.py` | 18 |
| `backend/tests/test_settings.py` | 41 |
| `backend/tests/test_shadow.py` | 5 |
| `backend/tests/test_sweep.py` | 13 |
| `backend/tests/test_swing_backtest.py` | 7 |
| **Total** | **587** |

## 5. Schéma SQL

### `backend/db/migrations/0001_init.sql`

- Tables : `llm_signals`, `llm_signals_default`, `risk_evaluations`, `risk_evaluations_default`, `bot_state`, `equity_snapshots`, `positions`, `orders`, `profit_sweeps`, `system_state_logs`
- Vues : `v_dashboard_pnl_metrics`, `v_dashboard_pnl_daily`, `v_risk_rejection_stats`, `v_open_positions`, `v_system_health`
- Type `signal_action` : `BUY`, `SELL`, `HOLD`
- Type `market_regime` : `TRENDING_UP`, `TRENDING_DOWN`, `RANGING`, `VOLATILE`
- Type `execution_mode` : `PAPER`, `TESTNET`, `LIVE`
- Type `risk_rejection_code` : `NONE`, `ACTION_IS_NOT_BUY`, `STALE_MARKET_DATA`, `INVALID_MARKET_DATA`, `MALFORMED_INTENT`, `BOT_LOCKED`, `CIRCUIT_BREAKER_DAILY_DRAWDOWN`, `CIRCUIT_BREAKER_WEEKLY_DRAWDOWN`, `CIRCUIT_BREAKER_MAX_DRAWDOWN`, `CIRCUIT_BREAKER_CONSECUTIVE_LOSSES`, `MAX_ORDERS_PER_HOUR_EXCEEDED`, `CONFIDENCE_TOO_LOW`, `POSITION_ALREADY_OPEN`, `REJECTED_LOW_VOLATILITY`, `REJECTED_EXTREME_VOLATILITY`, `REJECTED_COUNTER_TREND_EMA200`, `REJECTED_REGIME_CONFLICT`, `REJECTED_FALLING_KNIFE`, `INSUFFICIENT_FREE_BALANCE`, `INVALID_STOP_LOSS_CALCULATION`, `SIZING_BELOW_EXCHANGE_MINIMUM`, `EXCHANGE_MIN_NOTIONAL_FAILED`, `RISK_LIMIT_EXCEEDED`
- Type `order_side` : `BUY`, `SELL`
- Type `order_type` : `MARKET`, `LIMIT`, `STOP_LOSS`, `STOP_LOSS_LIMIT`, `STOP_MARKET`, `TAKE_PROFIT`
- Type `order_status` : `PENDING`, `SUBMITTED`, `FILLED`, `PARTIALLY_FILLED`, `CANCELED`, `REJECTED`, `EXPIRED`, `UNKNOWN`
- Type `position_status` : `OPEN`, `CLOSED`, `EMERGENCY_LIQUIDATED`
- Type `system_state` : `IDLE`, `EVALUATING`, `EXECUTING`, `IN_POSITION`, `POST_TRADE_SWEEP`, `COOLDOWN`, `LOCKED_CIRCUIT_BREAKER`, `EMERGENCY_HALT`
- Type `order_purpose` : `ENTRY`, `STOP_LOSS`, `TAKE_PROFIT`, `EMERGENCY_EXIT`, `RECONCILIATION`
- Type `sweep_status` : `PENDING`, `SUCCESS`, `FAILED`, `SKIPPED_BELOW_MINIMUM`, `SIMULATED`

### `backend/db/migrations/0002_lock_public_api.sql`

- Aucun objet créé (droits, index ou contraintes).

### `backend/db/migrations/0003_audit_fixes.sql`

- Vues : `v_system_health`
- `ALTER TYPE risk_rejection_code` ajoute `INTERNAL_ERROR`

### `backend/db/migrations/0004_llm_autonomy.sql`

- `ALTER TYPE signal_action` ajoute `HALT`
- `ALTER TYPE order_purpose` ajoute `LLM_EXIT`

### `backend/db/migrations/0005_runtime_settings.sql`

- Tables : `runtime_settings`

### `backend/db/migrations/0006_shadow_trades.sql`

- Tables : `shadow_trades`, `shadow_trades_default`

### `backend/db/migrations/0007_lessons.sql`

- Tables : `lessons`

### `backend/db/migrations/0008_market_candles_history.sql`

- Tables : `market_candles_history`
- Vues : `v_history_coverage`

### `backend/db/migrations/0009_allow_1d_timeframe.sql`

- Vues : `v_history_coverage`

## 6. Rafraîchir

```bash
python scripts/index_codebase.py          # régénère ce fichier
python scripts/index_codebase.py --check  # échoue si périmé
```
