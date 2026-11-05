"""Tests du rejeu swing Daily : entrée, stop structurel, breakeven, sortie.

Bougies synthétiques, aucun réseau, aucune base. Ces tests vérifient la mécanique
du rejeu (pas de lookahead, stop prioritaire, frais), pas la rentabilité.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from app.backtest.swing import run_swing_backtest
from app.engine.risk_gate import (
    EntryMode, ExchangeConstraints, RiskConfig, StopMode, SwingExitMode,
)
from app.exchange.kraken import KrakenMarketInfo

START = datetime(2020, 1, 1, tzinfo=timezone.utc)
D1_MS = 86_400_000
ENTRY_INDEX = 340


def _candle(index: int, open_: float, high: float, low: float, close: float) -> list:
    stamp = int((START + timedelta(milliseconds=D1_MS * index)).timestamp() * 1000)
    return [stamp, open_, high, low, close, 10.0]


def d1_series(crash: bool = False) -> list[list]:
    """Tendance longue, consolidation, puis cassure Donchian au jour 340."""
    candles: list[list] = []
    for i in range(ENTRY_INDEX):
        if i < 300:
            price = 100.0 + i * (100.0 / 300.0)
        else:
            price = 200.0 + 0.2 * math.sin(i)
        candles.append(_candle(i, price * 0.999, price * 1.005, price * 0.995, price))
    # Cassure : clôture 210 au-dessus du plus haut des 20 jours (≈202).
    candles.append(_candle(ENTRY_INDEX, 205, 212, 204, 210))
    if crash:
        # Touche +1 R et le stop dans la même bougie : le stop doit gagner.
        candles.append(_candle(ENTRY_INDEX + 1, 210, 230, 190, 220))
        candles.append(_candle(ENTRY_INDEX + 2, 220, 222, 218, 221))
    else:
        # +1 R atteint (entry ≈210, stop ≈199,5 → seuil ≈220,5), puis repli
        # sous le seuil de rentabilité sans toucher le stop initial.
        candles.append(_candle(ENTRY_INDEX + 1, 210, 226, 208, 222))
        candles.append(_candle(ENTRY_INDEX + 2, 215, 216, 210, 212))
        candles.append(_candle(ENTRY_INDEX + 3, 212, 213, 208, 209))
    return candles


def market_info() -> KrakenMarketInfo:
    return KrakenMarketInfo(
        symbol="BTC/EUR", exchange_symbol="XXBTZEUR", base="BTC", quote="EUR",
        active=True, taker_fee=Decimal("0.008"), maker_fee=Decimal("0.004"),
        constraints=ExchangeConstraints(
            amount_step=Decimal("0.00000001"), min_amount=Decimal("0.00001"),
            min_notional=Decimal("1"), price_tick=Decimal("0.01"),
        ),
    )


def settings(exit_mode: SwingExitMode = SwingExitMode.EMA20) -> SimpleNamespace:
    return SimpleNamespace(
        risk=RiskConfig(
            entry_mode=EntryMode.SWING_MACRO_D1,
            stop_mode=StopMode.STRUCTURAL_D1,
            take_profit_r_multiple=Decimal("0"),
            risk_per_trade_pct=Decimal("0.005"),
            max_notional_pct=Decimal("0.50"),
            min_confidence=Decimal("0.60"),
            swing_exit_mode=exit_mode,
        ),
        paper_start_equity=Decimal("1000"),
        paper_slippage_bps=5,
        llm=SimpleNamespace(model="stub"),
    )


def test_swing_enters_on_donchian_breakout_and_exits_in_profit():
    result = run_swing_backtest(
        symbol="BTC/EUR", info=market_info(), d1=d1_series(),
        settings=settings(), start_index=ENTRY_INDEX,
    )
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.entry_at == datetime(2020, 1, 1, tzinfo=timezone.utc) + timedelta(
        days=ENTRY_INDEX + 1
    )
    assert trade.entry_price > Decimal("210")
    # Stop structurel : jamais à plus de 5 % sous l'entrée (le fill inclut le
    # slippage, le stop est calculé sur le prix de décision).
    assert trade.stop_loss_price >= trade.entry_price * Decimal("0.949")
    assert trade.exit_at >= trade.entry_at
    assert trade.fees_quote > 0
    assert trade.reason in {"STOP_LOSS", "TREND_EXIT_EMA20", "TREND_EXIT_BOLLINGER"}


def test_swing_stop_wins_over_take_profit_in_the_same_candle():
    result = run_swing_backtest(
        symbol="BTC/EUR", info=market_info(), d1=d1_series(crash=True),
        settings=settings(), start_index=ENTRY_INDEX,
    )
    assert len(result.trades) == 1
    assert result.trades[0].reason == "STOP_LOSS"
    assert result.trades[0].pnl_quote < 0


def test_swing_breakeven_moves_the_stop_after_one_r():
    result = run_swing_backtest(
        symbol="BTC/EUR", info=market_info(), d1=d1_series(),
        settings=settings(), start_index=ENTRY_INDEX,
    )
    trade = result.trades[0]
    # Le stop initial est sous 200 ; la sortie se fait au seuil de rentabilité
    # (~entrée + frais), pas au stop initial. Une sortie au stop initial serait
    # bien plus basse.
    assert trade.reason == "STOP_LOSS"
    assert trade.exit_price > trade.stop_loss_price
    assert trade.exit_price >= trade.entry_price


def test_swing_does_not_enter_before_the_requested_start():
    result = run_swing_backtest(
        symbol="BTC/EUR", info=market_info(), d1=d1_series(),
        settings=settings(), start_index=ENTRY_INDEX - 5,
    )
    assert result.trades
    assert result.trades[0].entry_at >= datetime(
        2020, 1, 1, tzinfo=timezone.utc
    ) + timedelta(days=ENTRY_INDEX - 4)


def test_swing_refuses_a_series_too_short():
    import pytest

    with pytest.raises(ValueError, match="insuffisant"):
        run_swing_backtest(
            symbol="BTC/EUR", info=market_info(), d1=d1_series()[:100],
            settings=settings(),
        )


def chandelier_series() -> list[list]:
    """Après la cassure, une dérive baissière lente : les mèches restent au-dessus
    du stop structurel, mais la clôture passe sous le Chandelier."""
    candles = d1_series()[: ENTRY_INDEX + 1]
    candles.append(_candle(ENTRY_INDEX + 1, 210, 211, 208, 209))
    candles.append(_candle(ENTRY_INDEX + 2, 209, 210, 205, 206))
    candles.append(_candle(ENTRY_INDEX + 3, 206, 207, 201, 202))
    return candles


def test_swing_chandelier_exits_on_a_close_below_the_trailing_stop():
    result = run_swing_backtest(
        symbol="BTC/EUR", info=market_info(), d1=chandelier_series(),
        settings=settings(SwingExitMode.CHANDELIER), start_index=ENTRY_INDEX,
    )
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.reason == "TREND_EXIT_CHANDELIER"
    assert trade.exit_at == START + timedelta(days=ENTRY_INDEX + 4)
    # La sortie se fait à la clôture (202) moins le slippage, pas au stop.
    assert Decimal("201.8") < trade.exit_price < Decimal("202")
    assert trade.exit_price > trade.stop_loss_price


def test_swing_default_mode_does_not_use_the_chandelier():
    result = run_swing_backtest(
        symbol="BTC/EUR", info=market_info(), d1=chandelier_series(),
        settings=settings(), start_index=ENTRY_INDEX,
    )
    assert result.trades
    assert result.trades[0].reason != "TREND_EXIT_CHANDELIER"
