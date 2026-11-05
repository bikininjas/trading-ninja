"""Métriques de validation : profit factor, espérance, gains et pertes moyens.

Ces valeurs décident si une stratégie passe le jalon statistique (N ≥ 100,
PF ≥ 1.30, espérance > 0). Une erreur de signe ou un `None` transformé en zéro
ferait passer un système perdant pour acceptable.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal

from app.backtest.engine import BacktestResult, TradeRecord

NOW = datetime(2024, 1, 1, tzinfo=timezone.utc)


def trade(pnl: str) -> TradeRecord:
    return TradeRecord(
        entry_at=NOW,
        exit_at=NOW,
        entry_price=Decimal("100"),
        exit_price=Decimal("100") + Decimal(pnl),
        amount=Decimal("1"),
        notional_quote=Decimal("100"),
        fees_quote=Decimal("0.5"),
        pnl_quote=Decimal(pnl),
        reason="STOP_PRICE",
        stop_loss_price=Decimal("90"),
        take_profit_price=None,
        confidence=Decimal("0.7"),
    )


def result(trades: list[TradeRecord], equity_start: str = "1000") -> BacktestResult:
    return BacktestResult(
        symbol="BTC/EUR",
        model="test",
        period_start=NOW,
        period_end=NOW,
        candles=10,
        decisions=0,
        buy=0,
        hold=0,
        downgraded=0,
        degraded=0,
        inference_ms=[],
        approvals=0,
        rejections=Counter(),
        trades=trades,
        counterfactuals=[],
        equity_start=Decimal(equity_start),
        equity_end=Decimal(equity_start),
        max_drawdown_pct=Decimal("0.01"),
        buy_and_hold_pct=Decimal("0"),
        baseline_trades=[],
        baseline_equity_end=Decimal(equity_start),
        baseline_max_drawdown_pct=Decimal("0"),
    )


def test_profit_factor_oppose_gains_et_pertes_bruts():
    outcome = result([trade("3"), trade("1"), trade("-2")])

    assert outcome.gross_profit == Decimal("4")
    assert outcome.gross_loss == Decimal("2")
    assert outcome.profit_factor == Decimal("2")
    assert outcome.profit_factor_label == "2.00"


def test_esperance_est_le_pnl_net_par_trade():
    outcome = result([trade("3"), trade("1"), trade("-2")])

    assert outcome.net_pnl == Decimal("2")
    assert outcome.expectancy_quote == Decimal("2") / Decimal("3")
    assert outcome.expectancy_pct == (Decimal("2") / Decimal("3")) / Decimal("1000")


def test_gain_et_perte_moyens_sont_separes():
    outcome = result([trade("3"), trade("1"), trade("-2")])

    assert outcome.average_win == Decimal("2")
    assert outcome.average_loss == Decimal("2")


def test_sans_perte_le_profit_factor_est_non_interpretable():
    """Un PF sans perte ne vaut pas l'infini statistique : il ne prouve rien."""
    outcome = result([trade("1"), trade("2")])

    assert outcome.gross_loss == Decimal("0")
    assert outcome.profit_factor is None
    assert outcome.profit_factor_label == "∞"


def test_sans_trade_toutes_les_metriques_sont_nulles_ou_absentes():
    outcome = result([])

    assert outcome.profit_factor is None
    assert outcome.profit_factor_label == "n/a"
    assert outcome.expectancy_quote == Decimal("0")
    assert outcome.net_pnl == Decimal("0")


def test_une_seule_perte_donne_un_profit_factor_nul():
    outcome = result([trade("-5")])

    assert outcome.profit_factor == Decimal("0")
    assert outcome.expectancy_quote == Decimal("-5")
    assert outcome.average_win == Decimal("0")


def test_le_resume_affiche_les_nouvelles_metriques():
    summary = result([trade("3"), trade("-2")]).summary()

    assert "profit factor" in summary
    assert "espérance" in summary
