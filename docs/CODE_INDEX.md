# Index du code — Trading Ninja

> Fichier **généré** par `scripts/index_codebase.py`. Ne pas éditer à la main :
> lancer le script. `bash scripts/check.sh` refuse un index périmé.

Empreinte des sources : `634c2d2ced89089a`

## 1. Vue d'ensemble

| Mesure | Valeur |
|---|---|
| Modules Python | 59 |
| Lignes Python | 9607 |
| Classes | 65 |
| Fonctions et méthodes | 362 |
| Cas de test collectés par pytest | 329 |
| Fonctions de test (comptage statique) | 216 |
| Migrations SQL | 3 |
| Scripts shell | 3 |

## 2. Carte des fichiers

| Fichier | Lignes | Rôle (première ligne de docstring) |
|---|---|---|
| `backend/app/__init__.py` | 1 |  |
| `backend/app/api/__init__.py` | 1 | API d'administration (lecture, pause, panic-sell). |
| `backend/app/api/main.py` | 209 | API d'administration — observation, pause, reprise, panic-sell. |
| `backend/app/api/security.py` | 224 | Authentification de l'API d'administration. |
| `backend/app/api/service.py` | 102 | Vue « administration » : lecture de l'état et actions de contrôle. |
| `backend/app/backtest/__init__.py` | 5 | Rejeu historique et régression des décisions. |
| `backend/app/backtest/engine.py` | 612 | Rejeu historique — le vrai pare-feu, le vrai simulateur, aucune écriture en base. |
| `backend/app/backtest/golden.py` | 198 | Golden de backtest : décisions enregistrées + métriques attendues. |
| `backend/app/core/__init__.py` | 1 |  |
| `backend/app/core/db.py` | 166 | Résolution de la connexion PostgreSQL (Supabase). |
| `backend/app/core/env.py` | 57 | Lecture des fichiers d'environnement, sans dépendance externe. |
| `backend/app/core/settings.py` | 378 | Configuration centralisée — chargée depuis l'environnement, sans dépendance externe. |
| `backend/app/db/__init__.py` | 1 | app/db |
| `backend/app/db/repository.py` | 502 | Persistance PostgreSQL — état, signaux, décisions, positions, ordres. |
| `backend/app/engine/__init__.py` | 1 |  |
| `backend/app/engine/affordability.py` | 109 | Faisabilité d'un ordre avec un petit capital — calcul pur, sans I/O. |
| `backend/app/engine/machine.py` | 68 | Machine à états persistée. |
| `backend/app/engine/risk_gate.py` | 567 | Risk Gateway — pare-feu transactionnel déterministe (fail-closed). |
| `backend/app/engine/sweep.py` | 74 | Profit Sweeper — calcul pur, sans I/O. |
| `backend/app/exchange/__init__.py` | 1 | Package des passerelles exchange. Aucune I/O à l'import. |
| `backend/app/exchange/kraken.py` | 323 | Passerelle Kraken spot — préparation de l'étape 1.6. |
| `backend/app/execution/__init__.py` | 1 | app/execution |
| `backend/app/execution/paper.py` | 86 | Exécution papier — un fill simulé, jamais un ordre réel. |
| `backend/app/llm/__init__.py` | 1 | Client LLM local. Aucune I/O à l'import. |
| `backend/app/llm/client.py` | 376 | Client LLM local (Ollama) — auditeur de confluences, jamais exécutant. |
| `backend/app/main.py` | 124 | Point d'entrée du moteur — mode papier uniquement dans cette version. |
| `backend/app/market/__init__.py` | 1 | Couche marché : flux Kraken et indicateurs. Aucune I/O à l'import. |
| `backend/app/market/feed.py` | 255 | Flux de marché Kraken et indicateurs — bougies **clôturées** uniquement. |
| `backend/app/runtime/__init__.py` | 1 | app/runtime |
| `backend/app/runtime/orchestrator.py` | 411 | Boucle d'exécution : un cycle = une décision, jamais deux. |
| `backend/app/runtime/recovery.py` | 111 | Liquidation d'urgence — le seul chemin qui ferme une position sans pare-feu. |
| `backend/scripts/backtest.py` | 347 | Backtest historique — enregistrement, rejeu de régression, veille de dérive. |
| `backend/scripts/check_exchange.py` | 280 | Sonde **lecture seule** de la clé Binance — étape 1.1 de la feuille de route. |
| `backend/scripts/check_kraken.py` | 286 | Inspection Kraken spot — lecture seule. |
| `backend/scripts/db_migrate.py` | 264 | Application des migrations SQL sur la base cible. |
| `backend/scripts/demo_guardrails.py` | 79 | Démonstration lisible du pare-feu : ce qu'il refuse, et pourquoi. |
| `backend/scripts/hash_password.py` | 35 | Génère un hash de mot de passe administrateur (scrypt). |
| `backend/scripts/kill_switch.py` | 104 | Kill-switch autonome — solde la position et halte le bot, backend arrêté ou non. |
| `backend/scripts/probe_backtest.py` | 92 | Sonde : combien de décisions LLM un backtest coûterait-il sur BTC/EUR ? |
| `backend/scripts/totp_setup.py` | 51 | Génère un secret TOTP, son QR code et l'URI de provisionnement. |
| `backend/tests/__init__.py` | 1 |  |
| `backend/tests/test_affordability.py` | 149 | Tests de la faisabilité d'un ordre avec un petit capital. |
| `backend/tests/test_api.py` | 233 | Tests de l'API d'administration : session, pause, panic-sell, WebSocket. |
| `backend/tests/test_api_security.py` | 105 | Tests de la couche d'authentification : scrypt, JWT, TOTP, anti-rejeu. |
| `backend/tests/test_backtest.py` | 229 | Tests du moteur de backtest : absence de lookahead, stop prioritaire, frais. |
| `backend/tests/test_backtest_golden.py` | 93 | Régression de backtest : le comportement doit rester celui du golden. |
| `backend/tests/test_check_exchange.py` | 90 | Tests du verdict de la sonde exchange — la logique qui protège la clé. |
| `backend/tests/test_db_config.py` | 75 | Tests de la résolution de connexion base (aucun accès réseau). |
| `backend/tests/test_feed.py` | 109 | Tests du flux de marché et des indicateurs, sur bougies synthétiques. |
| `backend/tests/test_kraken_adapter.py` | 280 | Tests de la passerelle Kraken, hors réseau. |
| `backend/tests/test_llm.py` | 173 | Tests du client LLM : parsing strict, repli, garde-fous déterministes. |
| `backend/tests/test_machine.py` | 52 | Tests de la machine à états : le graphe est une contrainte, pas une convention. |
| `backend/tests/test_orchestrator.py` | 400 | Tests de bout en bout de l'orchestrateur, avec des doubles. |
| `backend/tests/test_paper.py` | 78 | Tests de l'exécution papier : frais, slippage, gaps, priorité du stop. |
| `backend/tests/test_recovery.py` | 123 | Tests du kill-switch : liquidation, annulation des ordres, halte forcée. |
| `backend/tests/test_risk_gate.py` | 420 | Tests du pare-feu transactionnel. |
| `backend/tests/test_schema_contract.py` | 148 | Contrat entre le schéma SQL et le code Python. |
| `backend/tests/test_settings.py` | 273 | Tests du chargement de configuration : presets, écrasements, verrous d'armement. |
| `backend/tests/test_sweep.py` | 71 | Tests du Profit Sweeper : calcul pur, seuil, absence de ligne sur perte. |
| `scripts/backtest_watch.sh` | 27 | Veille de backtest — conçue pour un cron silencieux. |
| `scripts/check.sh` | 195 | Vérification complète de l'état du dépôt en une commande. |
| `scripts/manage.sh` | 307 | ============================================================================= |
| `backend/db/migrations/0001_init.sql` | 550 | migration SQL |
| `backend/db/migrations/0002_lock_public_api.sql` | 56 | migration SQL |
| `backend/db/migrations/0003_audit_fixes.sql` | 82 | migration SQL |

## 3. API interne, module par module

Chaque entrée donne la ligne de définition : c'est le point d'entrée pour lire
le code, pas une copie de celui-ci.

### `backend/app/api/main.py`

*API d'administration — observation, pause, reprise, panic-sell.*

- **class LoginRequest(BaseModel)** (l.42)
- **class TotpRequest(BaseModel)** (l.47)
- `async def _default_liquidate(repo: PostgresRepository, config: Settings) -> list` (l.51) — Liquidation réelle de la position papier, au prix public courant.
- `def create_app(settings: Settings | None=None, *, repo: Any | None=None, liquidate: Any | None=None) -> FastAPI` (l.73)

### `backend/app/api/security.py`

*Authentification de l'API d'administration.*

- **class AuthError(RuntimeError)** (l.50) — Échec d'authentification générique.
- **class AuthConfigError(AuthError)** (l.54) — Configuration d'authentification absente ou invalide.
- **class TokenError(AuthError)** (l.58) — Jeton absent, malformé, expiré ou mal signé.
- **class TotpVerifier** (l.142) — Vérifie un code TOTP et refuse sa réutilisation.
  - `def __init__(self, secret: str, *, window: int=1, replay_ttl: int=90)` (l.150)
  - `def verify(self, code: str, *, now: datetime | None=None, consume: bool=True) -> bool` (l.162)
- **class AttemptLimiter** (l.184) — Fenêtre glissante en mémoire, par clé (adresse IP).
  - `def __init__(self, max_attempts: int=5, window_seconds: int=300)` (l.187)
  - `def _recent(self, key: str, epoch: float) -> list[float]` (l.192)
  - `def allow(self, key: str, *, now: float | None=None) -> bool` (l.195)
  - `def fail(self, key: str, *, now: float | None=None) -> None` (l.199)
  - `def reset(self, key: str) -> None` (l.203)
- `def _b64(raw: bytes) -> str` (l.67)
- `def hash_password(password: str, *, salt: bytes | None=None) -> str` (l.71)
- `def verify_password(password: str, stored: str) -> bool` (l.82) — Vrai si le mot de passe correspond. Toute anomalie vaut faux.
- `def create_token(secret: str, *, subject: str='admin', ttl_seconds: int=TOKEN_TTL_SECONDS, now: datetime | None=None) -> str` (l.104)
- `def decode_token(secret: str, token: str) -> dict[str, Any]` (l.121)
- `def require_secrets(jwt_secret: str, totp_secret: str, password_hash: str) -> None` (l.207) — Refuse une configuration incomplète, au démarrage plutôt qu'à l'usage.

### `backend/app/api/service.py`

*Vue « administration » : lecture de l'état et actions de contrôle.*

- **class ControlRefused(RuntimeError)** (l.22) — Commande refusée parce qu'elle laisserait le bot dans un état dangereux.
- `def jsonable(value: Any) -> Any` (l.26)
- `def _position_payload(position) -> dict | None` (l.40)
- `async def build_status(repo: PostgresRepository, settings: Settings) -> dict` (l.55)
- `async def pause(repo: PostgresRepository, settings: Settings) -> dict` (l.86)
- `async def resume(repo: PostgresRepository, settings: Settings) -> dict` (l.100)

### `backend/app/backtest/engine.py`

*Rejeu historique — le vrai pare-feu, le vrai simulateur, aucune écriture en base.*

- **class TradeRecord** (l.65)
- **class Counterfactual** (l.82) — Ce qu'aurait donné un achat forcé là où le modèle a dit HOLD.
- **class BacktestResult** (l.94)
  - `def wins(self) -> list[TradeRecord]` (l.121)
  - `def losses(self) -> list[TradeRecord]` (l.125)
  - `def net_pnl(self) -> Decimal` (l.129)
  - `def win_rate(self) -> Decimal` (l.133)
  - `def return_pct(self) -> Decimal` (l.139)
  - `def median_inference_ms(self) -> int` (l.145)
  - `def mean_inference_ms(self) -> int` (l.149)
  - `def summary(self) -> str` (l.152)
  - `def to_dict(self) -> dict[str, Any]` (l.189)
- **class ReplayExchange** (l.247) — Exchange factice : ne sert que des bougies clôturées à l'instant courant.
  - `def __init__(self, h4: list[list], h1: list[list]) -> None` (l.250)
  - `def set_now(self, milliseconds: int) -> None` (l.255)
  - `def milliseconds(self) -> int` (l.258)
  - `def fetch_ohlcv(self, symbol: str, timeframe: str, since: int | None=None, limit: int | None=None) -> list[list]` (l.261)
- `def _close_time(candle: list) -> datetime` (l.277)
- `def _decimal(value: Any) -> Decimal` (l.281)
- `def _drawdowns(curve: list[tuple[datetime, Decimal]], at: datetime) -> tuple[Decimal, Decimal, Decimal]` (l.285)
- `def zone_is_open(snapshot: MarketSnapshot, context: MarketContext, config: RiskConfig) -> bool` (l.297) — Les conditions où un BUY a seulement le droit d'exister (miroir du client LLM).
- `def _simulate_exit(h4: list[list], entry_index: int, decision: RiskDecision, broker: PaperBroker, max_hold_candles: int=0, stop_index: int | None=None) -> tuple[int, Fill, str]` (l.307) — Cherche la première sortie après l'entrée. Stop prioritaire sur le TP.
- `def _pnl(entry: Fill, exit_fill: Fill) -> Decimal` (l.340)
- `def _max_drawdown(curve: list[tuple[datetime, Decimal]]) -> Decimal` (l.349)
- `async def run_backtest(*, symbol: str, info: Any, h4: list[list], h1: list[list], settings: Any, propose: Callable[[MarketSnapshot, MarketContext], Awaitable[Any]] | None, broker: PaperBroker | None=None, start_index: int | None=None, end_index: int | None=None, max_hold_candles: int=0, mechanical: bool=False, counterfactuals: bool=True, progress: Callable[[int, int], None] | None=None) -> BacktestResult` (l.359) — Rejoue une fenêtre et rend les métriques. Ne touche ni au réseau ni à la base.

### `backend/app/backtest/golden.py`

*Golden de backtest : décisions enregistrées + métriques attendues.*

- **class GoldenBundle** (l.47)
  - `def to_dict(self) -> dict[str, Any]` (l.62)
- **class RecordedSignal** (l.146) — Rend une décision enregistrée avec l'interface d'un `LLMSignal`.
  - `def __init__(self, row: dict[str, Any]) -> None` (l.149)
- **class ReplayLLM** (l.160) — Sert les décisions du golden. Échoue si une décision manque.
  - `def __init__(self, decisions: list[dict[str, Any]]) -> None` (l.167)
  - `async def __call__(self, snapshot, context) -> RecordedSignal` (l.170)
- `def save_bundle(bundle: GoldenBundle, path: str | Path) -> None` (l.66)
- `def load_bundle(path: str | Path) -> GoldenBundle` (l.75)
- `def prompt_fingerprint(settings: Any) -> str` (l.84) — Identité du couple prompt + modèle. Un changement invalide les décisions.
- `def risk_fingerprint(settings: Any, info: Any) -> str` (l.96) — Identité du risque, des frais et du capital de départ. Change les résultats.
- `def metrics_of(result: BacktestResult) -> dict[str, str]` (l.109) — Métriques comparables, en texte : aucun flottant, aucune tolérance.
- `def diff_metrics(expected: dict[str, str], actual: dict[str, str]) -> list[str]` (l.138)
- `def market_from_bundle(bundle: GoldenBundle) -> KrakenMarketInfo` (l.181) — Reconstruit les contraintes figées, sans réseau.

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

- **class ExchangeSettings** (l.161)
- **class LLMSettings** (l.174)
- **class Settings** (l.185)
  - `def is_live(self) -> bool` (l.204)
  - `def is_sandbox(self) -> bool` (l.208)
  - `def execution_mode(self) -> str` (l.212) — Valeur écrite dans les colonnes `mode` de la base.
  - `def database(self) -> DatabaseConfig` (l.224) — Résolution paresseuse : la base n'est exigée que si on s'en sert.
- `def _env(env: Mapping[str, str], key: str, default: str | None=None) -> str` (l.126)
- `def _env_dec(env: Mapping[str, str], key: str, default: str) -> Decimal` (l.134)
- `def _env_int(env: Mapping[str, str], key: str, default: str) -> int` (l.138)
- `def _env_bool(env: Mapping[str, str], key: str, default: str='false') -> bool` (l.142)
- `def _first(env: Mapping[str, str], *names: str, default: str='') -> str` (l.146) — Première variable non vide parmi plusieurs alias acceptés.
- `def load_settings(env_file: str | Path | None=None, overrides: Mapping[str, str] | None=None) -> Settings` (l.234) — Charge la configuration. Lève RuntimeError si incohérente.

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
  - `async def insert_evaluation(self, mode: str, symbol: str, *, signal_id: uuid.UUID, signal_created_at: datetime, decision: Any, snapshot: Any, account: AccountSnapshot, evaluation_time_ms: int, decision_timeframe: str) -> tuple[uuid.UUID, datetime]` (l.244)
  - `async def open_position(self, mode: str) -> PositionRow | None` (l.278)
  - `async def insert_order(self, mode: str, *, client_order_id: str, symbol: str, side: str, order_type: str, purpose: str, status: str, requested_amount: Decimal, filled_amount: Decimal, requested_price: Decimal | None, stop_price: Decimal | None, average_fill_price: Decimal, fee_amount: Decimal, fee_currency: str, slippage_quote: Decimal | None, position_id: uuid.UUID | None=None, risk_evaluation_id: uuid.UUID | None=None, error_message: str | None=None) -> uuid.UUID` (l.295)
  - `async def create_position(self, mode: str, *, symbol: str, entry_price: Decimal, amount: Decimal, notional_quote: Decimal, stop_loss_price: Decimal, take_profit_price: Decimal | None, risk_amount_quote: Decimal, r_multiple_at_tp: Decimal | None, signal_id: uuid.UUID, evaluation_id: uuid.UUID, protective_order_id: str) -> uuid.UUID` (l.318)
  - `async def close_position(self, position_id: uuid.UUID, *, exit_price: Decimal, closed_amount: Decimal, gross_pnl_quote: Decimal, total_fees_quote: Decimal, net_pnl_quote: Decimal, exit_reason: str, status: str='CLOSED') -> None` (l.336)
  - `async def insert_sweep(self, mode: str, *, position_id: uuid.UUID, quote_currency: str, realized_net_pnl: Decimal, sweep_ratio: Decimal, computed_sweep_quote: Decimal, swept_amount: Decimal, retained_trading_capital: Decimal, status: str, next_retry_at: datetime | None=None, error_message: str | None=None) -> uuid.UUID` (l.349)
  - `async def deferred_sweep_quote(self, mode: str) -> Decimal` (l.367) — Somme des écréma­ges reportés depuis le dernier écrémage exécuté.
  - `async def open_orders(self, mode: str) -> list[dict]` (l.387)
  - `async def update_order_status(self, order_id: uuid.UUID, status: str, error_message: str | None=None) -> None` (l.397)
  - `async def force_lock(self, mode: str, reason: str) -> None` (l.405) — Pause administrative : le bot cesse d'évaluer jusqu'à reprise explicite.
  - `async def force_resume(self, mode: str, reason: str, who: str='admin') -> None` (l.425) — Reprise humaine : uniquement depuis un état bloqué, jamais depuis IDLE.
  - `async def force_halt(self, mode: str, reason: str, code: str | None=None) -> None` (l.455) — Arrêt d'urgence déclenché par un humain, depuis n'importe quel état.
  - `async def update_after_close(self, mode: str, *, consecutive_losses: int, equity: Decimal) -> None` (l.481)
  - `async def recent(self, table: str, mode: str, limit: int=5) -> list[dict]` (l.493)
- `def _drawdown(peak: Decimal, equity: Decimal) -> Decimal` (l.68)

### `backend/app/engine/affordability.py`

*Faisabilité d'un ordre avec un petit capital — calcul pur, sans I/O.*

- **class Affordability** (l.34) — Verdict chiffré pour une paire, à une équité donnée.
  - `def shortfall(self) -> Decimal` (l.47) — Ce qui manque pour que le plus petit ordre tienne. Zéro si c'est bon.
- `def assess(symbol: str, *, price: Decimal, atr_14: Decimal, constraints: ExchangeConstraints, config: RiskConfig, equity: Decimal, risk_pct: Decimal | None=None) -> Affordability` (l.54) — Calcule le minimum d'ordre et l'équité qu'il exige.

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

- **class Action(StrEnum)** (l.48)
- **class MarketRegime(StrEnum)** (l.54)
- **class RejectionCode(StrEnum)** (l.61)
- **class TradeIntent** (l.102) — Ce que le LLM a répondu. Non fiable par construction.
- **class MarketSnapshot** (l.112) — État de marché au moment de l'évaluation.
- **class AccountState** (l.130) — Photographie du compte. La réserve (funding) est exclue par construction.
- **class ExchangeConstraints** (l.146) — Lues dynamiquement via CCXT `market[...]`. Jamais codées en dur.
- **class RiskConfig** (l.161) — Réglages du pare-feu.
  - `def __post_init__(self) -> None` (l.200)
  - `def effective_risk_pct(self, consecutive_losses: int) -> Decimal` (l.221) — Risque unitaire après étranglement progressif par série de pertes.
- **class RiskDecision** (l.238)
  - `def ok(self) -> bool` (l.256)
- `def _reject(code: RejectionCode, details: str, passed: list[str]) -> RiskDecision` (l.260)
- `def _floor_to(value: Decimal, step: Decimal) -> Decimal` (l.269) — Tronque vers le bas au pas autorisé par l'exchange (jamais d'arrondi au-dessus).
- `def _as_decimal(value: float | Decimal | int) -> Decimal` (l.276)
- `def _is_finite(value: Decimal) -> bool` (l.280) — `Decimal('NaN')` et `Decimal('Infinity')` sont des valeurs valides pour
- `def evaluate(intent: TradeIntent, market: MarketSnapshot, account: AccountState, constraints: ExchangeConstraints, config: RiskConfig=DEFAULT_CONFIG) -> RiskDecision` (l.291) — Point d'entrée public. **Ne lève jamais.**
- `def _evaluate(intent: TradeIntent, market: MarketSnapshot, account: AccountState, constraints: ExchangeConstraints, config: RiskConfig=DEFAULT_CONFIG) -> RiskDecision` (l.315) — Filtre une intention de trade. Entonnoir strictement ordonné, fail-closed.

### `backend/app/engine/sweep.py`

*Profit Sweeper — calcul pur, sans I/O.*

- **class SweepPlan** (l.31)
- `def plan_sweep(net_pnl_quote: Decimal, ratio: Decimal, minimum_quote: Decimal, *, deferred_quote: Decimal=Decimal(0), simulated: bool=True) -> SweepPlan` (l.39) — Calcule l'écrémage, reports antérieurs compris.

### `backend/app/exchange/kraken.py`

*Passerelle Kraken spot — préparation de l'étape 1.6.*

- **class KrakenAdapterError(RuntimeError)** (l.49) — Toute anomalie de la passerelle qui doit arrêter le bot, pas être ignorée.
- **class KeyPermissionError(KrakenAdapterError)** (l.53) — La clé n'a pas les droits requis, ou ses droits n'ont pas pu être vérifiés.
- **class OrderPlanError(KrakenAdapterError)** (l.57) — La décision approuvée ne se traduit pas en ordre sans casser le budget.
- **class KeyAudit** (l.106) — Ce que Kraken dit de la clé utilisée, au-delà des seules permissions.
- **class KrakenMarketInfo** (l.179) — Ce que le pare-feu a besoin de savoir d'un marché, en `Decimal`.
- `def _tokens(permission: str) -> frozenset[str]` (l.87) — Découpe un libellé en jetons comparables.
- `def verify_key_permissions(exchange: Any) -> KeyAudit` (l.114) — Lit `GetApiKeyInfo` et refuse tout ce qui n'est pas explicitement autorisé.
- `def build_exchange(*, api_key: str='', secret: str='', enable_rate_limit: bool=True) -> Any` (l.192) — Instancie CCXT pour Kraken.
- `def _decimal(value: Any) -> Decimal` (l.209)
- `def market_info(exchange: Any, symbol: str) -> KrakenMarketInfo` (l.213) — Traduit un marché CCXT en contraintes utilisables par `evaluate()`.
- `def to_exchange_amount(exchange: Any, symbol: str, amount: Decimal) -> str` (l.241) — Formate une quantité au pas de l'exchange, sans jamais dépasser l'entrée.
- `def to_exchange_price(exchange: Any, symbol: str, price: Decimal) -> str` (l.257) — Formate un prix au tick de l'exchange. Utilisé pour l'entrée ou le TP.
- `def to_exchange_stop_price(exchange: Any, symbol: str, stop_price: Decimal) -> str` (l.266) — Formate le stop sans jamais le rapprocher de l'entrée.
- `def bracket_params(stop_price: str) -> dict[str, Any]` (l.287) — Paramètre `close` de Kraken : stop-loss au marché attaché à l'ordre d'entrée.
- `def build_bracket_entry(exchange: Any, decision: RiskDecision, symbol: str) -> dict[str, Any]` (l.298) — Arguments prêts pour `exchange.create_order(**args)`.

### `backend/app/execution/paper.py`

*Exécution papier — un fill simulé, jamais un ordre réel.*

- **class Fill** (l.26)
- **class PaperBroker** (l.34)
  - `def __init__(self, taker_fee: Decimal, slippage_bps: int=5)` (l.35)
  - `def fee(self, notional_quote: Decimal) -> Decimal` (l.43) — Frais taker d'un notionnel donné, pour reconstruire un coût d'entrée.
  - `def entry(self, decision: RiskDecision, market_price: Decimal) -> Fill` (l.47) — Achat au marché simulé. La quantité vient de la décision, jamais d'ici.
  - `def exit(self, amount: Decimal, market_price: Decimal) -> Fill` (l.61)
  - `def exit_trigger(stop_loss_price: Decimal, take_profit_price: Decimal | None, candle: list) -> tuple[Decimal, str] | None` (l.73) — Rend (prix de sortie, raison) si la bougie clôturée a déclenché une sortie.

### `backend/app/llm/client.py`

*Client LLM local (Ollama) — auditeur de confluences, jamais exécutant.*

- **class LLMError(RuntimeError)** (l.94) — Le modèle n'a pas produit d'intention exploitable.
- **class ParsedSignal** (l.99)
- **class LLMSignal** (l.109)
- **class SignalClient** (l.173) — Appelle Ollama et rend une intention validée, ou un HOLD de repli.
  - `def __init__(self, settings: LLMSettings, *, transport: httpx.AsyncBaseTransport | None=None)` (l.176)
  - `def _payload(self, symbol: str, snapshot: MarketSnapshot, context: MarketContext, has_position: bool, trend_buffer_pct: Decimal) -> tuple[dict, dict]` (l.182)
  - `def _root(self) -> str` (l.227)
  - `async def _post(self, payload: dict, timeout: float) -> dict` (l.231)
  - `def _fallback(self, snapshot: MarketSnapshot, reason: str) -> LLMSignal` (l.248)
  - `async def propose(self, symbol: str, snapshot: MarketSnapshot, context: MarketContext, *, has_position: bool=False, trend_buffer_pct: Decimal=Decimal('0.005')) -> LLMSignal` (l.270)
  - `def _enforce_rules(self, signal: LLMSignal, snapshot: MarketSnapshot, trend_buffer_pct: Decimal) -> LLMSignal` (l.323) — Rend inopérant un BUY que les règles interdisent.
  - `async def warmup(self, timeout_seconds: float=180.0) -> bool` (l.360) — Charge le modèle en mémoire avant la première décision.
- `def _extract_json(content: str) -> dict` (l.124) — Récupère le premier objet JSON, même entouré de texte ou de balises.
- `def _confidence(value: Any) -> Decimal` (l.139)
- `def parse_signal(content: str) -> ParsedSignal` (l.149) — Valide la réponse du modèle. Ne lit que les cinq champs autorisés.

### `backend/app/main.py`

*Point d'entrée du moteur — mode papier uniquement dans cette version.*

- `async def _status(repo: PostgresRepository, mode: str) -> None` (l.29)
- `def _taker_fee(exchange, symbols: tuple[str, ...]) -> Decimal` (l.57)
- `async def _run(args: argparse.Namespace) -> int` (l.62)
- `def main(argv: list[str] | None=None) -> int` (l.106)

### `backend/app/market/feed.py`

*Flux de marché Kraken et indicateurs — bougies **clôturées** uniquement.*

- **class FeedError(RuntimeError)** (l.50) — Historique insuffisant ou données inexploitables.
- **class MarketContext** (l.55) — Contexte qualitatif transmis au modèle. Aucun de ces champs ne dimensionne.
- **class SymbolState** (l.66) — Instantané complet d'une paire au moment T.
- `def _decimal(value: Any) -> Decimal` (l.76)
- `def closed_candles(exchange: Any, symbol: str, timeframe: str, limit: int, now_ms: int | None=None) -> list[list]` (l.80) — Bougies dont la fenêtre est terminée, la plus ancienne d'abord.
- `def ema(values: list, period: int=EMA_PERIOD) -> Decimal` (l.91) — EMA classique, alpha = 2/(n+1). `values` du plus ancien au plus récent.
- `def atr(candles: list[list], period: int=ATR_PERIOD) -> Decimal` (l.104) — ATR simple sur les `period` dernières variations vraies.
- `def rsi(candles: list[list], period: int=RSI_PERIOD) -> Decimal` (l.117) — RSI de Wilder sur les clôtures. 100 si aucune perte sur la fenêtre.
- `def _ema_series(values: list[Decimal], period: int) -> list[Decimal]` (l.136)
- `def macd_histogram(candles: list[list], fast: int=12, slow: int=26, signal: int=9) -> Decimal` (l.144) — Histogramme MACD = MACD - signal(EMA9 du MACD).
- `def order_book_imbalance(exchange: Any, symbol: str, depth: int=20) -> Decimal` (l.161) — (volume acheteur - volume vendeur) / total, sur les `depth` premiers niveaux.
- `def _close_time(candle: list, timeframe: str) -> datetime` (l.176)
- `def build_snapshot(exchange: Any, symbol: str, *, decision_timeframe: str='4h', entry_timeframe: str='1h', now: datetime | None=None, info: KrakenMarketInfo | None=None, with_order_book: bool=True) -> SymbolState` (l.180) — Construit l'instantané que consomment le pare-feu et le modèle.
- `def last_price(exchange: Any, symbol: str) -> Decimal` (l.249) — Prix courant, pour la vérification de fraîcheur juste avant `evaluate()`.

### `backend/app/runtime/orchestrator.py`

*Boucle d'exécution : un cycle = une décision, jamais deux.*

- **class CycleReport** (l.50)
  - `def lines(self) -> list[str]` (l.64)
- **class Orchestrator** (l.79)
  - `def __init__(self, settings: Settings, repo: PostgresRepository, exchange: Any, signal_client: SignalClient, broker: PaperBroker, *, now: Any=None)` (l.80)
  - `def _account_state(self, account: AccountSnapshot, open_symbols: tuple[str, ...]=()) -> AccountState` (l.93)
  - `def _client_id(self, symbol: str, purpose: str) -> str` (l.105)
  - `async def _starting_equity(self) -> Decimal` (l.108)
  - `async def run_once(self) -> CycleReport` (l.124)
  - `async def _monitor(self, machine: StateMachine, position: PositionRow, account: AccountSnapshot, report: CycleReport) -> None` (l.165)
  - `async def _evaluate_cycle(self, machine: StateMachine, account: AccountSnapshot, report: CycleReport) -> None` (l.272)
  - `async def _enter(self, machine: StateMachine, state: SymbolState, decision: Any, signal_id: uuid.UUID, evaluation_id: uuid.UUID, account: AccountSnapshot, report: CycleReport) -> None` (l.354)
- `def position_initial_notional(position: PositionRow) -> Decimal` (l.409) — Notionnel initial d'une position relue depuis la base.

### `backend/app/runtime/recovery.py`

*Liquidation d'urgence — le seul chemin qui ferme une position sans pare-feu.*

- **class Liquidation** (l.30)
- `async def liquidate_open_positions(repo: PostgresRepository, exchange: Any, broker: PaperBroker, mode: str, *, quote_currency: str, starting_equity: Decimal, reason: str=EMERGENCY_REASON, now: datetime | None=None) -> list[Liquidation]` (l.39) — Solde toutes les positions ouvertes, annule leurs ordres, puis halte le bot.

### `backend/scripts/backtest.py`

*Backtest historique — enregistrement, rejeu de régression, veille de dérive.*

- `def fetch_recent(exchange, symbol: str, timeframe: str) -> list[list]` (l.48) — Les 720 bougies les plus récentes, clôturées uniquement.
- `def first_index(h4: list[list], h1: list[list]) -> int` (l.61) — Premier indice où l'EMA200 et les 120 H1 nécessaires existent.
- `def with_overrides(settings, args)` (l.72) — Scénario de backtest : ne modifie jamais `.env`, seulement cette exécution.
- `async def record(args) -> int` (l.82)
- `async def replay(args) -> int` (l.165)
- `def _snapshot_at(bundle: GoldenBundle, info, index: int)` (l.208)
- `async def watch(args) -> int` (l.220)
- `def main() -> int` (l.327)

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

- `def _closed_candles(exchange, symbol: str, timeframe: str='4h', limit: int=80) -> list[list]` (l.50) — Bougies **clôturées** uniquement.
- `def _atr_14(candles: list[list]) -> Decimal` (l.63)
- `def _print_affordability(exchange, symbols, equity: Decimal, risk_config, quote: str) -> int` (l.74)
- `def _print_scan(exchange, equity: Decimal, risk_config, quote: str, top: int, min_volume: Decimal=Decimal('100000')) -> int` (l.106) — Cherche dans tout l'univers les paires réellement tradables avec cette équité.
- `def _credentials(env: dict[str, str]) -> tuple[str, str]` (l.171)
- `def _resolve_equity(exchange, quote: str, api_key: str, secret: str, explicit: str | None)` (l.179) — Équité à utiliser : explicite, sinon lue en lecture seule sur le compte.
- `def main() -> int` (l.192)

### `backend/scripts/db_migrate.py`

*Application des migrations SQL sur la base cible.*

- `def discover() -> list[Path]` (l.42)
- `def checksum(path: Path) -> str` (l.46)
- `async def connect(cfg: DatabaseConfig) -> asyncpg.Connection` (l.50)
- `async def applied_versions(conn: asyncpg.Connection) -> dict[str, str]` (l.54)
- `async def cmd_status(conn: asyncpg.Connection) -> int` (l.62)
- `async def cmd_apply(conn: asyncpg.Connection) -> int` (l.90)
- `async def cmd_verify(conn: asyncpg.Connection) -> int` (l.115) — Contrôle structurel + test d'écriture annulé (rien ne reste en base).
- `async def main_async(argv: list[str]) -> int` (l.228)
- `def main() -> int` (l.259)

### `backend/scripts/demo_guardrails.py`

*Démonstration lisible du pare-feu : ce qu'il refuse, et pourquoi.*

- `def show(label, *, market=None, account=None, intent=None)` (l.33)

### `backend/scripts/hash_password.py`

*Génère un hash de mot de passe administrateur (scrypt).*

- `def main() -> int` (l.21)

### `backend/scripts/kill_switch.py`

*Kill-switch autonome — solde la position et halte le bot, backend arrêté ou non.*

- `async def _run(dry_run: bool) -> int` (l.36)
- `def main() -> int` (l.92)

### `backend/scripts/probe_backtest.py`

*Sonde : combien de décisions LLM un backtest coûterait-il sur BTC/EUR ?*

- `def fetch_all(exchange, timeframe: str, days: int) -> list[list]` (l.28)
- `def main() -> None` (l.49)

### `backend/scripts/totp_setup.py`

*Génère un secret TOTP, son QR code et l'URI de provisionnement.*

- `def main() -> int` (l.25)

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

- **class FakeRepo** (l.24)
  - `def __init__(self) -> None` (l.25)
  - `async def state(self, mode)` (l.31)
  - `async def account(self, mode, starting_equity)` (l.34)
  - `async def open_position(self, mode)` (l.42)
  - `async def recent(self, table, mode, limit)` (l.45)
  - `async def force_lock(self, mode, reason)` (l.50)
  - `async def force_resume(self, mode, reason, who='admin')` (l.54)
- `def api(tmp_path)` (l.62)
- `def login(client, password: str=PASSWORD, code: str | None=None)` (l.87)
- `def token_of(client) -> str` (l.94)
- `def test_login_requires_both_factors(api)` (l.105)
- `def test_login_returns_a_fifteen_minute_token(api)` (l.112)
- `def test_status_requires_a_valid_session(api)` (l.120)
- `def test_status_describes_the_bot(api)` (l.129)
- `def test_too_many_failed_logins_are_blocked(api)` (l.140)
- `def test_pause_then_resume(api)` (l.152)
- `def test_pause_is_refused_with_an_open_position(api)` (l.167)
- `def test_resume_is_refused_when_the_bot_is_not_stopped(api)` (l.181)
- `def test_panic_requires_a_fresh_totp(api)` (l.188)
- `def test_history_rejects_unknown_tables(api)` (l.206)
- `def test_websocket_refuses_a_bad_token(api)` (l.218)
- `def test_websocket_streams_the_status(api)` (l.226)

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

- **class StubSignal** (l.83)
  - `def __init__(self, action='HOLD', confidence='0.9', regime='TRENDING_UP', thesis='stub', degraded=False, reason='')` (l.84)
- **class StubClient** (l.96) — Répond BUY à un index donné, HOLD ailleurs. Enregistre ce qu'il a vu.
  - `def __init__(self, buy_at: datetime | None=None, price_spy: list | None=None)` (l.99)
  - `async def __call__(self, snapshot, context)` (l.104)
- `def candles(start: datetime, count: int, step_ms: int) -> list[list]` (l.25) — Série qui monte, avec une oscillation pour éviter un RSI à 100.
- `def h1_series(count: int=2000) -> list[list]` (l.39)
- `def market_info() -> KrakenMarketInfo` (l.52)
- `def settings() -> SimpleNamespace` (l.70)
- `def close_time(candle: list) -> datetime` (l.112)
- `async def test_no_lookahead_the_snapshot_stops_at_the_decision_candle()` (l.117)
- `async def test_take_profit_is_reached_and_fees_are_paid()` (l.138)
- `async def test_stop_wins_when_a_candle_touches_both_levels()` (l.163)
- `async def test_hold_does_not_open_a_position_but_feeds_the_counterfactual()` (l.183)
- `async def test_zone_rejects_below_ema200()` (l.198)
- `def test_zone_is_open_matches_the_client_rules()` (l.214)
- `def test_replay_exchange_only_serves_closed_candles()` (l.223)

### `backend/tests/test_backtest_golden.py`

*Régression de backtest : le comportement doit rester celui du golden.*

- `def _bundle_and_settings()` (l.49)
- `def test_le_prompt_et_le_modele_sont_ceux_du_golden()` (l.55)
- `def test_le_profil_de_risque_est_celui_du_golden()` (l.63)
- `async def test_le_rejeu_produit_les_metriques_attendues()` (l.74)

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

### `backend/tests/test_feed.py`

*Tests du flux de marché et des indicateurs, sur bougies synthétiques.*

- **class FakeExchange** (l.27)
  - `def __init__(self, h4: list[list], h1: list[list], now_ms: int, book: dict | None=None)` (l.28)
  - `def fetch_ohlcv(self, symbol, timeframe, limit=None)` (l.32)
  - `def milliseconds(self)` (l.35)
  - `def fetch_order_book(self, symbol, limit=20)` (l.38)
- `def candles(n: int, start: float=100.0, step: float=0.1, spread: float=0.5, t0: int=1700000000000) -> list[list]` (l.18)
- `def test_ema_rises_in_an_uptrend()` (l.42)
- `def test_ema_refuses_short_history()` (l.48)
- `def test_atr_matches_a_constant_range()` (l.53)
- `def test_rsi_is_maximal_on_a_pure_uptrend()` (l.59)
- `def test_rsi_is_minimal_on_a_pure_downtrend()` (l.63)
- `def test_macd_is_positive_in_an_uptrend()` (l.68)
- `def test_closed_candles_drop_the_unfinished_one()` (l.72)
- `def test_build_snapshot_uses_only_closed_candles()` (l.82)
- `def test_build_snapshot_refuses_insufficient_history()` (l.100)

### `backend/tests/test_kraken_adapter.py`

*Tests de la passerelle Kraken, hors réseau.*

- **class FakeKraken** (l.47) — Surface minimale de CCXT utilisée par l'adaptateur, sans réseau.
  - `def __init__(self, market: dict | None=None, permissions: list[str] | None=None, boom: Exception | None=None, extra: dict | None=None)` (l.50)
  - `def market(self, symbol: str) -> dict` (l.59)
  - `def amount_to_precision(self, symbol: str, amount) -> str` (l.64)
  - `def price_to_precision(self, symbol: str, price) -> str` (l.69)
  - `def privatePostGetApiKeyInfo(self) -> dict` (l.75)
  - `def create_order(self, **kwargs)` (l.81)
- `def decision(**kwargs) -> RiskDecision` (l.95)
- `def test_market_info_maps_limits_to_decimal_constraints()` (l.110)
- `def test_eur_pair_is_spot_and_active()` (l.121)
- `def test_non_spot_market_is_refused()` (l.127)
- `def test_amount_is_never_rounded_above_the_validated_quantity()` (l.138)
- `def test_float_amount_is_refused()` (l.145)
- `def test_stop_rounded_below_the_validated_price_is_refused()` (l.150)
- `def test_stop_rounded_up_is_accepted()` (l.156)
- `def test_bracket_uses_the_close_stop_loss_parameter()` (l.165)
- `def test_bracket_entry_shape_is_kraken_compatible()` (l.170)
- `def test_unapproved_decision_cannot_produce_an_order()` (l.183)
- `def test_approved_decision_without_stop_is_refused()` (l.189)
- `def test_healthy_key_passes()` (l.199)
- `def test_ui_style_labels_are_normalised()` (l.204)
- `def test_ip_allowlist_is_read_in_snake_case()` (l.219)
- `def test_ip_allowlist_is_read_in_camel_case_too()` (l.228)
- `def test_withdraw_permission_is_fatal()` (l.235)
- `def test_withdraw_label_in_ui_style_is_also_caught()` (l.240)
- `def test_missing_cancel_permission_is_fatal()` (l.247)
- `def test_old_cancel_label_is_still_accepted()` (l.253)
- `def test_unreadable_permissions_fail_closed()` (l.260)
- `def test_empty_permissions_fail_closed()` (l.265)
- `def test_build_exchange_sets_the_two_non_negotiable_options()` (l.275)

### `backend/tests/test_llm.py`

*Tests du client LLM : parsing strict, repli, garde-fous déterministes.*

- `def settings(**kwargs) -> LLMSettings` (l.33)
- `def client(content: str=VALID, status: int=200) -> SignalClient` (l.39)
- `def test_valid_response_is_parsed()` (l.55)
- `def test_json_wrapped_in_prose_is_recovered()` (l.63)
- `def test_invalid_responses_are_refused(payload)` (l.75)
- `def test_numeric_fields_from_the_model_are_ignored()` (l.80)
- `def test_thesis_is_truncated_to_200_characters()` (l.88)
- `async def test_propose_returns_a_signal_and_hides_account_data()` (l.100)
- `async def test_timeout_falls_back_to_hold_with_zero_confidence()` (l.113)
- `async def test_http_error_falls_back_to_hold()` (l.126)
- `async def test_garbage_response_falls_back_to_hold()` (l.133)
- `async def test_buy_below_ema200_is_downgraded_even_if_the_model_insists()` (l.145)
- `async def test_buy_with_rsi_above_70_is_downgraded()` (l.158)
- `async def test_sell_and_hold_are_not_downgraded()` (l.169)

### `backend/tests/test_machine.py`

*Tests de la machine à états : le graphe est une contrainte, pas une convention.*

- **class FakeRepo** (l.13)
  - `def __init__(self) -> None` (l.14)
  - `async def transition(self, mode, expected, target, reason, code=None, context=None)` (l.17)
- `async def test_expected_transition_is_written()` (l.22)
- `async def test_illegal_transition_is_refused_before_writing()` (l.31)
- `async def test_locked_and_halted_states_have_no_automatic_exit()` (l.41)
- `def test_unknown_state_is_refused_at_construction()` (l.50)

### `backend/tests/test_orchestrator.py`

*Tests de bout en bout de l'orchestrateur, avec des doubles.*

- **class FakeRepo** (l.58)
  - `def __init__(self, state: str=IDLE, position: PositionRow | None=None, free_quote: str='9.37')` (l.59)
  - `async def ensure_mode(self, mode)` (l.80)
  - `async def state(self, mode)` (l.82)
  - `async def transition(self, mode, expected, target, reason, code=None, context=None)` (l.84)
  - `async def account(self, mode, starting)` (l.90)
  - `async def open_position(self, mode)` (l.92)
  - `async def snapshot_equity(self, mode, **kwargs)` (l.94)
  - `async def insert_signal(self, mode, symbol, model_name, **kwargs)` (l.96)
  - `async def insert_evaluation(self, mode, symbol, **kwargs)` (l.100)
  - `async def create_position(self, mode, **kwargs)` (l.104)
  - `async def insert_order(self, mode, **kwargs)` (l.113)
  - `async def close_position(self, position_id, **kwargs)` (l.117)
  - `async def update_after_close(self, mode, **kwargs)` (l.121)
  - `async def insert_sweep(self, mode, **kwargs)` (l.123)
  - `async def deferred_sweep_quote(self, mode)` (l.127)
  - `async def set_cooldown(self, mode, until)` (l.130)
- **class FakeLLM** (l.133)
  - `def __init__(self, signal: LLMSignal)` (l.134)
  - `async def propose(self, symbol, snapshot, context, **kwargs)` (l.138)
- `def symbol_state(price: str='100', ema: str='90') -> SymbolState` (l.45)
- `def llm_signal(action: str='BUY', confidence: str='0.9', *, degraded: bool=False, reason: str | None=None) -> LLMSignal` (l.143)
- `def settings(tmp_path: Path)` (l.153)
- `def orchestrator(tmp_path: Path, repo: FakeRepo, llm: FakeLLM) -> Orchestrator` (l.165)
- `def _patch_feed(monkeypatch)` (l.174)
- `async def test_approved_intent_opens_a_paper_position(tmp_path)` (l.182)
- `async def test_hold_never_reaches_the_risk_gate(tmp_path)` (l.196)
- `async def test_degraded_llm_produces_no_evaluation(tmp_path)` (l.207)
- `async def test_counter_trend_buy_is_refused_by_the_gate(tmp_path, monkeypatch)` (l.218)
- `async def test_cooldown_is_resumed_after_a_restart(tmp_path)` (l.235)
- `async def test_expired_cooldown_returns_to_idle(tmp_path)` (l.248)
- `async def test_open_position_is_recovered_after_a_restart(tmp_path, monkeypatch)` (l.258)
- `def test_drawdown_maths_are_exact()` (l.278)
- `async def test_candle_hitting_the_stop_closes_the_position_and_cools_down(tmp_path, monkeypatch)` (l.288)
- `async def test_winning_trade_sweeps_thirty_percent(tmp_path, monkeypatch)` (l.314)
- `async def test_small_win_is_recorded_as_skipped(tmp_path, monkeypatch)` (l.351)
- `async def test_deferred_sweeps_accumulate_until_the_threshold(tmp_path, monkeypatch)` (l.374)

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

### `backend/tests/test_recovery.py`

*Tests du kill-switch : liquidation, annulation des ordres, halte forcée.*

- **class FakeRepo** (l.27)
  - `def __init__(self, pos: PositionRow | None)` (l.28)
  - `async def account(self, mode, starting)` (l.42)
  - `async def open_position(self, mode)` (l.50)
  - `async def open_orders(self, mode)` (l.54)
  - `async def update_order_status(self, order_id, status, error_message=None)` (l.56)
  - `async def insert_order(self, mode, **kwargs)` (l.59)
  - `async def close_position(self, position_id, **kwargs)` (l.63)
  - `async def update_after_close(self, mode, **kwargs)` (l.66)
  - `async def snapshot_equity(self, mode, **kwargs)` (l.69)
  - `async def force_halt(self, mode, reason, code=None)` (l.72)
- `def position(symbol: str='BTC/EUR') -> PositionRow` (l.19)
- `def _price(monkeypatch)` (l.77)
- `async def test_a_position_is_liquidated_and_the_bot_halted()` (l.82)
- `async def test_the_kill_switch_halts_even_without_a_position()` (l.104)
- `async def test_emergency_exit_pays_fees_and_slippage()` (l.116)

### `backend/tests/test_risk_gate.py`

*Tests du pare-feu transactionnel.*

- `def market(price='100', ema='90', atr='2', h1='-0.2', age_s=0.0) -> MarketSnapshot` (l.38)
- `def account(**kw) -> AccountState` (l.50)
- `def intent(action=Action.BUY, confidence=0.9, regime=MarketRegime.TRENDING_UP) -> TradeIntent` (l.56)
- `def run(m=None, a=None, i=None, c=CONSTRAINTS, cfg=RiskConfig())` (l.60)
- `def test_happy_path_sizing_is_exact()` (l.69)
- `def test_determinism()` (l.84)
- `def test_full_check_chain_reported()` (l.88)
- `def test_only_buy_is_actionable(action)` (l.100)
- `def test_garbage_action_rejected()` (l.106)
- `def test_confidence_out_of_bounds_rejected()` (l.112)
- `def test_low_confidence_rejected()` (l.118)
- `def test_llm_bearish_regime_blocks_entry()` (l.124)
- `def test_string_enums_accepted_from_json()` (l.130)
- `def test_stale_data_rejected()` (l.140)
- `def test_future_timestamp_rejected()` (l.145)
- `def test_invalid_market_data_rejected(price, ema, atr)` (l.151)
- `def test_zero_equity_rejected()` (l.156)
- `def test_locked_bot_rejected()` (l.166)
- `def test_circuit_breakers(field, value, code)` (l.180)
- `def test_circuit_breakers_below_threshold_pass()` (l.186)
- `def test_order_rate_limit()` (l.190)
- `def test_counter_trend_rejected_at_ema()` (l.200)
- `def test_trend_buffer_matters()` (l.206)
- `def test_falling_knife_rejected()` (l.212)
- `def test_falling_knife_threshold_is_inclusive()` (l.217)
- `def test_dead_market_rejected()` (l.224)
- `def test_abnormal_volatility_rejected()` (l.229)
- `def test_position_already_open()` (l.239)
- `def test_other_symbol_fills_max_positions()` (l.244)
- `def test_notional_cap_clamps_size_and_lowers_effective_risk()` (l.254)
- `def test_cash_cap_clamps_size()` (l.265)
- `def test_absolute_notional_cap()` (l.272)
- `def test_min_notional_rejected()` (l.279)
- `def test_min_amount_rejected()` (l.284)
- `def test_zero_free_balance_rejected()` (l.293)
- `def test_loss_streak_halves_risk()` (l.298)
- `def test_stop_is_truncated_down_never_up()` (l.305)
- `def test_risk_budget_never_exceeded(atr, equity, cash_ratio)` (l.322)
- `def test_dangerous_config_rejected(kwargs)` (l.363)
- `def test_naive_datetime_is_rejected_not_raised()` (l.374)
- `def test_non_finite_price_is_rejected(bad)` (l.391)
- `def test_non_finite_atr_is_rejected()` (l.397)
- `def test_unexpected_type_becomes_internal_error_not_exception()` (l.402)
- `def test_evaluate_never_raises_on_hostile_input()` (l.410)

### `backend/tests/test_schema_contract.py`

*Contrat entre le schéma SQL et le code Python.*

- `def enum_labels(type_name: str) -> set[str]` (l.26) — Libellés d'un ENUM, à travers **toutes** les migrations.
- `def test_python_codes_all_exist_in_sql()` (l.55)
- `def test_sql_codes_all_exist_in_python()` (l.64)
- `def test_rejection_code_has_no_empty_label()` (l.74)
- `def test_system_state_includes_executing()` (l.83)
- `def test_order_type_supports_binance_spot_stop()` (l.89)
- `def test_order_type_never_used_for_entry_is_declared()` (l.97)
- `def test_migration_files_are_ordered_and_named()` (l.106)
- `def test_no_migration_uses_timescaledb()` (l.113)
- `def test_every_partitioned_table_declares_a_default_partition()` (l.123)
- `def test_guardrails_are_enforced_in_the_database(guard)` (l.143)
- `def test_execution_mode_separates_paper_from_live()` (l.147)

### `backend/tests/test_settings.py`

*Tests du chargement de configuration : presets, écrasements, verrous d'armement.*

- `def test_defaults_are_paper_and_do_not_touch_real_money(tmp_path: Path)` (l.13)
- `def test_profile_preset_changes_several_knobs_at_once(tmp_path: Path)` (l.23)
- `def test_individual_override_beats_preset(tmp_path: Path)` (l.29)
- `def test_live_mode_requires_two_deliberate_gestures(tmp_path: Path)` (l.38)
- `def test_live_mode_accepted_only_when_fully_configured(tmp_path: Path)` (l.62)
- `def test_bad_values_fail_loudly(tmp_path: Path)` (l.77)
- `def test_dotenv_file_is_read(tmp_path: Path)` (l.86)
- `def test_binance_key_aliases_are_accepted(tmp_path: Path)` (l.94)
- `def test_explicit_exchange_names_win_over_aliases(tmp_path: Path)` (l.105)
- `def test_secrets_never_appear_in_repr(tmp_path: Path)` (l.119)
- `def test_paper_and_testnet_refuse_a_disabled_sandbox(tmp_path: Path, mode)` (l.137)
- `def test_execution_mode_follows_the_declared_mode(tmp_path: Path)` (l.145)
- `def test_kraken_key_aliases_are_accepted(tmp_path: Path)` (l.159)
- `def test_kraken_does_not_borrow_binance_keys(tmp_path: Path)` (l.173)
- `def test_explicit_exchange_key_still_wins_on_kraken(tmp_path: Path)` (l.188)
- `def test_fee_buffer_default_matches_the_exchange_taker_fee(tmp_path: Path)` (l.200)
- `def test_quote_currency_is_derived_from_the_symbol(tmp_path: Path)` (l.212)
- `def test_declared_symbols_keep_the_primary_symbol_first(tmp_path: Path)` (l.220)
- `def test_unknown_exchange_is_refused(tmp_path: Path)` (l.232)
- `def test_micro_profile_loads_for_small_accounts(tmp_path: Path)` (l.242)
- `def test_daily_breaker_leaves_room_for_more_than_one_loss(tmp_path: Path, profile: str)` (l.251)
- `def test_invalid_sweep_ratio_is_refused_at_startup(tmp_path: Path)` (l.258)
- `def test_negative_sweep_minimum_is_refused(tmp_path: Path)` (l.263)
- `def test_negative_paper_slippage_is_refused(tmp_path: Path)` (l.271)

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

## 4. Tests

Cas réellement collectés par pytest (les `parametrize` sont comptés à leur juste nombre, pas comme une seule fonction).

| Fichier | Cas collectés |
|---|---|
| `backend/tests/__init__.py` | 0 |
| `backend/tests/test_affordability.py` | 6 |
| `backend/tests/test_api.py` | 12 |
| `backend/tests/test_api_security.py` | 22 |
| `backend/tests/test_backtest.py` | 7 |
| `backend/tests/test_backtest_golden.py` | 3 |
| `backend/tests/test_check_exchange.py` | 9 |
| `backend/tests/test_db_config.py` | 10 |
| `backend/tests/test_feed.py` | 9 |
| `backend/tests/test_kraken_adapter.py` | 22 |
| `backend/tests/test_llm.py` | 16 |
| `backend/tests/test_machine.py` | 4 |
| `backend/tests/test_orchestrator.py` | 12 |
| `backend/tests/test_paper.py` | 9 |
| `backend/tests/test_recovery.py` | 3 |
| `backend/tests/test_risk_gate.py` | 129 |
| `backend/tests/test_schema_contract.py` | 15 |
| `backend/tests/test_settings.py` | 28 |
| `backend/tests/test_sweep.py` | 13 |
| **Total** | **329** |

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

## 6. Rafraîchir

```bash
python scripts/index_codebase.py          # régénère ce fichier
python scripts/index_codebase.py --check  # échoue si périmé
```
