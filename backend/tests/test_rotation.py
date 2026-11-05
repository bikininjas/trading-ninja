"""Tests du laboratoire de rotation — aucun réseau, aucun ordre."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.backtest.rotation import CASH, evaluate_momentum, run_rotation_backtest

DAY_MS = 86_400_000
START_MS = 1_600_000_000_000
UNIVERSE = ("BTC/EUR", "ETH/EUR", "SOL/EUR")


def candles(price, count: int = 320) -> list[list]:
    return [
        [START_MS + index * DAY_MS, str(price(index)), str(price(index)),
         str(price(index)), str(price(index)), "1"]
        for index in range(count)
    ]


def universe(btc, eth, sol) -> dict[str, list[list]]:
    return {"BTC/EUR": btc, "ETH/EUR": eth, "SOL/EUR": sol}


def test_all_assets_below_their_ema_stay_in_cash():
    declining = candles(lambda index: 1000 - Decimal(index) * Decimal("3"))
    result = run_rotation_backtest(
        candles_by_symbol=universe(declining, declining, declining),
        equity_start=Decimal("1000"), cadence_days=7,
    )

    assert result.rotations == ()
    assert result.final_symbol == CASH
    assert result.equity_end == Decimal("1000")
    assert result.days_in_market == 0
    assert result.exposure_pct == 0


def test_the_strongest_asset_above_its_ema_takes_the_whole_allocation():
    btc = candles(lambda index: 100 + Decimal(index) * Decimal("0.1"))
    eth = candles(lambda index: 100 + Decimal(index) * Decimal("0.3"))
    sol = candles(lambda index: 300 - Decimal(index) * Decimal("0.3"))

    result = run_rotation_backtest(
        candles_by_symbol=universe(btc, eth, sol),
        equity_start=Decimal("1000"), cadence_days=7,
    )

    assert len(result.rotations) == 1
    rotation = result.rotations[0]
    assert rotation.from_symbol == CASH
    assert rotation.to_symbol == "ETH/EUR"
    assert rotation.equity_after < rotation.equity_before  # frais + slippage
    assert result.final_symbol == "ETH/EUR"
    assert result.exposure_pct == 1


def test_fees_reduce_the_final_equity():
    btc = candles(lambda index: 100 + Decimal(index) * Decimal("0.1"))
    eth = candles(lambda index: 100 + Decimal(index) * Decimal("0.3"))
    sol = candles(lambda index: 300 - Decimal(index) * Decimal("0.3"))

    free = run_rotation_backtest(
        candles_by_symbol=universe(btc, eth, sol),
        equity_start=Decimal("1000"), fee_bps=Decimal(0), slippage_bps=0,
        cadence_days=7,
    )
    paid = run_rotation_backtest(
        candles_by_symbol=universe(btc, eth, sol),
        equity_start=Decimal("1000"), fee_bps=Decimal(25), slippage_bps=0,
        cadence_days=7,
    )

    assert paid.equity_end < free.equity_end
    assert paid.fees_quote > 0


def test_a_crash_below_the_ema_sends_the_book_to_cash():
    def crash(base: int, slope: str):
        def price(index: int) -> Decimal:
            if index < 260:
                return Decimal(base) + Decimal(index) * Decimal(slope)
            return max(
                Decimal(10),
                Decimal(base) + Decimal(260) * Decimal(slope)
                - Decimal(index - 260) * Decimal("10"),
            )
        return candles(price)

    result = run_rotation_backtest(
        candles_by_symbol=universe(
            crash(100, "0.1"), crash(100, "0.5"), crash(100, "0.2"),
        ),
        equity_start=Decimal("1000"), cadence_days=7,
    )

    assert any(rotation.to_symbol == CASH for rotation in result.rotations)
    assert result.final_symbol == CASH


def test_momentum_is_a_plain_average_of_the_two_windows():
    closes = [Decimal(index) for index in range(100)]
    ema = [Decimal("1")] * 100
    momentum = evaluate_momentum(
        closes, ema, 90, symbol="BTC/EUR", short=30, long=60,
    )

    assert momentum.ret_short == Decimal(90) / Decimal(60) - 1
    assert momentum.ret_long == Decimal(90) / Decimal(30) - 1
    assert momentum.score == (momentum.ret_short + momentum.ret_long) / 2
    assert momentum.eligible is True


def test_an_invalid_window_is_refused():
    with pytest.raises(ValueError, match="momentum_short"):
        run_rotation_backtest(
            candles_by_symbol={
                symbol: candles(lambda index: 100 + index)
                for symbol in UNIVERSE
            },
            equity_start=Decimal("1000"),
            momentum_short=60,
            momentum_long=30,
        )
