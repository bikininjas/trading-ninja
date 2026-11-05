"""Tests du laboratoire de rotation — aucun réseau, aucun ordre."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.backtest.rotation import (
    CASH,
    Momentum,
    evaluate_momentum,
    run_rotation_backtest,
    select_target,
)

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


def ramp_then_flat(base: int = 100, slope: str = "0.5",
                   plateau_at: int = 260, count: int = 340) -> list[list]:
    """Monte linéairement puis plafonne : le ROC60 finit par tomber à zéro."""
    def price(index: int) -> Decimal:
        if index < plateau_at:
            return Decimal(base) + Decimal(index) * Decimal(slope)
        return Decimal(base) + Decimal(plateau_at) * Decimal(slope)

    return candles(price, count=count)


def test_none_holds_the_least_bad_asset_below_ema():
    declining = candles(lambda index: 1000 - Decimal(index) * Decimal("3"))
    result = run_rotation_backtest(
        candles_by_symbol=universe(declining, declining, declining),
        equity_start=Decimal("1000"), cadence_days=7, abs_filter="none",
    )

    assert result.rotations
    assert result.final_symbol == "BTC/EUR"
    assert result.cash_pct == 0
    assert result.rotations_to_cash == 0


def test_all_assets_below_their_ema_stay_in_cash():
    declining = candles(lambda index: 1000 - Decimal(index) * Decimal("3"))
    result = run_rotation_backtest(
        candles_by_symbol=universe(declining, declining, declining),
        equity_start=Decimal("1000"), cadence_days=7, abs_filter="ema200",
    )

    assert result.rotations == ()
    assert result.final_symbol == CASH
    assert result.equity_end == Decimal("1000")
    assert result.days_in_market == 0
    assert result.exposure_pct == 0
    assert result.cash_pct == 1


def test_the_strongest_asset_above_its_ema_takes_the_whole_allocation():
    btc = candles(lambda index: 100 + Decimal(index) * Decimal("0.1"))
    eth = candles(lambda index: 100 + Decimal(index) * Decimal("0.3"))
    sol = candles(lambda index: 300 - Decimal(index) * Decimal("0.3"))

    result = run_rotation_backtest(
        candles_by_symbol=universe(btc, eth, sol),
        equity_start=Decimal("1000"), cadence_days=7, abs_filter="ema200",
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
        cadence_days=7, abs_filter="ema200",
    )
    paid = run_rotation_backtest(
        candles_by_symbol=universe(btc, eth, sol),
        equity_start=Decimal("1000"), fee_bps=Decimal(25), slippage_bps=0,
        cadence_days=7, abs_filter="ema200",
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
        equity_start=Decimal("1000"), cadence_days=7, abs_filter="ema200",
    )

    assert any(rotation.to_symbol == CASH for rotation in result.rotations)
    assert result.final_symbol == CASH


def test_dual_momentum_fallback_to_cash_on_negative_roc():
    leader = Momentum(
        symbol="BTC/EUR", ret_short=Decimal("0.04"),
        ret_long=Decimal("-0.05"), score=Decimal("-0.005"), above_ema=True,
    )
    other = Momentum(
        symbol="ETH/EUR", ret_short=Decimal("-0.02"),
        ret_long=Decimal("-0.03"), score=Decimal("-0.025"), above_ema=True,
    )

    assert leader.score > other.score
    assert select_target([leader, other], "dual") == CASH


def test_dual_momentum_fallback_to_cash_on_sub_ema200():
    leader = Momentum(
        symbol="BTC/EUR", ret_short=Decimal("0.08"),
        ret_long=Decimal("0.05"), score=Decimal("0.065"), above_ema=False,
    )
    other = Momentum(
        symbol="ETH/EUR", ret_short=Decimal("0.01"),
        ret_long=Decimal("0.02"), score=Decimal("0.015"), above_ema=True,
    )

    assert leader.score > other.score
    assert select_target([leader, other], "dual") == CASH


def test_dual_momentum_allocates_leader_when_valid():
    leader = Momentum(
        symbol="BTC/EUR", ret_short=Decimal("0.08"),
        ret_long=Decimal("0.05"), score=Decimal("0.065"), above_ema=True,
    )
    other = Momentum(
        symbol="ETH/EUR", ret_short=Decimal("0.01"),
        ret_long=Decimal("0.02"), score=Decimal("0.015"), above_ema=True,
    )

    assert select_target([leader, other], "dual") == "BTC/EUR"


def test_fee_accounting_transitions():
    fee_rate = Decimal("25") / Decimal(10_000)

    # CASH -> CASH : aucun actif ne valide le filtre, aucun frais.
    declining = candles(lambda index: 1000 - Decimal(index) * Decimal("3"))
    flat = run_rotation_backtest(
        candles_by_symbol=universe(declining, declining, declining),
        equity_start=Decimal("1000"), fee_bps=Decimal("25"), slippage_bps=0,
        cadence_days=7, abs_filter="dual",
    )
    assert flat.rotations == ()
    assert flat.fees_quote == 0

    # CASH -> BTC (un achat), puis BTC -> CASH (une vente).
    series = ramp_then_flat()
    result = run_rotation_backtest(
        candles_by_symbol=universe(series, series, series),
        equity_start=Decimal("1000"), fee_bps=Decimal("25"), slippage_bps=0,
        cadence_days=7, abs_filter="dual",
    )
    assert result.rotations_to_crypto == 1
    assert result.rotations_to_cash == 1
    assert result.final_symbol == CASH

    entry = result.rotations[0]
    exit_ = result.rotations[1]
    assert entry.from_symbol == CASH
    assert entry.to_symbol == "BTC/EUR"
    assert exit_.from_symbol == "BTC/EUR"
    assert exit_.to_symbol == CASH
    # Achat : le notionnel est equity / (1 + taux), les frais portent dessus.
    assert entry.fee_quote == entry.equity_before * fee_rate / (1 + fee_rate)
    # Vente : un seul côté, frais sur la valeur de sortie.
    assert exit_.fee_quote == exit_.equity_before * fee_rate
    assert result.fees_quote == entry.fee_quote + exit_.fee_quote


def test_momentum_is_a_plain_average_of_the_two_windows():
    closes = [Decimal(index) for index in range(100)]
    ema = [Decimal("1")] * 100
    momentum = evaluate_momentum(
        closes, ema, 90, symbol="BTC/EUR", short=30, long=60,
    )

    assert momentum.ret_short == Decimal(90) / Decimal(60) - 1
    assert momentum.ret_long == Decimal(90) / Decimal(30) - 1
    assert momentum.score == (momentum.ret_short + momentum.ret_long) / 2
    assert momentum.passes("ema200") is True
    assert momentum.passes("roc60") is True
    assert momentum.passes("dual") is True


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


def test_an_invalid_abs_filter_is_refused():
    with pytest.raises(ValueError, match="filtre absolu"):
        run_rotation_backtest(
            candles_by_symbol={
                symbol: candles(lambda index: 100 + index)
                for symbol in UNIVERSE
            },
            equity_start=Decimal("1000"),
            abs_filter="bogus",
        )
