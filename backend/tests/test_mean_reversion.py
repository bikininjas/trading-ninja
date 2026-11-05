"""Tests du laboratoire Mean Reversion — aucun réseau, aucun ordre."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.backtest.mean_reversion import (
    WARMUP,
    evaluate_mean_reversion,
    run_mean_reversion_backtest,
)
from app.core.settings import load_settings
from app.engine.risk_gate import ExchangeConstraints

SYMBOL = "BTC/EUR"
STEP_MS = 4 * 3_600_000
START_MS = 1_700_000_000_000


def flat_candles(count: int = WARMUP + 40) -> list[list]:
    """Marché plat : ATR 2, aucune bande franchie, volume constant."""
    out = []
    for index in range(count):
        out.append([
            START_MS + index * STEP_MS,
            "100", "101", "99", "100", "10",
        ])
    return out


def dip_candles(*, recovery_close: str = "99.9") -> list[list]:
    """Une seule entrée : chute à 97 sous la bande basse, puis retour à la médiane."""
    candles = flat_candles()
    index = WARMUP
    candles[index] = [
        START_MS + index * STEP_MS, "100", "101", "96.5", "97", "1000",
    ]
    candles[index + 1] = [
        START_MS + (index + 1) * STEP_MS, "97", "100", "97", recovery_close, "50",
    ]
    return candles


def settings_for(tmp_path, **overrides):
    values = {
        "RISK_PROFILE": "prudent",
        "PAPER_START_EQUITY": "1000",
        "TRADING_SYMBOL": SYMBOL,
        "RUNTIME_CONFIG_PATH": str(tmp_path / "runtime.json"),
    }
    values.update(overrides)
    return load_settings(env_file=tmp_path / "absent.env", overrides=values)


def info() -> SimpleNamespace:
    return SimpleNamespace(
        taker_fee=Decimal("0.0026"),
        constraints=ExchangeConstraints(
            amount_step=Decimal("0.00000001"),
            min_amount=Decimal("0.00006"),
            min_notional=Decimal("0.45"),
            price_tick=Decimal("0.1"),
        ),
    )


def test_the_setup_requires_all_three_conditions():
    candles = dip_candles()
    from app.backtest.mean_reversion import MeanReversionSeries

    series = MeanReversionSeries(candles)
    assert evaluate_mean_reversion(series, WARMUP) is not None
    # Volume redevenu normal : pas de signal.
    candles[WARMUP][5] = "10"
    series = MeanReversionSeries(candles)
    assert evaluate_mean_reversion(series, WARMUP) is None
    # RSI remonté : pas de signal non plus (le seuil est strict).
    assert evaluate_mean_reversion(series, WARMUP, rsi_max=Decimal("0")) is None


def test_the_gate_refuses_a_counter_trend_entry_and_the_lab_can_measure_it(tmp_path):
    candles = dip_candles()

    gated = run_mean_reversion_backtest(
        symbol=SYMBOL, info=info(), candles=candles,
        settings=settings_for(tmp_path), exit_mode="touch",
        allow_counter_trend=False,
    )
    assert gated.trades == []
    assert gated.rejections["REJECTED_COUNTER_TREND_EMA200"] == 1

    raw = run_mean_reversion_backtest(
        symbol=SYMBOL, info=info(), candles=candles,
        settings=settings_for(tmp_path), exit_mode="touch",
        allow_counter_trend=True,
    )
    assert len(raw.trades) == 1
    assert raw.trades[0].reason == "MID_BAND_TOUCH"
    assert raw.trades[0].pnl_quote > 0


def test_the_exit_can_wait_for_a_close_above_the_middle_band(tmp_path):
    # 99,75 reste sous la médiane (~99,84) : la sortie « touch » se déclenche,
    # la sortie « close » attend la bougie suivante.
    candles = dip_candles(recovery_close="99.75")
    candles[WARMUP + 2] = [
        START_MS + (WARMUP + 2) * STEP_MS, "99.75", "100", "99.5", "99.9", "20",
    ]

    touch = run_mean_reversion_backtest(
        symbol=SYMBOL, info=info(), candles=candles,
        settings=settings_for(tmp_path), exit_mode="touch",
        allow_counter_trend=True,
    )
    close = run_mean_reversion_backtest(
        symbol=SYMBOL, info=info(), candles=candles,
        settings=settings_for(tmp_path), exit_mode="close",
        allow_counter_trend=True,
    )

    assert touch.trades[0].reason == "MID_BAND_TOUCH"
    assert close.trades[0].reason == "MID_BAND_CLOSE"


def test_an_invalid_exit_mode_is_refused(tmp_path):
    with pytest.raises(ValueError, match="exit_mode inconnu"):
        run_mean_reversion_backtest(
            symbol=SYMBOL, info=info(), candles=dip_candles(),
            settings=settings_for(tmp_path), exit_mode="magic",
        )
