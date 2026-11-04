"""Tests du moteur de backtest : absence de lookahead, stop prioritaire, frais.

Les bougies sont synthétiques et les décisions viennent d'un stub : aucun réseau,
aucun LLM, aucune base. Ces tests vérifient la mécanique du rejeu, pas la qualité
des décisions du modèle (c'est le rôle du golden enregistré).
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.backtest.engine import ReplayExchange, zone_is_open, run_backtest
from app.engine.risk_gate import ExchangeConstraints, RiskConfig
from app.exchange.kraken import KrakenMarketInfo

START = datetime(2026, 1, 1, tzinfo=timezone.utc)
H4_MS = 4 * 3_600_000


def candles(start: datetime, count: int, step_ms: int) -> list[list]:
    """Série qui monte, avec une oscillation pour éviter un RSI à 100."""
    out = []
    for i in range(count):
        base = 100.0 * (1 + 0.002 * i) * (1 + 0.01 * math.sin(i / 2))
        open_ = base * (1 - 0.002)
        close = base
        high = max(open_, close) * 1.004
        low = min(open_, close) * 0.996
        timestamp = int((start + timedelta(milliseconds=step_ms * i)).timestamp() * 1000)
        out.append([timestamp, open_, high, low, close, 10.0])
    return out


def h1_series(count: int = 2000) -> list[list]:
    start = START - timedelta(days=10)
    out = []
    for i in range(count):
        base = 100.0 * (1 + 0.0002 * i) * (1 + 0.003 * math.sin(i))
        open_ = base * (1 - 0.001)
        close = base
        timestamp = int((start + timedelta(hours=i)).timestamp() * 1000)
        out.append([timestamp, open_, max(open_, close) * 1.001,
                    min(open_, close) * 0.999, close, 5.0])
    return out


def market_info() -> KrakenMarketInfo:
    return KrakenMarketInfo(
        symbol="BTC/EUR",
        exchange_symbol="XXBTZEUR",
        base="BTC",
        quote="EUR",
        active=True,
        taker_fee=Decimal("0.0026"),
        maker_fee=Decimal("0.0016"),
        constraints=ExchangeConstraints(
            amount_step=Decimal("0.00000001"),
            min_amount=Decimal("0.00001"),
            min_notional=Decimal("1"),
            price_tick=Decimal("0.01"),
        ),
    )


def settings() -> SimpleNamespace:
    return SimpleNamespace(
        risk=RiskConfig(
            risk_per_trade_pct=Decimal("0.0175"),
            max_notional_pct=Decimal("0.50"),
            min_confidence=Decimal("0.85"),
        ),
        paper_start_equity=Decimal("1000"),
        paper_slippage_bps=5,
        llm=SimpleNamespace(model="stub"),
    )


class StubSignal:
    def __init__(self, action="HOLD", confidence="0.9", regime="TRENDING_UP",
                 thesis="stub", degraded=False, reason=""):
        self.action = action
        self.confidence = Decimal(confidence)
        self.market_regime = regime
        self.directional_bias = "NEUTRAL"
        self.thesis = thesis
        self.inference_ms = 1
        self.degraded = degraded
        self.degradation_reason = reason


class StubClient:
    """Répond BUY à un index donné, HOLD ailleurs. Enregistre ce qu'il a vu."""

    def __init__(self, buy_at: datetime | None = None, price_spy: list | None = None):
        self.buy_at = buy_at
        self.price_spy = price_spy if price_spy is not None else []
        self.calls: list[datetime] = []

    async def __call__(self, snapshot, context):
        self.calls.append(snapshot.tick_at)
        self.price_spy.append((snapshot.tick_at, snapshot.price))
        if self.buy_at is not None and snapshot.tick_at == self.buy_at:
            return StubSignal("BUY")
        return StubSignal("HOLD")


def close_time(candle: list) -> datetime:
    return datetime.fromtimestamp((candle[0] + H4_MS) / 1000, tz=timezone.utc)


@pytest.mark.asyncio
async def test_no_lookahead_the_snapshot_stops_at_the_decision_candle():
    h4 = candles(START, 260, H4_MS)
    h1 = h1_series()
    # Un pic énorme juste après la décision : il ne doit pas être visible.
    h4[251][2] = 10_000.0
    h4[251][4] = 9_000.0

    spy: list = []
    client = StubClient(buy_at=None, price_spy=spy)
    result = await run_backtest(
        symbol="BTC/EUR", info=market_info(), h4=h4, h1=h1, settings=settings(),
        propose=client, start_index=250, end_index=250,
    )

    assert spy, "le modèle doit être appelé dans la zone"
    assert all(price < 200 for _, price in spy), f"prix futur visible : {spy}"
    assert spy[0][1] == Decimal(str(h4[250][4]))
    assert result.decisions >= 1


@pytest.mark.asyncio
async def test_take_profit_is_reached_and_fees_are_paid():
    h4 = candles(START, 260, H4_MS)
    h1 = h1_series()
    entry_at = close_time(h4[250])
    # La bougie suivante monte au-dessus du TP (2R = 5 ATR au-dessus de l'entrée).
    h4[251][2] = h4[250][4] * 1.10
    h4[251][3] = h4[250][4] * 0.999
    h4[251][4] = h4[250][4] * 1.09

    result = await run_backtest(
        symbol="BTC/EUR", info=market_info(), h4=h4, h1=h1, settings=settings(),
        propose=StubClient(buy_at=entry_at), start_index=250, end_index=255,
    )

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.reason == "TAKE_PROFIT"
    assert trade.pnl_quote > 0
    assert trade.fees_quote > 0
    # L'entrée paie la clôture plus 5 bps de slippage.
    assert trade.entry_price == Decimal(str(h4[250][4])) * Decimal("1.0005")
    assert result.equity_end > result.equity_start


@pytest.mark.asyncio
async def test_stop_wins_when_a_candle_touches_both_levels():
    h4 = candles(START, 260, H4_MS)
    h1 = h1_series()
    entry_at = close_time(h4[250])
    price = h4[250][4]
    h4[251][2] = price * 1.20   # au-dessus du TP
    h4[251][3] = price * 0.80   # sous le stop
    h4[251][4] = price

    result = await run_backtest(
        symbol="BTC/EUR", info=market_info(), h4=h4, h1=h1, settings=settings(),
        propose=StubClient(buy_at=entry_at), start_index=250, end_index=255,
    )

    assert len(result.trades) == 1
    assert result.trades[0].reason == "STOP_LOSS"
    assert result.trades[0].pnl_quote < 0


@pytest.mark.asyncio
async def test_hold_does_not_open_a_position_but_feeds_the_counterfactual():
    h4 = candles(START, 260, H4_MS)
    h1 = h1_series()
    # Bougie suivante neutre : le contrefactuel doit produire un trade fictif.
    result = await run_backtest(
        symbol="BTC/EUR", info=market_info(), h4=h4, h1=h1, settings=settings(),
        propose=StubClient(buy_at=None), start_index=250, end_index=252,
    )
    assert result.trades == []
    assert result.approvals == 0
    assert result.decisions >= 1
    assert result.counterfactuals, "un HOLD dans la zone doit être évalué à l'envers"


@pytest.mark.asyncio
async def test_zone_rejects_below_ema200():
    h4 = candles(START, 260, H4_MS)
    h1 = h1_series()
    # Dernière clôture loin sous l'EMA200 : le modèle ne doit pas être appelé.
    for i in range(240, 260):
        h4[i][4] = 50.0
        h4[i][3] = 49.0
        h4[i][2] = 51.0
    spy: list = []
    await run_backtest(
        symbol="BTC/EUR", info=market_info(), h4=h4, h1=h1, settings=settings(),
        propose=StubClient(price_spy=spy), start_index=250, end_index=255,
    )
    assert spy == []


def test_zone_is_open_matches_the_client_rules():
    snap = SimpleNamespace(price=Decimal("100"), ema_200_h4=Decimal("99"),
                           atr_14_h4=Decimal("1"))
    ctx = SimpleNamespace(rsi_14_h4=Decimal("50"))
    assert zone_is_open(snap, ctx, settings().risk) is True
    ctx_hot = SimpleNamespace(rsi_14_h4=Decimal("75"))
    assert zone_is_open(snap, ctx_hot, settings().risk) is False


def test_replay_exchange_only_serves_closed_candles():
    h4 = candles(START, 10, H4_MS)
    replay = ReplayExchange(h4, h1_series(50))
    replay.set_now(h4[5][0] + H4_MS + 1)
    served = replay.fetch_ohlcv("BTC/EUR", "4h", limit=100)
    assert len(served) == 6
    assert served[-1][0] == h4[5][0]
