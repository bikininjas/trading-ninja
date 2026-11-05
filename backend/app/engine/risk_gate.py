"""Risk Gateway — pare-feu transactionnel déterministe (fail-closed).

Ce module est volontairement PUR :
  - aucune entrée/sortie, aucun appel réseau, aucune horloge implicite ;
  - aucune dépendance externe (ni FastAPI, ni SQLAlchemy, ni CCXT, ni Pydantic).

Conséquence : le composant le plus critique du système est testable de façon
exhaustive en quelques millisecondes, et son comportement est *identique* en
backtest, en paper-trading et en production. Aucun chemin de code ne peut
« se comporter différemment » selon l'environnement.

Contrat : `evaluate()` retourne TOUJOURS une décision. Une exception n'est pas
un chemin de sortie normal — toute anomalie devient un rejet explicite, jamais
un ordre.

Règle d'or : le LLM propose, ce module dispose. Aucun champ produit par le LLM
(taille, prix, levier, stop) n'est utilisé. Seuls `action`, `confidence` et
`market_regime` sont lus, et tous les trois ne peuvent que BLOQUER.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from enum import StrEnum

__all__ = [
    "Action",
    "AccountState",
    "EntryMode",
    "ExchangeConstraints",
    "MarketRegime",
    "MarketSnapshot",
    "RejectionCode",
    "RiskConfig",
    "RiskDecision",
    "StopMode",
    "SwingExitMode",
    "TradeIntent",
    "DEFAULT_CONFIG",
    "evaluate",
]


# ---------------------------------------------------------------------------
# Vocabulaire (miroir exact des ENUMs PostgreSQL)
# ---------------------------------------------------------------------------


class Action(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    HALT = "HALT"


class MarketRegime(StrEnum):
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGING = "RANGING"
    VOLATILE = "VOLATILE"


class EntryMode(StrEnum):
    """Où la règle d'entrée cherche le repli.

    Ce n'est pas un réglage de risque : les modes passent le même pare-feu et
    le même sizing. Seul l'instant où le modèle est consulté change, donc la
    fréquence des setups — et c'est elle que le balayage mesure (D-058).
    """

    EMA50_H4 = "EMA50_H4"  # setup historique : repli sur l'EMA50 H4
    PULLBACK_H1 = "PULLBACK_H1"  # repli fin : l'EMA50 H1 est touchée puis refermée
    SWING_MACRO_D1 = "SWING_MACRO_D1"  # cassure/repli de valeur en Daily (D-060)


class StopMode(StrEnum):
    """Comment le stop initial est calculé.

    ``ATR`` est le comportement historique (bruit du timeframe de décision).
    ``STRUCTURAL_D1`` pose le stop sous le creux D1, avec un plancher de distance :
    un swing a besoin d'air, pas d'un stop dans le bruit d'une bougie H1.
    """

    ATR = "ATR"
    STRUCTURAL_D1 = "STRUCTURAL_D1"


class SwingExitMode(StrEnum):
    """Comment une position swing D1 est soldée hors stop protecteur.

    ``EMA20`` est la sortie historique de D-060 : clôture sous l'EMA20 D1 ou
    divergence baissière sur Bollinger. ``CHANDELIER`` suit le plus haut des
    ``N`` dernières bougies avec un retrait en ATR, et ne redescend jamais.
    Le stop structurel intrabar reste la première ligne de défense dans les
    deux modes.
    """

    EMA20 = "EMA20"
    CHANDELIER = "CHANDELIER"


class RejectionCode(StrEnum):
    NONE = "NONE"

    # Protocole / données
    ACTION_IS_NOT_BUY = "ACTION_IS_NOT_BUY"
    STALE_MARKET_DATA = "STALE_MARKET_DATA"
    INVALID_MARKET_DATA = "INVALID_MARKET_DATA"
    MALFORMED_INTENT = "MALFORMED_INTENT"
    INTERNAL_ERROR = "INTERNAL_ERROR"

    # État du bot
    BOT_LOCKED = "BOT_LOCKED"
    CIRCUIT_BREAKER_DAILY_DRAWDOWN = "CIRCUIT_BREAKER_DAILY_DRAWDOWN"
    CIRCUIT_BREAKER_WEEKLY_DRAWDOWN = "CIRCUIT_BREAKER_WEEKLY_DRAWDOWN"
    CIRCUIT_BREAKER_MAX_DRAWDOWN = "CIRCUIT_BREAKER_MAX_DRAWDOWN"
    CIRCUIT_BREAKER_CONSECUTIVE_LOSSES = "CIRCUIT_BREAKER_CONSECUTIVE_LOSSES"
    MAX_ORDERS_PER_HOUR_EXCEEDED = "MAX_ORDERS_PER_HOUR_EXCEEDED"

    # Qualité du signal
    CONFIDENCE_TOO_LOW = "CONFIDENCE_TOO_LOW"
    POSITION_ALREADY_OPEN = "POSITION_ALREADY_OPEN"
    REJECTED_LOW_VOLATILITY = "REJECTED_LOW_VOLATILITY"
    REJECTED_EXTREME_VOLATILITY = "REJECTED_EXTREME_VOLATILITY"
    REJECTED_COUNTER_TREND_EMA200 = "REJECTED_COUNTER_TREND_EMA200"
    REJECTED_REGIME_CONFLICT = "REJECTED_REGIME_CONFLICT"
    REJECTED_FALLING_KNIFE = "REJECTED_FALLING_KNIFE"

    # Dimensionnement / exchange
    INSUFFICIENT_FREE_BALANCE = "INSUFFICIENT_FREE_BALANCE"
    INVALID_STOP_LOSS_CALCULATION = "INVALID_STOP_LOSS_CALCULATION"
    SIZING_BELOW_EXCHANGE_MINIMUM = "SIZING_BELOW_EXCHANGE_MINIMUM"
    EXCHANGE_MIN_NOTIONAL_FAILED = "EXCHANGE_MIN_NOTIONAL_FAILED"
    RISK_LIMIT_EXCEEDED = "RISK_LIMIT_EXCEEDED"


# ---------------------------------------------------------------------------
# Entrées
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TradeIntent:
    """Ce que le LLM a répondu. Non fiable par construction."""

    action: Action
    confidence: float
    market_regime: MarketRegime = MarketRegime.RANGING
    thesis: str = ""


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    """État de marché au moment de l'évaluation.

    Tous les indicateurs doivent provenir de bougies **clôturées** : une valeur
    recalculée en cours de bougie « repeint » et rend le comportement
    irreproductible entre backtest et live.
    """

    symbol: str
    price: Decimal
    ema_200_h4: Decimal
    atr_14_h4: Decimal
    last_closed_h1_return_atr: Decimal  # rendement de la dernière H1 clôturée, en multiples d'ATR14(H1)
    tick_at: datetime  # horodatage du dernier tick reçu (UTC)
    now: datetime  # horloge système (UTC) — injectée, jamais lue implicitement
    # Pente de l'EMA200 H4 sur les 60 dernières bougies (10 jours), en ATR H4.
    # Sert de filtre de régime : une EMA200 plate ou qui descend n'est pas une
    # tendance haussière, même si le prix est encore au-dessus.
    ema_200_slope_atr: Decimal = Decimal("0")
    # Creux des 10 dernières D1, utilisé par StopMode.STRUCTURAL_D1. 0 = absent :
    # le mode structurel refuse alors le trade au lieu de retomber sur l'ATR.
    swing_low_10d: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class AccountState:
    """Photographie du compte. La réserve (funding) est exclue par construction."""

    equity_quote: Decimal  # USDT libre + position valorisée au prix du marché
    free_quote: Decimal  # USDT réellement disponible
    open_position_symbols: tuple[str, ...] = ()
    consecutive_losses: int = 0
    daily_drawdown_pct: Decimal = Decimal("0")  # 0.02 == 2 %
    weekly_drawdown_pct: Decimal = Decimal("0")
    drawdown_from_hwm_pct: Decimal = Decimal("0")  # écart au plus haut d'équité
    orders_last_hour: int = 0
    locked: bool = False
    lock_reason: RejectionCode | None = None


@dataclass(frozen=True, slots=True)
class ExchangeConstraints:
    """Lues dynamiquement via CCXT `market[...]`. Jamais codées en dur."""

    amount_step: Decimal
    min_amount: Decimal
    min_notional: Decimal
    price_tick: Decimal


# ---------------------------------------------------------------------------
# Configuration — tout est réglable, rien n'est enfoui dans le code
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RiskConfig:
    """Réglages du pare-feu.

    Les valeurs par défaut constituent le profil PRUDENT (phase 1 : préserver le
    capital et accumuler des données, pas maximiser le rendement). Toute
    augmentation de risque se fait ici, sans toucher à la logique.
    """

    # --- Taille du risque ---------------------------------------------------
    risk_per_trade_pct: Decimal = Decimal("0.005")  # 0.5 % de l'équité par trade
    max_notional_pct: Decimal = Decimal("0.20")  # exposition max par position
    max_position_notional_quote: Decimal = Decimal("2000")  # plafond absolu, anti-bug
    fee_buffer_pct: Decimal = Decimal("0.002")  # marge pour frais taker A/R

    # --- Stop / sortie ------------------------------------------------------
    stop_atr_multiple: Decimal = Decimal("2.5")  # aligné sur l'ATR du timeframe de décision
    take_profit_r_multiple: Decimal = Decimal("2.0")  # 0 == pas de TP (sortie SL only)
    trend_buffer_pct: Decimal = Decimal("0.005")  # il faut être 0.5 % AU-DESSUS de l'EMA200
    falling_knife_atr_multiple: Decimal = Decimal("2.5")
    # Stop structurel D1 : sous le creux des 10 dernières bougies, mais jamais à
    # plus de ``max_stop_distance_pct`` sous l'entrée. Le plancher évite qu'un
    # creux lointain ne réduise la taille sous le minimum de l'exchange.
    stop_mode: StopMode = StopMode.ATR
    max_stop_distance_pct: Decimal = Decimal("0.05")

    # --- Petit portefeuille -------------------------------------------------
    # Autorise 100 % de l'équité en exposition quand elle est sous le seuil, pour
    # que le minimum notionnel de Kraken (~10 EUR) reste atteignable. Désactivé
    # par défaut : sans ce drapeau, le plafond de 50 % continue de s'appliquer.
    small_account_full_allocation: bool = False
    small_account_threshold_quote: Decimal = Decimal("50")
    # Plafond de risque quand le plancher de viabilité du petit compte s'applique.
    # 5 % est le maximum absolu déjà imposé par risk_per_trade_pct.
    small_account_max_risk_pct: Decimal = Decimal("0.05")

    # --- Filtres de régime --------------------------------------------------
    min_atr_pct_of_price: Decimal = Decimal("0.0015")  # marché mort => pas d'edge, on passe
    max_atr_pct_of_price: Decimal = Decimal("0.08")  # volatilité anormale => données suspectes
    min_confidence: Decimal = Decimal("0.80")
    reject_trending_down_regime: bool = True

    # --- Coupe-circuits -----------------------------------------------------
    max_daily_drawdown_pct: Decimal = Decimal("0.02")  # 2 % / 24 h glissantes
    max_weekly_drawdown_pct: Decimal = Decimal("0.05")  # 5 % / 7 jours glissants
    max_drawdown_from_hwm_pct: Decimal = Decimal("0.10")  # -10 % depuis le plus haut => STOP
    max_consecutive_losses: int = 4  # verrouillage dur
    risk_throttle_after_losses: int = 2  # à partir de 2 pertes...
    risk_throttle_factor: Decimal = Decimal("0.5")  # ...risque divisé par 2

    # --- Hygiène d'exécution ------------------------------------------------
    max_orders_per_hour: int = 6
    stale_data_seconds: int = 5
    max_open_positions: int = 1

    # --- Règle d'entrée et sorties proposées par le LLM ---------------------
    # La règle réduit les instants où le modèle est consulté ; elle ne remplace pas
    # le pare-feu. Les seuils de sortie sont plus bas que celui d'entrée : solder
    # une position est une action qui réduit le risque, pas qui en crée.
    entry_rule_enabled: bool = True
    entry_require_positive_h1: bool = False
    entry_rsi_min: Decimal = Decimal("40")
    entry_rsi_max: Decimal = Decimal("65")
    entry_pullback_atr: Decimal = Decimal("0.75")
    entry_require_ema50_above_ema200: bool = True
    # Pente minimale de l'EMA200 H4 sur 10 jours, en ATR. 0 = pas de filtre.
    entry_ema200_slope_min: Decimal = Decimal("0")
    # Mode de recherche du repli (D-058). EMA50_H4 est le comportement historique ;
    # PULLBACK_H1 élargit le setup sans toucher au pare-feu.
    entry_mode: EntryMode = EntryMode.EMA50_H4
    entry_pullback_h1_atr: Decimal = Decimal("1.0")
    entry_h1_rsi_min: Decimal = Decimal("38")
    entry_h1_rsi_max: Decimal = Decimal("55")
    # --- SWING_MACRO_D1 (D-060) ---------------------------------------------
    # Le Daily ne cherche plus un micro-repli : il suit la tendance de fond par
    # cassure de compression (Donchian 20) ou rebond de valeur sur l'EMA20.
    swing_donchian_days: int = 20
    swing_rsi_min: Decimal = Decimal("50")
    swing_rsi_max: Decimal = Decimal("65")
    swing_require_donchian: bool = True
    swing_require_value_rebound: bool = True
    swing_breakeven_r: Decimal = Decimal("1")  # stop remonté à l'entrée à +1 R
    swing_exit_mode: SwingExitMode = SwingExitMode.EMA20
    swing_exit_below_ema20: bool = True
    swing_bollinger_period: int = 20
    swing_bollinger_std: Decimal = Decimal("2.5")
    # Chandelier : plus haut des N bougies moins N × ATR. Le stop ne descend
    # jamais ; une clôture sous ce niveau solde la position (D-061).
    swing_chandelier_period: int = 20
    swing_chandelier_atr_multiple: Decimal = Decimal("3.0")
    min_exit_confidence: Decimal = Decimal("0.55")
    min_halt_confidence: Decimal = Decimal("0.80")

    def __post_init__(self) -> None:
        if not (Decimal("0") < self.risk_per_trade_pct <= Decimal("0.05")):
            raise ValueError(
                "risk_per_trade_pct doit être dans ]0, 5%] : au-delà, ce n'est plus du trading "
                "mais du casino, et le paramètre est refusé au démarrage."
            )
        notional_ceiling = Decimal("1") if self.small_account_full_allocation else Decimal("0.50")
        if not (Decimal("0") < self.max_notional_pct <= notional_ceiling):
            raise ValueError(
                "max_notional_pct doit être dans ]0, 50 %] (ou ]0, 100 %] avec "
                "small_account_full_allocation) : un seul achat ne peut pas engager "
                "plus de la moitié du capital sans drapeau explicite (pas de all-in)."
            )
        if self.small_account_threshold_quote <= 0:
            raise ValueError("small_account_threshold_quote doit être > 0")
        if not (Decimal("0") < self.small_account_max_risk_pct <= Decimal("0.05")):
            raise ValueError("small_account_max_risk_pct doit être dans ]0, 5 %]")
        if self.stop_atr_multiple <= 0:
            raise ValueError("stop_atr_multiple doit être > 0")
        if self.stop_mode not in tuple(StopMode):
            raise ValueError(f"stop_mode inconnu : {self.stop_mode!r}")
        if not (Decimal("0") < self.max_stop_distance_pct <= Decimal("0.25")):
            raise ValueError("max_stop_distance_pct doit être dans ]0, 25 %]")
        if self.take_profit_r_multiple < 0:
            raise ValueError("take_profit_r_multiple doit être >= 0")
        if self.max_daily_drawdown_pct > Decimal("0.20"):
            raise ValueError("max_daily_drawdown_pct > 20 % : garde-fou inopérant")
        if self.risk_throttle_factor <= 0 or self.risk_throttle_factor >= 1:
            raise ValueError("risk_throttle_factor doit être dans ]0, 1[")
        if self.min_atr_pct_of_price >= self.max_atr_pct_of_price:
            raise ValueError("bornes de volatilité incohérentes")
        if not (Decimal(0) <= self.entry_rsi_min < self.entry_rsi_max <= Decimal(100)):
            raise ValueError("bornes RSI de la règle d'entrée incohérentes")
        if self.entry_pullback_atr <= 0:
            raise ValueError("entry_pullback_atr doit être > 0")
        if self.entry_ema200_slope_min < 0:
            raise ValueError("entry_ema200_slope_min ne peut pas être négatif")
        if self.entry_mode not in tuple(EntryMode):
            raise ValueError(
                f"entry_mode inconnu : {self.entry_mode!r} "
                f"(connus : {', '.join(m.value for m in EntryMode)})"
            )
        if self.entry_pullback_h1_atr <= 0:
            raise ValueError("entry_pullback_h1_atr doit être > 0")
        if not (Decimal(0) <= self.entry_h1_rsi_min < self.entry_h1_rsi_max <= Decimal(100)):
            raise ValueError("bornes RSI H1 de la règle d'entrée incohérentes")
        if not (Decimal(0) <= self.swing_rsi_min < self.swing_rsi_max <= Decimal(100)):
            raise ValueError("bornes RSI D1 de la règle swing incohérentes")
        if self.swing_donchian_days < 2:
            raise ValueError("swing_donchian_days doit être >= 2")
        if self.swing_breakeven_r < 0:
            raise ValueError("swing_breakeven_r doit être >= 0")
        if self.swing_exit_mode not in tuple(SwingExitMode):
            raise ValueError(
                f"swing_exit_mode inconnu : {self.swing_exit_mode!r} "
                f"(connus : {', '.join(m.value for m in SwingExitMode)})"
            )
        if not (2 <= self.swing_bollinger_period <= 200):
            raise ValueError("swing_bollinger_period doit être dans [2, 200]")
        if self.swing_bollinger_std <= 0:
            raise ValueError("swing_bollinger_std doit être > 0")
        if not (2 <= self.swing_chandelier_period <= 200):
            raise ValueError("swing_chandelier_period doit être dans [2, 200]")
        if (
            not self.swing_chandelier_atr_multiple.is_finite()
            or self.swing_chandelier_atr_multiple <= 0
        ):
            raise ValueError("swing_chandelier_atr_multiple doit être fini et > 0")
        if not (Decimal(0) <= self.min_exit_confidence <= Decimal(1)):
            raise ValueError("min_exit_confidence doit être dans [0, 1]")
        if not (Decimal(0) <= self.min_halt_confidence <= Decimal(1)):
            raise ValueError("min_halt_confidence doit être dans [0, 1]")

    # -- dérivés -------------------------------------------------------------

    def effective_risk_pct(self, consecutive_losses: int) -> Decimal:
        """Risque unitaire après étranglement progressif par série de pertes."""
        pct = self.risk_per_trade_pct
        if self.risk_throttle_after_losses > 0 and consecutive_losses >= self.risk_throttle_after_losses:
            pct = pct * self.risk_throttle_factor
        return pct


DEFAULT_CONFIG = RiskConfig()


# ---------------------------------------------------------------------------
# Sortie
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RiskDecision:
    approved: bool
    rejection_code: RejectionCode = RejectionCode.NONE
    rejection_details: str | None = None

    # Renseignés uniquement si approved
    amount: Decimal | None = None
    notional_quote: Decimal | None = None
    stop_loss_price: Decimal | None = None
    take_profit_price: Decimal | None = None
    stop_distance_quote: Decimal | None = None
    intended_risk_quote: Decimal | None = None
    effective_risk_quote: Decimal | None = None
    effective_risk_pct: Decimal | None = None

    checks_passed: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return self.approved


def _reject(code: RejectionCode, details: str, passed: list[str]) -> RiskDecision:
    return RiskDecision(
        approved=False,
        rejection_code=code,
        rejection_details=details,
        checks_passed=tuple(passed),
    )


def _floor_to(value: Decimal, step: Decimal) -> Decimal:
    """Tronque vers le bas au pas autorisé par l'exchange (jamais d'arrondi au-dessus)."""
    if step <= 0:
        return value
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def _as_decimal(value: float | Decimal | int) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _is_finite(value: Decimal) -> bool:
    """`Decimal('NaN')` et `Decimal('Infinity')` sont des valeurs valides pour
    Decimal mais invalides pour un prix : les laisser passer ferait lever plus loin."""
    return value.is_finite()


# ---------------------------------------------------------------------------
# Le pipeline
# ---------------------------------------------------------------------------


def evaluate(
    intent: TradeIntent,
    market: MarketSnapshot,
    account: AccountState,
    constraints: ExchangeConstraints,
    config: RiskConfig = DEFAULT_CONFIG,
) -> RiskDecision:
    """Point d'entrée public. **Ne lève jamais.**

    Le module s'engage à toujours retourner une décision : une exception qui
    remonterait jusqu'à la boucle principale pourrait être interprétée comme un
    « continue » et laisser un ordre orphelin. Toute anomalie imprévue devient donc un
    refus explicite `INTERNAL_ERROR`, jamais une exception.
    """
    try:
        return _evaluate(intent, market, account, constraints, config)
    except Exception as exc:  # noqa: BLE001 — fail-closed assumé et documenté
        return RiskDecision(
            approved=False,
            rejection_code=RejectionCode.INTERNAL_ERROR,
            rejection_details=f"{type(exc).__name__}: {exc}",
        )


def _evaluate(
    intent: TradeIntent,
    market: MarketSnapshot,
    account: AccountState,
    constraints: ExchangeConstraints,
    config: RiskConfig = DEFAULT_CONFIG,
) -> RiskDecision:
    """Filtre une intention de trade. Entonnoir strictement ordonné, fail-closed.

    L'ordre est délibéré : les contrôles les moins coûteux et les plus
    protecteurs d'abord, les calculs de dimensionnement en dernier.
    """
    passed: list[str] = []

    # -- 0. Intégrité de l'entrée -------------------------------------------
    # On accepte str ou enum (le LLM produit du JSON, donc des chaînes) et on
    # normalise. Tout ce qui ne se normalise pas est un rejet, jamais un ordre.
    try:
        action = Action(intent.action)
        regime = MarketRegime(intent.market_regime)
        confidence = _as_decimal(intent.confidence)
    except (ValueError, ArithmeticError) as exc:
        return _reject(RejectionCode.MALFORMED_INTENT, f"intention illisible: {exc}", passed)
    if not (Decimal(0) <= confidence <= Decimal(1)):
        return _reject(RejectionCode.MALFORMED_INTENT, f"confidence hors bornes: {intent.confidence}", passed)
    if market.now.tzinfo is None or market.tick_at.tzinfo is None:
        # Une date naïve face à une date consciente lève TypeError ; on refuse avant,
        # avec un motif lisible plutôt qu'une trace d'exécution.
        return _reject(
            RejectionCode.MALFORMED_INTENT,
            "horodatages sans fuseau horaire — UTC explicite exigé",
            passed,
        )
    if not all(_is_finite(v) for v in (market.price, market.ema_200_h4, market.atr_14_h4)):
        # Decimal('NaN') n'est jamais <= 0 : sans ce test, NaN traverserait tout le
        # dimensionnement sans être arrêté par aucune comparaison.
        return _reject(RejectionCode.INVALID_MARKET_DATA, "prix/EMA/ATR non finis (NaN ou infini)", passed)
    if not all(_is_finite(v) for v in (account.equity_quote, account.free_quote)):
        return _reject(RejectionCode.INVALID_MARKET_DATA, "équité ou solde libre non fini", passed)
    if market.price <= 0 or market.ema_200_h4 <= 0 or market.atr_14_h4 <= 0:
        return _reject(RejectionCode.INVALID_MARKET_DATA, "prix/EMA/ATR non strictement positifs", passed)
    if account.equity_quote <= 0:
        return _reject(RejectionCode.INVALID_MARKET_DATA, "équité nulle ou négative", passed)
    passed.append("input_integrity")

    # -- 1. Fraîcheur des données -------------------------------------------
    age = market.now - market.tick_at
    if age > timedelta(seconds=config.stale_data_seconds):
        return _reject(
            RejectionCode.STALE_MARKET_DATA,
            f"dernier tick il y a {age.total_seconds():.1f}s (max {config.stale_data_seconds}s)",
            passed,
        )
    if age < timedelta(seconds=-config.stale_data_seconds):
        return _reject(RejectionCode.STALE_MARKET_DATA, "horodatage dans le futur (horloge désynchronisée)", passed)
    passed.append("data_freshness")

    # -- 2. Le bot n'achète que sur BUY --------------------------------------
    if action is not Action.BUY:
        return _reject(
            RejectionCode.ACTION_IS_NOT_BUY,
            f"action={action} ; le moteur est long-only spot, seules les entrées BUY sont exécutables",
            passed,
        )
    passed.append("action_is_buy")

    # -- 3. État du bot / coupe-circuits durs --------------------------------
    if account.locked:
        return _reject(
            account.lock_reason or RejectionCode.BOT_LOCKED,
            "bot verrouillé : déverrouillage manuel 2FA requis",
            passed,
        )
    passed.append("bot_unlocked")

    if account.daily_drawdown_pct >= config.max_daily_drawdown_pct:
        return _reject(
            RejectionCode.CIRCUIT_BREAKER_DAILY_DRAWDOWN,
            f"drawdown 24h {account.daily_drawdown_pct:.4%} >= {config.max_daily_drawdown_pct:.2%}",
            passed,
        )
    if account.weekly_drawdown_pct >= config.max_weekly_drawdown_pct:
        return _reject(
            RejectionCode.CIRCUIT_BREAKER_WEEKLY_DRAWDOWN,
            f"drawdown 7j {account.weekly_drawdown_pct:.4%} >= {config.max_weekly_drawdown_pct:.2%}",
            passed,
        )
    if account.drawdown_from_hwm_pct >= config.max_drawdown_from_hwm_pct:
        return _reject(
            RejectionCode.CIRCUIT_BREAKER_MAX_DRAWDOWN,
            f"écart au plus haut {account.drawdown_from_hwm_pct:.4%} >= {config.max_drawdown_from_hwm_pct:.2%}",
            passed,
        )
    if account.consecutive_losses >= config.max_consecutive_losses:
        return _reject(
            RejectionCode.CIRCUIT_BREAKER_CONSECUTIVE_LOSSES,
            f"{account.consecutive_losses} pertes consécutives >= {config.max_consecutive_losses}",
            passed,
        )
    passed.append("circuit_breakers")

    # -- 4. Hygiène d'exécution (anti-emballement) ---------------------------
    if account.orders_last_hour >= config.max_orders_per_hour:
        return _reject(
            RejectionCode.MAX_ORDERS_PER_HOUR_EXCEEDED,
            f"{account.orders_last_hour} ordres sur 60 min >= {config.max_orders_per_hour}",
            passed,
        )
    passed.append("order_rate_limit")

    # -- 5. Qualité du signal ------------------------------------------------
    if confidence < config.min_confidence:
        return _reject(
            RejectionCode.CONFIDENCE_TOO_LOW,
            f"confidence {confidence} < {config.min_confidence}",
            passed,
        )
    passed.append("confidence_floor")

    if config.reject_trending_down_regime and regime is MarketRegime.TRENDING_DOWN:
        return _reject(
            RejectionCode.REJECTED_REGIME_CONFLICT,
            "le modèle lui-même qualifie le régime de baissier",
            passed,
        )
    passed.append("regime_consistent")

    # -- 6. Monoposition -----------------------------------------------------
    if len(account.open_position_symbols) >= config.max_open_positions:
        return _reject(
            RejectionCode.POSITION_ALREADY_OPEN,
            f"positions ouvertes: {', '.join(account.open_position_symbols)}",
            passed,
        )
    if market.symbol in account.open_position_symbols:
        return _reject(RejectionCode.POSITION_ALREADY_OPEN, f"position déjà ouverte sur {market.symbol}", passed)
    passed.append("monoposition")

    # -- 7. Filtres de volatilité -------------------------------------------
    atr_pct = market.atr_14_h4 / market.price
    if atr_pct > config.max_atr_pct_of_price:
        return _reject(
            RejectionCode.REJECTED_EXTREME_VOLATILITY,
            f"ATR/prix {atr_pct:.4%} > {config.max_atr_pct_of_price:.2%} : volatilité anormale",
            passed,
        )
    if atr_pct < config.min_atr_pct_of_price:
        return _reject(
            RejectionCode.REJECTED_LOW_VOLATILITY,
            f"ATR/prix {atr_pct:.4%} < {config.min_atr_pct_of_price:.2%} : marché atone, pas d'edge",
            passed,
        )
    passed.append("volatility_band")

    # -- 8. Contexte de tendance ---------------------------------------------
    trend_floor = market.ema_200_h4 * (Decimal(1) + config.trend_buffer_pct)
    if market.price <= trend_floor:
        return _reject(
            RejectionCode.REJECTED_COUNTER_TREND_EMA200,
            f"prix {market.price} <= EMA200 H4 + marge ({trend_floor:.4f})",
            passed,
        )
    passed.append("long_term_trend")

    if market.last_closed_h1_return_atr <= -config.falling_knife_atr_multiple:
        return _reject(
            RejectionCode.REJECTED_FALLING_KNIFE,
            f"dernière H1: {market.last_closed_h1_return_atr} ATR (seuil -{config.falling_knife_atr_multiple})",
            passed,
        )
    passed.append("no_falling_knife")

    # -- 9. Dimensionnement --------------------------------------------------
    if config.stop_mode is StopMode.STRUCTURAL_D1:
        swing_low = market.swing_low_10d
        if not _is_finite(swing_low) or swing_low <= 0:
            return _reject(
                RejectionCode.INVALID_STOP_LOSS_CALCULATION,
                f"creux D1 indisponible ({swing_low}) pour un stop structurel",
                passed,
            )
        # Stop sous le creux, mais jamais à plus de max_stop_distance_pct :
        # on prend le plus haut des deux (le plus proche de l'entrée).
        floor_price = market.price * (Decimal(1) - config.max_stop_distance_pct)
        raw_stop = max(swing_low, floor_price)
    else:
        stop_distance = config.stop_atr_multiple * market.atr_14_h4
        raw_stop = market.price - stop_distance
    stop_price = _floor_to(raw_stop, constraints.price_tick)
    if stop_price <= 0:
        return _reject(
            RejectionCode.INVALID_STOP_LOSS_CALCULATION,
            f"stop calculé nul ou négatif ({stop_price}) pour prix {market.price}",
            passed,
        )
    actual_distance = market.price - stop_price
    if actual_distance <= 0:
        return _reject(RejectionCode.INVALID_STOP_LOSS_CALCULATION, "distance de stop nulle", passed)

    if account.free_quote <= 0:
        return _reject(
            RejectionCode.INSUFFICIENT_FREE_BALANCE,
            f"solde libre disponible {account.free_quote} (après buffer de frais)",
            passed,
        )
    if account.free_quote < constraints.min_notional:
        return _reject(
            RejectionCode.EXCHANGE_MIN_NOTIONAL_FAILED,
            f"solde libre {account.free_quote} < minimum notionnel exchange "
            f"{constraints.min_notional} : aucune nouvelle entrée possible",
            passed,
        )

    risk_pct = config.effective_risk_pct(account.consecutive_losses)
    if (config.small_account_full_allocation
            and account.equity_quote < config.small_account_threshold_quote
            and account.consecutive_losses < config.risk_throttle_after_losses):
        # Plancher de viabilité : le plus petit risque qui permet d'atteindre le
        # minimum notionnel de l'exchange. Sans lui, 1,75 % de 20 EUR avec un stop
        # à 5 % ne finance que 7 EUR et aucun ordre ne passe.
        minimum_amount = constraints.min_notional / market.price
        required_risk = minimum_amount * actual_distance
        required_pct = required_risk / account.equity_quote
        if required_pct > config.small_account_max_risk_pct:
            return _reject(
                RejectionCode.EXCHANGE_MIN_NOTIONAL_FAILED,
                f"minimum notionnel {constraints.min_notional} exigerait "
                f"{required_pct:.2%} de risque, au-delà du plafond petit compte "
                f"{config.small_account_max_risk_pct:.2%}",
                passed,
            )
        risk_pct = max(risk_pct, required_pct)
    intended_risk = account.equity_quote * risk_pct

    # Trois plafonds concurrents : le risque nominal, l'exposition, le cash réel.
    max_by_risk = intended_risk / actual_distance
    if (config.small_account_full_allocation
            and account.equity_quote < config.small_account_threshold_quote):
        # Petit compte : le minimum notionnel de l'exchange est le vrai plancher.
        # Le stop borne la perte, pas la taille ; 100 % est explicitement demandé.
        exposure_cap = account.equity_quote
    else:
        exposure_cap = min(
            account.equity_quote * min(config.max_notional_pct, Decimal("0.50")),
            config.max_position_notional_quote,
        )
    max_by_notional = exposure_cap / market.price
    spendable = account.free_quote * (Decimal(1) - config.fee_buffer_pct)
    max_by_cash = spendable / market.price

    amount = _floor_to(min(max_by_risk, max_by_notional, max_by_cash), constraints.amount_step)

    if spendable <= 0 or amount <= 0:
        return _reject(
            RejectionCode.INSUFFICIENT_FREE_BALANCE,
            f"solde libre disponible {account.free_quote} (après buffer de frais)",
            passed,
        )
    if amount < constraints.min_amount:
        return _reject(
            RejectionCode.SIZING_BELOW_EXCHANGE_MINIMUM,
            f"quantité {amount} < minimum exchange {constraints.min_amount}",
            passed,
        )

    notional = amount * market.price
    if notional < constraints.min_notional:
        return _reject(
            RejectionCode.EXCHANGE_MIN_NOTIONAL_FAILED,
            f"notionnel {notional:.4f} < minimum exchange {constraints.min_notional}",
            passed,
        )
    passed.append("sizing")

    # -- 10. Vérification finale : jamais plus de risque que demandé ---------
    effective_risk = amount * actual_distance
    effective_risk_pct = effective_risk / account.equity_quote
    if effective_risk_pct > risk_pct:
        # Ne doit jamais arriver ; si ça arrive, le calcul est faux => on refuse.
        return _reject(
            RejectionCode.RISK_LIMIT_EXCEEDED,
            f"risque effectif {effective_risk_pct:.4%} > budget {risk_pct:.4%}",
            passed,
        )
    passed.append("risk_budget_enforced")

    take_profit: Decimal | None = None
    if config.take_profit_r_multiple > 0:
        take_profit = _floor_to(
            market.price + config.take_profit_r_multiple * actual_distance,
            constraints.price_tick,
        )

    return RiskDecision(
        approved=True,
        amount=amount,
        notional_quote=notional,
        stop_loss_price=stop_price,
        take_profit_price=take_profit,
        stop_distance_quote=actual_distance,
        intended_risk_quote=intended_risk,
        effective_risk_quote=effective_risk,
        effective_risk_pct=effective_risk_pct,
        checks_passed=tuple(passed),
    )
