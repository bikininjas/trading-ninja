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
    "ExchangeConstraints",
    "MarketRegime",
    "MarketSnapshot",
    "RejectionCode",
    "RiskConfig",
    "RiskDecision",
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


class MarketRegime(StrEnum):
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGING = "RANGING"
    VOLATILE = "VOLATILE"


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

    def __post_init__(self) -> None:
        if not (Decimal("0") < self.risk_per_trade_pct <= Decimal("0.05")):
            raise ValueError(
                "risk_per_trade_pct doit être dans ]0, 5%] : au-delà, ce n'est plus du trading "
                "mais du casino, et le paramètre est refusé au démarrage."
            )
        if not (Decimal("0") < self.max_notional_pct <= Decimal("1")):
            raise ValueError("max_notional_pct doit être dans ]0, 1]")
        if self.stop_atr_multiple <= 0:
            raise ValueError("stop_atr_multiple doit être > 0")
        if self.take_profit_r_multiple < 0:
            raise ValueError("take_profit_r_multiple doit être >= 0")
        if self.max_daily_drawdown_pct > Decimal("0.20"):
            raise ValueError("max_daily_drawdown_pct > 20 % : garde-fou inopérant")
        if self.risk_throttle_factor <= 0 or self.risk_throttle_factor >= 1:
            raise ValueError("risk_throttle_factor doit être dans ]0, 1[")
        if self.min_atr_pct_of_price >= self.max_atr_pct_of_price:
            raise ValueError("bornes de volatilité incohérentes")

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

    risk_pct = config.effective_risk_pct(account.consecutive_losses)
    intended_risk = account.equity_quote * risk_pct

    # Trois plafonds concurrents : le risque nominal, l'exposition, le cash réel.
    max_by_risk = intended_risk / actual_distance
    max_by_notional = min(
        account.equity_quote * config.max_notional_pct,
        config.max_position_notional_quote,
    ) / market.price
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
