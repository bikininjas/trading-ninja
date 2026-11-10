"""Configuration centralisée — chargée depuis l'environnement, sans dépendance externe.

Deux principes :

1. **Rien de sensible n'a de valeur par défaut utilisable en production.** Si une
   clé manque, le démarrage échoue bruyamment au lieu de partir avec un secret
   vide et de « fonctionner » à moitié.

2. **Tout ce qui touche au risque est un paramètre.** Changer de profil (prudent →
   agressif) ne doit jamais demander de modifier du code, seulement `.env`.
   `RISK_PROFILE` fournit des presets ; les variables individuelles les écrasent.

Le mode par défaut est `paper` : aucun ordre réel ne peut partir sans une action
explicite et une variable d'environnement supplémentaire.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Mapping

from app.core.db import DatabaseConfig
from app.core.db import from_env as database_from_env
from app.core.env import load_env, read_dotenv
from app.engine.capital import CapitalPolicy
from app.engine.risk_gate import EntryMode, RiskConfig, StopMode, SwingExitMode

__all__ = ["Settings", "ExchangeSettings", "LLMSettings", "load_settings", "RISK_PRESETS"]

TRUTHY = {"1", "true", "yes", "on", "oui"}


# ---------------------------------------------------------------------------
# Presets de risque — la seule chose à changer pour faire évoluer le bot
# ---------------------------------------------------------------------------

RISK_PRESETS: dict[str, dict[str, str]] = {
    # Phase 1 : prouver que le système ne perd pas d'argent avant de chercher à en gagner.
    "prudent": {
        "RISK_PER_TRADE_PCT": "0.005",
        "MAX_NOTIONAL_PCT": "0.20",
        "STOP_ATR_MULTIPLE": "2.5",
        "TAKE_PROFIT_R_MULTIPLE": "2.0",
        "MIN_CONFIDENCE": "0.80",
        "ENTRY_EMA200_SLOPE_MIN": "2.0",
        "MAX_DAILY_DRAWDOWN_PCT": "0.02",
        "MAX_WEEKLY_DRAWDOWN_PCT": "0.05",
        "MAX_DRAWDOWN_FROM_HWM_PCT": "0.10",
        "MAX_CONSECUTIVE_LOSSES": "4",
        "MAX_ORDERS_PER_HOUR": "6",
    },
    # Phase 2 : une fois >= 100 trades paper concluants et une courbe d'équité propre.
    "standard": {
        "RISK_PER_TRADE_PCT": "0.0075",
        "MAX_NOTIONAL_PCT": "0.35",
        "STOP_ATR_MULTIPLE": "2.5",
        "TAKE_PROFIT_R_MULTIPLE": "2.0",
        "MIN_CONFIDENCE": "0.75",
        "ENTRY_EMA200_SLOPE_MIN": "2.0",
        "MAX_DAILY_DRAWDOWN_PCT": "0.03",
        "MAX_WEEKLY_DRAWDOWN_PCT": "0.07",
        "MAX_DRAWDOWN_FROM_HWM_PCT": "0.15",
        "MAX_CONSECUTIVE_LOSSES": "5",
        "MAX_ORDERS_PER_HOUR": "10",
    },
    # Phase 3 : jamais atteinte sans revue manuelle. Volontairement plafonnée.
    "aggressive": {
        "RISK_PER_TRADE_PCT": "0.01",
        "MAX_NOTIONAL_PCT": "0.50",
        "STOP_ATR_MULTIPLE": "2.0",
        "TAKE_PROFIT_R_MULTIPLE": "2.0",
        "MIN_CONFIDENCE": "0.70",
        "ENTRY_EMA200_SLOPE_MIN": "1.5",
        "MAX_DAILY_DRAWDOWN_PCT": "0.04",
        "MAX_WEEKLY_DRAWDOWN_PCT": "0.10",
        "MAX_DRAWDOWN_FROM_HWM_PCT": "0.20",
        "MAX_CONSECUTIVE_LOSSES": "6",
        "MAX_ORDERS_PER_HOUR": "15",
    },
    # Compte de quelques euros. Le minimum d'ordre de l'exchange (3,20 EUR sur
    # SHIB/EUR, le plus bas des paires EUR) impose un plancher en valeur absolue :
    # à 0,5 % de 9,37 EUR (4,7 centimes), aucun ordre ne passe. Les pourcentages
    # montent donc, mais le montant risqué par trade reste sous 0,20 EUR.
    # 50 % d'exposition est assumé : le stop borne la perte, pas la taille.
    "micro": {
        "RISK_PER_TRADE_PCT": "0.0175",
        "MAX_NOTIONAL_PCT": "0.50",
        "STOP_ATR_MULTIPLE": "2.5",
        "TAKE_PROFIT_R_MULTIPLE": "2.0",
        # Le modèle mesuré plafonne à 0,72 sur les BUY. Un plancher à 0,85 rendait
        # tout achat impossible ; 0,60 rend le LLM capable d'agir, et la règle
        # d'entrée + le pare-feu restent les filtres. C'est une expérience mesurée
        # par le backtest, pas une baisse de garde-fou : le dimensionnement et le
        # stop ne dépendent pas de la confiance.
        "MIN_CONFIDENCE": "0.60",
        "ENTRY_EMA200_SLOPE_MIN": "2.0",
        "MAX_DAILY_DRAWDOWN_PCT": "0.08",
        "MAX_WEEKLY_DRAWDOWN_PCT": "0.15",
        "MAX_DRAWDOWN_FROM_HWM_PCT": "0.20",
        "MAX_CONSECUTIVE_LOSSES": "4",
        "MAX_ORDERS_PER_HOUR": "3",
    },
    # Swing macro Daily (D-060) : peu de trades, stop structurel sous le creux D1,
    # pas de take-profit fixe (la sortie est la tendance qui casse). Le seuil de
    # confiance reste bas : le LLM peut bloquer, il ne dimensionne rien.
    "swing": {
        "ENTRY_MODE": "SWING_MACRO_D1",
        "STOP_MODE": "STRUCTURAL_D1",
        "RISK_PER_TRADE_PCT": "0.005",
        "MAX_NOTIONAL_PCT": "1.0",
        "SMALL_ACCOUNT_FULL_ALLOCATION": "true",
        "STOP_ATR_MULTIPLE": "2.5",
        "TAKE_PROFIT_R_MULTIPLE": "0",
        "MIN_CONFIDENCE": "0.60",
        "ENTRY_EMA200_SLOPE_MIN": "0",
        "SWING_EXIT_MODE": "ema20",
        "MAX_DAILY_DRAWDOWN_PCT": "0.08",
        "MAX_WEEKLY_DRAWDOWN_PCT": "0.15",
        "MAX_DRAWDOWN_FROM_HWM_PCT": "0.20",
        "MAX_CONSECUTIVE_LOSSES": "6",
        "MAX_ORDERS_PER_HOUR": "3",
    },
}


RISK_PRESETS["progressif"] = {
    **RISK_PRESETS["prudent"],
    "MAX_NOTIONAL_PCT": "0.50",
    "TAKE_PROFIT_R_MULTIPLE": "0",
    "MIN_CONFIDENCE": "0.60",
    "COST_AWARE": "true",
    "PROFIT_TRAILING_ENABLED": "true",
}


# ---------------------------------------------------------------------------
# Spécificités exchange
# ---------------------------------------------------------------------------

# Les clés sont nommées par leur exchange : un fichier de secrets peut contenir les
# deux jeux, et prendre « la première clé trouvée » ferait trader Kraken avec une clé
# Binance. Le nom explicite EXCHANGE_API_KEY reste prioritaire sur les alias.
EXCHANGE_KEY_ALIASES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "binance": (("BINANCE_API_KEY", "BINANCE_KEY"), ("BINANCE_SECRET_KEY", "BINANCE_SECRET")),
    "kraken": (("KRAKEN_API_KEY", "KRAKEN_KEY"), ("KRAKEN_SECRET", "KRAKEN_SECRET_KEY")),
}

# Marge de frais utilisée pour ne jamais engager le solde libre en entier.
# Elle doit couvrir un aller-retour au tarif taker de l'exchange, sinon le dernier
# achat peut échouer pour quelques centimes de frais. Mesuré sur les marchés réels :
# Binance spot 0,10 %/côté → 0,20 % ; Kraken spot 0,80 %/côté (palier 1 depuis le
# 2026-07-09) → 1,60 %. Le tarif réel du compte est lu par `fetch_trading_fee` et
# peut être surchargé par `TAKER_FEE_PCT`.
EXCHANGE_FEE_BUFFER_DEFAULTS: dict[str, str] = {
    "binance": "0.002",
    "kraken": "0.016",
}

# Tarifs de repli si l'exchange ne répond pas ou si le compte n'est pas lisible.
# CCXT annonce encore 0,26 %/0,16 % pour Kraken : c'est le tarif historique, pas
# le palier 1 actuel. Le tarif réel est demandé à l'API privée quand une clé existe.
EXCHANGE_TAKER_FEE_DEFAULTS: dict[str, str] = {
    "binance": "0.001",
    "kraken": "0.008",
}
EXCHANGE_MAKER_FEE_DEFAULTS: dict[str, str] = {
    "binance": "0.001",
    "kraken": "0.004",
}

# Clés qu'un fichier de réglages runtime (écrit par le dashboard) a le droit de
# modifier. Tout le reste est ignoré : un fichier de réglages ne doit pas pouvoir
# changer le mode d'exécution, l'exchange ou les secrets.
RUNTIME_ALLOWED_KEYS = frozenset({
    "RISK_PROFILE",
    "RISK_PER_TRADE_PCT",
    "MAX_NOTIONAL_PCT",
    "MAX_DAILY_DRAWDOWN_PCT",
    "MAX_WEEKLY_DRAWDOWN_PCT",
    "MAX_DRAWDOWN_FROM_HWM_PCT",
    "MAX_CONSECUTIVE_LOSSES",
    "MAX_ORDERS_PER_HOUR",
    "MIN_CONFIDENCE",
    "ENTRY_RULE_ENABLED",
    "ENTRY_RSI_MIN",
    "ENTRY_RSI_MAX",
    "ENTRY_PULLBACK_ATR",
    "ENTRY_REQUIRE_EMA50_ABOVE_EMA200",
    "MIN_EXIT_CONFIDENCE",
    "MIN_HALT_CONFIDENCE",
    "HALT_AUTO_RESUME_HOURS",
})


# ---------------------------------------------------------------------------
# Helpers de lecture
# ---------------------------------------------------------------------------


def _env(env: Mapping[str, str], key: str, default: str | None = None) -> str:
    if key in env and env[key] != "":
        return env[key]
    if default is None:
        raise RuntimeError(f"Variable d'environnement obligatoire manquante : {key}")
    return default


def _env_dec(env: Mapping[str, str], key: str, default: str) -> Decimal:
    return Decimal(_env(env, key, default))


def _env_int(env: Mapping[str, str], key: str, default: str) -> int:
    return int(_env(env, key, default))


def _env_bool(env: Mapping[str, str], key: str, default: str = "false") -> bool:
    return _env(env, key, default).lower() in TRUTHY


def _first(env: Mapping[str, str], *names: str, default: str = "") -> str:
    """Première variable non vide parmi plusieurs alias acceptés."""
    for name in names:
        value = env.get(name)
        if value:
            return value
    return default


# ---------------------------------------------------------------------------
# Groupes de configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExchangeSettings:
    exchange_id: str = "binance"
    api_key: str = field(default="", repr=False)
    secret: str = field(default="", repr=False)
    # Clé dédiée au kill-switch : Kraken exige des nonces strictement croissants
    # par clé. Sans seconde paire, le kill-switch ne peut pas signer pendant que
    # le bot tourne avec la même clé.
    kill_api_key: str = field(default="", repr=False)
    kill_secret: str = field(default="", repr=False)
    sandbox: bool = True
    symbol: str = "BTC/USDT"
    quote_currency: str = "USDT"
    allowed_symbols: tuple[str, ...] = ()
    daily_timeframe_decision: str = "4h"
    entry_timeframe: str = "1h"
    taker_fee: Decimal = Decimal("0.001")
    maker_fee: Decimal = Decimal("0.001")


@dataclass(frozen=True, slots=True)
class LLMSettings:
    base_url: str = "http://127.0.0.1:11434/v1"
    model: str = "qwen2.5:14b-instruct-q4_K_M"
    # 4 s est la cible du cahier des charges, mais le modèle 9B met 6,3 s sur cette
    # machine (mesuré, 82 tokens). Un défaut à 4 s produirait 100 % de replis.
    timeout_seconds: float = 8.0
    max_retries: int = 1
    temperature: float = 0.0


@dataclass(frozen=True, slots=True)
class Settings:
    env: str
    debug: bool
    mode: str  # "paper" | "testnet" | "live"
    risk: RiskConfig
    risk_profile: str
    exchange: ExchangeSettings
    llm: LLMSettings
    master_encryption_key: str = field(default="", repr=False)
    jwt_secret: str = field(default="", repr=False)
    totp_secret: str = field(default="", repr=False)
    admin_password_hash: str = field(default="", repr=False)
    paper_start_equity: Decimal = Decimal("0")
    paper_slippage_bps: int = 5
    capital_policy: CapitalPolicy = field(default_factory=CapitalPolicy)
    insights_enabled: bool = True
    ranking_mode: str = "observe"
    observer_api_key: str = field(default="", repr=False)
    observer_secret: str = field(default="", repr=False)
    observer_interval_seconds: int = 15
    sweep_ratio: Decimal = Decimal("0.30")
    sweep_min_transfer_quote: Decimal = Decimal("1.00")
    halt_auto_resume_hours: int = 24
    _raw_env: Mapping[str, str] = field(default_factory=dict, repr=False, compare=False)

    @property
    def is_live(self) -> bool:
        return self.mode == "live"

    @property
    def is_sandbox(self) -> bool:
        return self.exchange.sandbox

    @property
    def execution_mode(self) -> str:
        """Valeur écrite dans les colonnes `mode` de la base.

        Dérivée du mode déclaré uniquement : sandbox ne peut plus faire basculer une
        configuration « testnet » en étiquette TESTNET alors qu'elle parle au réel.
        """
        if self.mode == "live":
            return "LIVE"
        if self.mode == "testnet":
            return "TESTNET"
        return "PAPER"

    def database(self) -> DatabaseConfig:
        """Résolution paresseuse : la base n'est exigée que si on s'en sert."""
        return database_from_env(self._raw_env)


# ---------------------------------------------------------------------------
# Chargement
# ---------------------------------------------------------------------------


def load_settings(env_file: str | Path | None = None, overrides: Mapping[str, str] | None = None,
                  *, use_environment: bool = True) -> Settings:
    """Charge la configuration. Lève RuntimeError si incohérente.

    Sans `env_file` : chaîne `backend/.env` → `.env` → `.env.local` → os.environ.
    Avec `env_file` : ce fichier remplace la chaîne (utile en test).
    Dans les deux cas : overrides gagne sur tout.
    """
    if env_file is not None:
        merged: dict[str, str] = read_dotenv(Path(env_file))
        if use_environment:
            merged.update(os.environ)
    else:
        merged = load_env() if use_environment else {}

    if overrides:
        merged.update(overrides)

    mode = _env(merged, "TRADING_MODE", "paper").lower()
    if mode not in {"paper", "testnet", "live"}:
        raise RuntimeError(f"TRADING_MODE invalide : {mode!r} (attendu: paper | testnet | live)")

    profile = _env(merged, "RISK_PROFILE", "prudent").lower()
    if profile not in RISK_PRESETS:
        raise RuntimeError(f"RISK_PROFILE inconnu : {profile!r} (connus: {', '.join(RISK_PRESETS)})")
    preset = RISK_PRESETS[profile]
    if profile == "progressif" and mode != "paper":
        raise RuntimeError("Le profil progressif reste réservé au mode paper jusqu'à validation")

    exchange_id = _env(merged, "EXCHANGE_ID", "binance").lower()
    if exchange_id not in EXCHANGE_KEY_ALIASES:
        raise RuntimeError(
            f"EXCHANGE_ID inconnu : {exchange_id!r} "
            f"(connus: {', '.join(sorted(EXCHANGE_KEY_ALIASES))})"
        )

    def p(key: str) -> str:
        return merged.get(key, preset.get(key, ""))

    try:
        entry_mode = EntryMode(_env(merged, "ENTRY_MODE", preset.get("ENTRY_MODE", "EMA50_H4")))
    except ValueError as exc:
        raise RuntimeError(
            f"ENTRY_MODE invalide : {merged.get('ENTRY_MODE')!r} "
            f"(connus : {', '.join(m.value for m in EntryMode)})"
        ) from exc
    try:
        stop_mode = StopMode(_env(merged, "STOP_MODE", preset.get("STOP_MODE", "ATR")))
    except ValueError as exc:
        raise RuntimeError(
            f"STOP_MODE invalide : {merged.get('STOP_MODE')!r} "
            f"(connus : {', '.join(m.value for m in StopMode)})"
        ) from exc
    try:
        swing_exit_mode = SwingExitMode(
            _env(merged, "SWING_EXIT_MODE", preset.get("SWING_EXIT_MODE", "EMA20")).upper()
        )
    except ValueError as exc:
        raise RuntimeError(
            f"SWING_EXIT_MODE invalide : {merged.get('SWING_EXIT_MODE')!r} "
            f"(connus : {', '.join(m.value for m in SwingExitMode)})"
        ) from exc

    default_decision = "1d" if entry_mode is EntryMode.SWING_MACRO_D1 else "4h"
    decision_timeframe = _env(merged, "DECISION_TIMEFRAME", default_decision)
    if entry_mode is EntryMode.SWING_MACRO_D1 and decision_timeframe != "1d":
        raise RuntimeError(
            "ENTRY_MODE=SWING_MACRO_D1 exige DECISION_TIMEFRAME=1d : la règle "
            "Donchian/EMA20 lit des bougies Daily, pas H4."
        )

    fee = Decimal(merged.get("TAKER_FEE_PCT", EXCHANGE_TAKER_FEE_DEFAULTS[exchange_id]))
    slip_bps = Decimal(merged.get("PAPER_SLIPPAGE_BPS", "5"))
    if not fee.is_finite() or not Decimal(0) < fee <= Decimal("0.05"):
        raise RuntimeError("TAKER_FEE_PCT doit être fini et dans ]0, 5 %]")
    if not slip_bps.is_finite() or not Decimal(0) <= slip_bps < Decimal(1000):
        raise RuntimeError("PAPER_SLIPPAGE_BPS doit être fini et dans [0, 1000[")
    configured_cost_aware = _env_bool(
        merged, "COST_AWARE", preset.get("COST_AWARE", "false")
    )
    risk_per_trade_pct = Decimal(p("RISK_PER_TRADE_PCT"))
    small_account_max_risk_pct = Decimal(
        merged.get("SMALL_ACCOUNT_MAX_RISK_PCT", "0.05")
    )
    # En réel, le minimum exchange ne peut jamais relever le risque autorisé.
    # Charger la configuration reste possible pour gérer une position existante.
    if mode == "live":
        small_account_max_risk_pct = min(small_account_max_risk_pct, risk_per_trade_pct)
    risk = RiskConfig(
        cost_aware=mode == "live" or configured_cost_aware,
        execution_fee_pct=fee,
        execution_slippage_pct=slip_bps / Decimal(10000),
        opportunity_r_multiple=Decimal(merged.get("OPPORTUNITY_R_MULTIPLE", "4")),
        min_net_reward_to_risk=Decimal(merged.get("MIN_NET_REWARD_TO_RISK", "1.5")),
        profit_trailing_enabled=_env_bool(
            merged, "PROFIT_TRAILING_ENABLED", preset.get("PROFIT_TRAILING_ENABLED", "false")),
        profit_trailing_start_r=Decimal(merged.get("PROFIT_TRAILING_START_R", "2")),
        profit_trailing_distance_r=Decimal(merged.get("PROFIT_TRAILING_DISTANCE_R", "1")),
        risk_per_trade_pct=risk_per_trade_pct,
        max_notional_pct=Decimal(p("MAX_NOTIONAL_PCT")),
        max_position_notional_quote=Decimal(merged.get("MAX_POSITION_NOTIONAL_QUOTE", "2000")),
        fee_buffer_pct=Decimal(
            merged.get("FEE_BUFFER_PCT", EXCHANGE_FEE_BUFFER_DEFAULTS[exchange_id])
        ),
        stop_atr_multiple=Decimal(p("STOP_ATR_MULTIPLE")),
        take_profit_r_multiple=Decimal(p("TAKE_PROFIT_R_MULTIPLE")),
        trend_buffer_pct=Decimal(merged.get("TREND_BUFFER_PCT", "0.005")),
        falling_knife_atr_multiple=Decimal(merged.get("FALLING_KNIFE_ATR_MULTIPLE", "2.5")),
        stop_mode=stop_mode,
        max_stop_distance_pct=Decimal(merged.get("MAX_STOP_DISTANCE_PCT", "0.05")),
        small_account_full_allocation=_env_bool(
            merged, "SMALL_ACCOUNT_FULL_ALLOCATION",
            preset.get("SMALL_ACCOUNT_FULL_ALLOCATION", "false"),
        ),
        small_account_threshold_quote=Decimal(
            merged.get("SMALL_ACCOUNT_THRESHOLD_QUOTE", "50")
        ),
        small_account_max_risk_pct=small_account_max_risk_pct,
        min_atr_pct_of_price=Decimal(merged.get("MIN_ATR_PCT_OF_PRICE", "0.0015")),
        max_atr_pct_of_price=Decimal(merged.get("MAX_ATR_PCT_OF_PRICE", "0.08")),
        min_confidence=Decimal(p("MIN_CONFIDENCE")),
        reject_trending_down_regime=_env_bool(merged, "REJECT_TRENDING_DOWN_REGIME", "true"),
        max_daily_drawdown_pct=Decimal(p("MAX_DAILY_DRAWDOWN_PCT")),
        max_weekly_drawdown_pct=Decimal(p("MAX_WEEKLY_DRAWDOWN_PCT")),
        max_drawdown_from_hwm_pct=Decimal(p("MAX_DRAWDOWN_FROM_HWM_PCT")),
        max_consecutive_losses=_env_int(merged, "MAX_CONSECUTIVE_LOSSES", p("MAX_CONSECUTIVE_LOSSES")),
        risk_throttle_after_losses=_env_int(merged, "RISK_THROTTLE_AFTER_LOSSES", "2"),
        risk_throttle_factor=Decimal(merged.get("RISK_THROTTLE_FACTOR", "0.5")),
        max_orders_per_hour=_env_int(merged, "MAX_ORDERS_PER_HOUR", p("MAX_ORDERS_PER_HOUR")),
        stale_data_seconds=_env_int(merged, "STALE_DATA_SECONDS", "5"),
        max_open_positions=_env_int(merged, "MAX_OPEN_POSITIONS", "1"),
        entry_rule_enabled=_env_bool(merged, "ENTRY_RULE_ENABLED", "true"),
        entry_rsi_min=Decimal(merged.get("ENTRY_RSI_MIN", "40")),
        entry_rsi_max=Decimal(merged.get("ENTRY_RSI_MAX", "65")),
        entry_pullback_atr=Decimal(merged.get("ENTRY_PULLBACK_ATR", "0.75")),
        entry_require_ema50_above_ema200=_env_bool(
            merged, "ENTRY_REQUIRE_EMA50_ABOVE_EMA200", "true"
        ),
        entry_ema200_slope_min=Decimal(p("ENTRY_EMA200_SLOPE_MIN")),
        entry_mode=entry_mode,
        entry_pullback_h1_atr=Decimal(merged.get("ENTRY_PULLBACK_H1_ATR", "1.0")),
        entry_h1_rsi_min=Decimal(merged.get("ENTRY_H1_RSI_MIN", "38")),
        entry_h1_rsi_max=Decimal(merged.get("ENTRY_H1_RSI_MAX", "55")),
        swing_donchian_days=_env_int(merged, "SWING_DONCHIAN_DAYS", "20"),
        swing_rsi_min=Decimal(merged.get("SWING_RSI_MIN", "50")),
        swing_rsi_max=Decimal(merged.get("SWING_RSI_MAX", "65")),
        swing_require_donchian=_env_bool(merged, "SWING_REQUIRE_DONCHIAN", "true"),
        swing_require_value_rebound=_env_bool(merged, "SWING_REQUIRE_VALUE_REBOUND", "true"),
        swing_breakeven_r=Decimal(merged.get("SWING_BREAKEVEN_R", "1")),
        swing_exit_mode=swing_exit_mode,
        swing_exit_below_ema20=_env_bool(merged, "SWING_EXIT_BELOW_EMA20", "true"),
        swing_bollinger_period=_env_int(merged, "SWING_BOLLINGER_PERIOD", "20"),
        swing_bollinger_std=Decimal(merged.get("SWING_BOLLINGER_STD", "2.5")),
        swing_chandelier_period=_env_int(merged, "SWING_CHANDELIER_PERIOD", "20"),
        swing_chandelier_atr_multiple=Decimal(
            merged.get("SWING_CHANDELIER_ATR_MULTIPLE", "3.0")
        ),
        min_exit_confidence=Decimal(merged.get("MIN_EXIT_CONFIDENCE", "0.55")),
        min_halt_confidence=Decimal(merged.get("MIN_HALT_CONFIDENCE", "0.80")),
    )

    try:
        capital_policy = CapitalPolicy(
            enabled=_env_bool(merged, "CAPITAL_STEPS_ENABLED", "true" if profile == "progressif" else "false"),
            reserve_ratio=Decimal(merged.get("SWEEP_RATIO", "0.30")),
            growth_trigger=Decimal(merged.get("CAPITAL_GROWTH_TRIGGER", "1")),
            step_quote=Decimal(merged.get("CAPITAL_STEP_QUOTE", "0.25")),
            step_max_pct=Decimal(merged.get("CAPITAL_STEP_MAX_PCT", "0.05")),
            interval_days=int(merged.get("CAPITAL_INTERVAL_DAYS", "7")),
            max_active_quote=Decimal(merged.get("CAPITAL_MAX_ACTIVE_QUOTE", "20")),
        )
    except ValueError as exc:
        raise RuntimeError(f"Politique de capital invalide (SWEEP_RATIO / CAPITAL_*) : {exc}") from exc
    if capital_policy.enabled and mode != "paper":
        raise RuntimeError("Les paliers de capital restent réservés au mode paper")
    if capital_policy.enabled and not risk.cost_aware:
        raise RuntimeError("Les paliers exigent COST_AWARE=true pour borner les pertes frais compris")

    ranking_mode=_env(merged,"RANKING_MODE","observe")
    if ranking_mode not in {"observe","paper_select"} or (ranking_mode=="paper_select" and mode!="paper"):
        raise RuntimeError("classement sélectionnable uniquement en papier, observation par défaut")
    if not 5<=int(_env(merged,"OBSERVER_INTERVAL_SECONDS","15"))<=300:
        raise RuntimeError("surveillance entre 5 et 300 secondes")

    sandbox = _env_bool(merged, "EXCHANGE_SANDBOX_MODE", "true")

    # Cohérence mode / sandbox. Sans ce contrôle, `TRADING_MODE=testnet` avec
    # `EXCHANGE_SANDBOX_MODE=false` chargeait sans erreur : la config se croyait en
    # testnet, `execution_mode` étiquetait TESTNET, et les ordres partaient en réel.
    # Le seul chemin vers l'argent réel est `live` + sandbox=false + verrou explicite.
    if mode in {"paper", "testnet"} and not sandbox:
        raise RuntimeError(
            f"TRADING_MODE={mode} exige EXCHANGE_SANDBOX_MODE=true. "
            "L'argent réel ne s'obtient que par TRADING_MODE=live + "
            "EXCHANGE_SANDBOX_MODE=false + I_UNDERSTAND_REAL_MONEY=true."
        )
    if mode == "live" and sandbox:
        raise RuntimeError("TRADING_MODE=live incompatible avec EXCHANGE_SANDBOX_MODE=true")
    if mode == "live" and not _env_bool(merged, "I_UNDERSTAND_REAL_MONEY", "false"):
        raise RuntimeError(
            "TRADING_MODE=live refuse de démarrer sans I_UNDERSTAND_REAL_MONEY=true. "
            "Ce verrou existe pour qu'un déploiement accidentel ne puisse pas engager d'argent réel."
        )

    symbol = _env(merged, "TRADING_SYMBOL", "BTC/USDT")
    quote_currency = _env(merged, "QUOTE_CURRENCY", symbol.split("/")[-1].upper())
    declared_symbols = tuple(
        item.strip() for item in merged.get("TRADING_SYMBOLS", "").split(",") if item.strip()
    )
    # Le symbole principal est toujours autorisé ; la liste sert de garde-fou pour
    # les flux multi-paires à venir, pas d'universe dynamique.
    allowed_symbols = tuple(dict.fromkeys((symbol, *declared_symbols)))

    key_names, secret_names = EXCHANGE_KEY_ALIASES[exchange_id]
    taker_fee = Decimal(merged.get("TAKER_FEE_PCT", EXCHANGE_TAKER_FEE_DEFAULTS[exchange_id]))
    maker_fee = Decimal(merged.get("MAKER_FEE_PCT", EXCHANGE_MAKER_FEE_DEFAULTS[exchange_id]))
    if not (Decimal("0") < taker_fee <= Decimal("0.05")):
        raise RuntimeError(f"TAKER_FEE_PCT doit être dans ]0, 5%], reçu {taker_fee}")
    if not (Decimal("0") <= maker_fee <= Decimal("0.05")):
        raise RuntimeError(f"MAKER_FEE_PCT doit être dans [0, 5%], reçu {maker_fee}")
    kill_api_key = _env(merged, "KILL_SWITCH_API_KEY", "")
    kill_secret = _env(merged, "KILL_SWITCH_SECRET", "")
    if bool(kill_api_key) != bool(kill_secret):
        raise RuntimeError(
            "KILL_SWITCH_API_KEY et KILL_SWITCH_SECRET vont ensemble : "
            "une clé secondaire incomplète ne peut pas signer."
        )
    exchange = ExchangeSettings(
        exchange_id=exchange_id,
        api_key=_first(merged, "EXCHANGE_API_KEY", *key_names),
        secret=_first(merged, "EXCHANGE_SECRET", "EXCHANGE_SECRET_KEY", *secret_names),
        kill_api_key=kill_api_key,
        kill_secret=kill_secret,
        sandbox=sandbox,
        symbol=symbol,
        quote_currency=quote_currency,
        allowed_symbols=allowed_symbols,
        daily_timeframe_decision=decision_timeframe,
        entry_timeframe=_env(merged, "ENTRY_TIMEFRAME", "1h"),
        taker_fee=taker_fee,
        maker_fee=maker_fee,
    )
    if mode == "live" and (not exchange.api_key or not exchange.secret):
        raise RuntimeError("TRADING_MODE=live sans clés API exchange")

    # Un ratio d'écrémage invalide doit échouer au démarrage, pas au moment de
    # clôturer une position gagnante — à cet instant, l'argent est déjà sorti.
    sweep_ratio = Decimal(merged.get("SWEEP_RATIO", "0.30"))
    if not (Decimal(0) < sweep_ratio < Decimal(1)):
        raise RuntimeError(
            f"SWEEP_RATIO doit être strictement entre 0 et 1, reçu {sweep_ratio}"
        )
    sweep_min = Decimal(merged.get("SWEEP_MIN_TRANSFER_QUOTE", "1.00"))
    if sweep_min < 0:
        raise RuntimeError("SWEEP_MIN_TRANSFER_QUOTE ne peut pas être négatif")
    slippage_bps = int(merged.get("PAPER_SLIPPAGE_BPS", "5"))
    if slippage_bps < 0:
        raise RuntimeError("PAPER_SLIPPAGE_BPS ne peut pas être négatif")

    halt_auto_resume_hours = _env_int(merged, "HALT_AUTO_RESUME_HOURS", "24")
    if not (0 <= halt_auto_resume_hours <= 168):
        raise RuntimeError("HALT_AUTO_RESUME_HOURS doit être entre 0 et 168 heures")

    llm = LLMSettings(
        base_url=_env(merged, "LLM_BASE_URL", "http://127.0.0.1:11434/v1"),
        model=_env(merged, "LLM_MODEL_NAME", "qwen2.5:14b-instruct-q4_K_M"),
        timeout_seconds=float(merged.get("LLM_TIMEOUT_SECONDS", "5.0")),
        max_retries=int(merged.get("LLM_MAX_RETRIES", "1")),
        temperature=float(merged.get("LLM_TEMPERATURE", "0.0")),
    )

    return Settings(
        env=_env(merged, "ENV", "development"),
        debug=_env_bool(merged, "DEBUG", "true"),
        mode=mode,
        risk=risk,
        risk_profile=profile,
        exchange=exchange,
        llm=llm,
        master_encryption_key=_env(merged, "MASTER_ENCRYPTION_KEY", ""),
        jwt_secret=_env(merged, "JWT_SECRET_KEY", ""),
        totp_secret=_env(merged, "TOTP_SECRET", ""),
        admin_password_hash=_env(merged, "ADMIN_PASSWORD_HASH", ""),
        paper_start_equity=Decimal(merged.get("PAPER_START_EQUITY", "0")),
        paper_slippage_bps=slippage_bps,
        capital_policy=capital_policy,
        insights_enabled=_env_bool(merged, "INSIGHTS_ENABLED", "true"),
        ranking_mode=_env(merged, "RANKING_MODE", "observe"),
        observer_api_key=_env(merged, "OBSERVER_API_KEY", ""),
        observer_secret=_env(merged, "OBSERVER_SECRET", ""),
        observer_interval_seconds=int(_env(merged, "OBSERVER_INTERVAL_SECONDS", "15")),
        sweep_ratio=sweep_ratio,
        sweep_min_transfer_quote=sweep_min,
        halt_auto_resume_hours=halt_auto_resume_hours,
        _raw_env=merged,
    )
