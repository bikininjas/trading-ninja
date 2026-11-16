"""Le faux rebond a un dénominateur explicite ; le lag n'invente pas de paire."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from types import SimpleNamespace as NS

from app.backtest.macro import bull_traps, entry_lag

T = datetime(2025, 1, 1, tzinfo=timezone.utc)


def trade(hours=0, pnl="1", reason="TAKE_PROFIT", duration=8):
    return NS(entry_at=T+timedelta(hours=hours), exit_at=T+timedelta(hours=hours+duration),
              entry_price=D("100")+hours, pnl_quote=D(pnl), reason=reason,
              stop_loss_price=D("95"))


def states(trades):
    return {t.entry_at: NS(snapshot=NS(price=D("100"), ema_50_h4=D("99"),
                                     ema_200_h4=D("110"))) for t in trades}


def test_bull_trap_counts_only_first_h4_stop_and_eligible_entries():
    trades = [trade(reason="STOP_LOSS", duration=4), trade(8, reason="STOP_LOSS"),
              trade(16), trade(24, reason="END_OF_WINDOW", duration=4)]
    assert bull_traps(trades, states(trades)) == {
        "under_ema200_trades": 4, "immediate_stops": 1, "bull_trap_rate": "0.25"}
    assert bull_traps([], {})["bull_trap_rate"] is None
    context = states(trades)
    for s in context.values():
        s.snapshot.ema_200_h4 = D("90")
    assert bull_traps(trades, context)["under_ema200_trades"] == 0


def test_lag_is_one_to_one_and_reports_unmatched():
    early = [trade(), trade(4)]
    metrics = entry_lag(early, [trade(12)], states(early), [])
    assert metrics["lag_pairs"] == 1
    assert metrics["lag_unmatched"] == 1
    assert D(metrics["lag_price_eur"]) == 12
    assert D(metrics["lag_price_pct"]) == D("0.12")
    assert D(metrics["lag_hours"]) == 12


def test_lag_refuses_invalidation_loss_and_distant_reference():
    early = [trade()]
    low = [[int(T.timestamp()*1000), 100, 110, 94, 102, 1]]
    assert entry_lag(early, [trade(12)], states(early), low)["lag_pairs"] == 0
    assert entry_lag(early, [trade(12, pnl="-1"), trade(16)], states(early), [])["lag_pairs"] == 0
    assert entry_lag(early, [trade(31*24)], states(early), [])["lag_pairs"] == 0
