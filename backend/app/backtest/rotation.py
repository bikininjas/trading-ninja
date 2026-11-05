"""Laboratoire de rotation momentum — BTC/ETH/SOL contre cash, sans I/O.

Hypothèse testée : plutôt que de choisir un sens par paire, ne détenir **qu'un
seul** actif à la fois — celui qui a la plus forte force relative — et rester en
cash quand aucun actif ne mérite le risque.

Règles :

- univers : BTC/EUR, ETH/EUR, SOL/EUR, plus CASH/EUR (rendement nul) ;
- réévaluation toutes les 7 ou 14 bougies D1 ;
- force relative : moyenne des rendements 30 D1 et 60 D1 ;
- l'actif leader est celui de plus forte force relative ;
- un filtre absolu optionnel décide s'il reçoit l'allocation : aucun filtre
  (``none``), clôture au-dessus de l'EMA200 D1 (``ema200``), ROC60 positif
  (``roc60``) ou les deux (``dual``) ;
- si le filtre absolu échoue, allocation 100 % en cash.

Le module est PUR : il reçoit des bougies D1 déjà agrégées et ne touche ni à la
base, ni à l'exchange, ni à une horloge. Les frais sont appliqués à chaque
rotation (vente puis achat), avec le slippage existant du broker papier. C'est
un laboratoire de mesure, pas un chemin d'exécution.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Sequence

from app.backtest.engine import _max_drawdown
from app.backtest.swing import _ema_series

__all__ = [
    "ABS_FILTERS",
    "CASH",
    "Momentum",
    "Rotation",
    "RotationResult",
    "evaluate_momentum",
    "run_rotation_backtest",
    "select_target",
]

CASH = "CASH/EUR"
ABS_FILTERS = ("none", "ema200", "roc60", "dual")
DEFAULT_UNIVERSE = ("BTC/EUR", "ETH/EUR", "SOL/EUR")
DAY_MS = 86_400_000
EMA_PERIOD = 200
MOMENTUM_SHORT = 30
MOMENTUM_LONG = 60


def _d(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _close_time(open_ms: int) -> datetime:
    return datetime.fromtimestamp((open_ms + DAY_MS) / 1000, tz=timezone.utc)


@dataclass(frozen=True, slots=True)
class Momentum:
    """Force relative d'un actif à une date de réévaluation."""

    symbol: str
    ret_short: Decimal
    ret_long: Decimal
    score: Decimal
    above_ema: bool

    def passes(self, abs_filter: str = "dual") -> bool:
        """Le leader valide-t-il le filtre absolu demandé ?"""
        if abs_filter == "none":
            return True
        if abs_filter == "ema200":
            return self.above_ema
        if abs_filter == "roc60":
            return self.ret_long > 0
        if abs_filter == "dual":
            return self.above_ema and self.ret_long > 0
        raise ValueError(
            f"filtre absolu inconnu : {abs_filter!r} "
            f"(connus : {', '.join(ABS_FILTERS)})"
        )


@dataclass(frozen=True, slots=True)
class Rotation:
    """Un changement d'allocation, frais et slippage compris."""

    at: datetime
    from_symbol: str
    to_symbol: str
    equity_before: Decimal
    equity_after: Decimal
    fee_quote: Decimal
    scores: tuple[Momentum, ...]


@dataclass(frozen=True, slots=True)
class RotationResult:
    """Résultat complet d'un rejeu de rotation."""

    universe: tuple[str, ...]
    period_start: datetime
    period_end: datetime
    candles: int
    equity_start: Decimal
    equity_end: Decimal
    max_drawdown_pct: Decimal
    rotations: tuple[Rotation, ...]
    fees_quote: Decimal
    days_in_market: int
    days_in_cash: int
    buy_and_hold: tuple[tuple[str, Decimal], ...]
    final_symbol: str
    yearly: tuple[tuple[int, int, int], ...] = ()
    equity_curve: tuple[tuple[datetime, Decimal], ...] = ()

    @property
    def net_pnl(self) -> Decimal:
        return self.equity_end - self.equity_start

    @property
    def return_pct(self) -> Decimal:
        if self.equity_start == 0:
            return Decimal(0)
        return self.net_pnl / self.equity_start

    @property
    def years(self) -> Decimal:
        seconds = (self.period_end - self.period_start).total_seconds()
        return max(Decimal(str(seconds)) / Decimal(31_557_600), Decimal("0.01"))

    @property
    def rotations_per_year(self) -> Decimal:
        return Decimal(len(self.rotations)) / self.years

    @property
    def exposure_pct(self) -> Decimal:
        total = self.days_in_market + self.days_in_cash
        return Decimal(self.days_in_market) / Decimal(total) if total else Decimal(0)

    @property
    def cash_pct(self) -> Decimal:
        total = self.days_in_market + self.days_in_cash
        return Decimal(self.days_in_cash) / Decimal(total) if total else Decimal(0)

    @property
    def rotations_to_cash(self) -> int:
        return sum(1 for rotation in self.rotations if rotation.to_symbol == CASH)

    @property
    def rotations_to_crypto(self) -> int:
        return sum(1 for rotation in self.rotations if rotation.to_symbol != CASH)

    def cash_pct_for_year(self, year: int) -> Decimal | None:
        """Part de jours en cash pour une année mesurée, si elle existe."""
        for current_year, market_days, cash_days in self.yearly:
            if current_year == year:
                total = market_days + cash_days
                return Decimal(cash_days) / Decimal(total) if total else Decimal(0)
        return None


def evaluate_momentum(
    closes: Sequence[Decimal],
    ema: Sequence[Decimal],
    index: int,
    *,
    symbol: str,
    short: int = MOMENTUM_SHORT,
    long: int = MOMENTUM_LONG,
) -> Momentum:
    """Rendements 30/60 D1 et position relative à l'EMA200, sans futur."""
    if index < long:
        raise ValueError(f"indice {index} trop court pour un momentum {long} D1")
    current = closes[index]
    ret_short = current / closes[index - short] - Decimal(1)
    ret_long = current / closes[index - long] - Decimal(1)
    score = (ret_short + ret_long) / Decimal(2)
    return Momentum(
        symbol=symbol,
        ret_short=ret_short,
        ret_long=ret_long,
        score=score,
        above_ema=current > ema[index],
    )


def select_target(scores: Sequence[Momentum], abs_filter: str = "none") -> str:
    """Choisit le leader relatif, puis applique le filtre absolu.

    Le leader est toujours le score le plus élevé ; le filtre absolu ne le
    remplace jamais par le deuxième. S'il échoue, le portefeuille va en cash.
    """
    if abs_filter not in ABS_FILTERS:
        raise ValueError(
            f"filtre absolu inconnu : {abs_filter!r} "
            f"(connus : {', '.join(ABS_FILTERS)})"
        )
    if not scores:
        raise ValueError("aucun score de momentum à comparer")
    leader = max(scores, key=lambda item: item.score)
    return leader.symbol if leader.passes(abs_filter) else CASH


def _series(candles: Sequence[Sequence[Any]]) -> tuple[dict[int, list], list[Decimal], list[Decimal]]:
    if len(candles) < EMA_PERIOD:
        raise ValueError(f"historique insuffisant : {len(candles)} bougies D1")
    ordered = sorted(candles, key=lambda candle: int(candle[0]))
    by_time = {int(candle[0]): candle for candle in ordered}
    closes = [_d(candle[4]) for candle in ordered]
    ema = _ema_series(closes, EMA_PERIOD)
    return by_time, closes, ema


def run_rotation_backtest(
    *,
    candles_by_symbol: dict[str, Sequence[Sequence[Any]]],
    equity_start: Decimal,
    universe: Sequence[str] = DEFAULT_UNIVERSE,
    fee_bps: Decimal = Decimal("25"),
    slippage_bps: int = 5,
    cadence_days: int = 7,
    momentum_short: int = MOMENTUM_SHORT,
    momentum_long: int = MOMENTUM_LONG,
    abs_filter: str = "none",
    start_at: datetime | None = None,
) -> RotationResult:
    """Rejoue la rotation sur des bougies D1 alignées par date d'ouverture.

    ``start_at`` sert à charger de l'historique avant la fenêtre mesurée pour
    l'EMA200 et les momentum, sans compter ces jours dans le résultat.
    """
    if cadence_days <= 0:
        raise ValueError("cadence_days doit être positif")
    if abs_filter not in ABS_FILTERS:
        raise ValueError(
            f"filtre absolu inconnu : {abs_filter!r} "
            f"(connus : {', '.join(ABS_FILTERS)})"
        )
    if momentum_short <= 0 or momentum_long <= 0 or momentum_short >= momentum_long:
        raise ValueError("il faut 0 < momentum_short < momentum_long")
    symbols = tuple(universe)
    if not symbols:
        raise ValueError("univers vide")
    missing = [symbol for symbol in symbols if symbol not in candles_by_symbol]
    if missing:
        raise ValueError(f"bougies manquantes : {', '.join(missing)}")

    fee_rate = _d(fee_bps) / Decimal(10_000)
    slippage = Decimal(slippage_bps) / Decimal(10_000)
    prepared = {symbol: _series(candles_by_symbol[symbol]) for symbol in symbols}
    common = sorted(
        set.intersection(*(set(prepared[symbol][0]) for symbol in symbols))
    )
    if len(common) < max(EMA_PERIOD, momentum_long) + 1:
        raise ValueError("dates communes insuffisantes pour la rotation")

    closes = {symbol: [_d(prepared[symbol][0][at][4]) for at in common]
              for symbol in symbols}
    # L'EMA est calculée sur la série propre du symbole : on la réaligne par
    # date, pas par indice, pour qu'un jour manquant ne décale pas la moyenne.
    ema: dict[str, list[Decimal]] = {}
    for symbol in symbols:
        by_time, _, series_ema = prepared[symbol]
        ordered = sorted(by_time.values(), key=lambda candle: int(candle[0]))
        positions = {int(candle[0]): i for i, candle in enumerate(ordered)}
        ema[symbol] = [series_ema[positions[at]] for at in common]

    warmup = max(EMA_PERIOD, momentum_long)
    first = warmup
    if start_at is not None:
        target_ms = int(start_at.timestamp() * 1000)
        first = max(first, bisect_left(common, target_ms))
    last = len(common) - 1
    if last <= first:
        raise ValueError("fenêtre de rotation vide")

    equity = _d(equity_start)
    units = Decimal(0)
    current = CASH
    fees = Decimal(0)
    days_in_market = 0
    days_in_cash = 0
    yearly_market: dict[int, int] = {}
    yearly_cash: dict[int, int] = {}
    rotations: list[Rotation] = []
    curve: list[tuple[datetime, Decimal]] = [(_close_time(common[first - 1]), equity)]

    for index in range(first, last + 1):
        if (index - first) % cadence_days == 0:
            scores = tuple(
                evaluate_momentum(
                    closes[symbol], ema[symbol], index, symbol=symbol,
                    short=momentum_short, long=momentum_long,
                )
                for symbol in symbols
            )
            target = select_target(scores, abs_filter)
            if target != current:
                before = equity
                fee_total = Decimal(0)
                if current != CASH:
                    price = closes[current][index] * (Decimal(1) - slippage)
                    notional = units * price
                    fee = notional * fee_rate
                    equity = notional - fee
                    fee_total += fee
                    units = Decimal(0)
                if target != CASH:
                    price = closes[target][index] * (Decimal(1) + slippage)
                    notional = equity / (Decimal(1) + fee_rate)
                    fee = notional * fee_rate
                    units = notional / price
                    equity = units * price
                    fee_total += fee
                fees += fee_total
                rotations.append(Rotation(
                    at=_close_time(common[index]),
                    from_symbol=current,
                    to_symbol=target,
                    equity_before=before,
                    equity_after=equity,
                    fee_quote=fee_total,
                    scores=scores,
                ))
                current = target

        day = _close_time(common[index])
        if current == CASH:
            days_in_cash += 1
            yearly_cash[day.year] = yearly_cash.get(day.year, 0) + 1
        else:
            equity = units * closes[current][index]
            days_in_market += 1
            yearly_market[day.year] = yearly_market.get(day.year, 0) + 1
        curve.append((day, equity))

    final_symbol = current
    if current != CASH:
        price = closes[current][last] * (Decimal(1) - slippage)
        notional = units * price
        fee = notional * fee_rate
        equity = notional - fee
        fees += fee
        curve[-1] = (_close_time(common[last]), equity)

    returns = tuple(
        (symbol, closes[symbol][last] / closes[symbol][first] - Decimal(1))
        for symbol in symbols
    )
    return RotationResult(
        universe=symbols,
        period_start=_close_time(common[first]),
        period_end=_close_time(common[last]),
        candles=last - first + 1,
        equity_start=_d(equity_start),
        equity_end=equity,
        max_drawdown_pct=_max_drawdown(curve),
        rotations=tuple(rotations),
        fees_quote=fees,
        days_in_market=days_in_market,
        days_in_cash=days_in_cash,
        buy_and_hold=returns,
        final_symbol=final_symbol,
        yearly=tuple(
            (year, yearly_market.get(year, 0), yearly_cash.get(year, 0))
            for year in sorted(set(yearly_market) | set(yearly_cash))
        ),
        equity_curve=tuple(curve),
    )
