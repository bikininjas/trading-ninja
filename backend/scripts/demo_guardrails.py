"""Démonstration lisible du pare-feu : ce qu'il refuse, et pourquoi.

Usage :  .venv/bin/python scripts/demo_guardrails.py
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.engine.risk_gate import (
    AccountState, Action, ExchangeConstraints, MarketRegime, MarketSnapshot,
    RejectionCode, RiskConfig, TradeIntent, evaluate,
)

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
EQUITY = Decimal("1000")

CONSTRAINTS = ExchangeConstraints(
    amount_step=Decimal("0.00001"),
    min_amount=Decimal("0.00001"),
    min_notional=Decimal("10"),
    price_tick=Decimal("0.01"),
)

BASE_MARKET = dict(
    symbol="BTC/USDT", price=Decimal("68450"), ema_200_h4=Decimal("67200"),
    atr_14_h4=Decimal("900"), last_closed_h1_return_atr=Decimal("-0.3"),
    tick_at=NOW, now=NOW,
)


def show(label, *, market=None, account=None, intent=None):
    m = MarketSnapshot(**{**BASE_MARKET, **(market or {})})
    a = account or AccountState(equity_quote=EQUITY, free_quote=EQUITY)
    i = intent or TradeIntent(Action.BUY, 0.91, MarketRegime.TRENDING_UP)
    d = evaluate(i, m, a, CONSTRAINTS, RiskConfig())

    if d.approved:
        print(f"  OK   {label}")
        print(f"       achat {d.amount} BTC  = {d.notional_quote:.2f} USDT "
              f"({d.notional_quote / EQUITY:.1%} de l'équité)")
        print(f"       stop {d.stop_loss_price}  ({d.stop_distance_quote:.2f} USDT de distance)")
        print(f"       TP   {d.take_profit_price}")
        print(f"       risque réel {d.effective_risk_quote:.2f} USDT "
              f"= {d.effective_risk_pct:.2%} (budget {d.intended_risk_quote:.2f})")
    else:
        print(f"  REFUS {label}")
        print(f"        {d.rejection_code}: {d.rejection_details}")


print(f"\nCompte : {EQUITY} USDT | BTC/USDT 68 450 | EMA200 H4 67 200 | ATR14 H4 900")
print("Profil prudent : risque 0.5 %/trade, stop 2.5xATR, exposition max 20 %\n")

print("Cas nominal")
show("toutes les conditions réunies")

print("\nLe marché ne s'y prête pas")
show("prix sous l'EMA200", market=dict(price=Decimal("66000")))
show("grosse bougie rouge", market=dict(last_closed_h1_return_atr=Decimal("-3.1")))
show("marché mort (ATR 0.1 %)", market=dict(atr_14_h4=Decimal("68")))
show("krach / données suspectes (ATR 12 %)", market=dict(atr_14_h4=Decimal("8200")))
show("données périmées (12 s sans tick)", market=dict(tick_at=NOW - timedelta(seconds=12)))

print("\nLe modèle n'est pas convaincu")
show("confidence 0.62", intent=TradeIntent(Action.BUY, 0.62, MarketRegime.RANGING))
show("le modèle dit baissier", intent=TradeIntent(Action.BUY, 0.95, MarketRegime.TRENDING_DOWN))
show("le modèle dit SELL", intent=TradeIntent(Action.SELL, 0.99, MarketRegime.TRENDING_DOWN))

print("\nLe compte est en mauvaise posture")
show("position déjà ouverte", account=AccountState(EQUITY, EQUITY, ("BTC/USDT",)))
show("-2.4 % sur 24 h", account=AccountState(EQUITY, EQUITY, daily_drawdown_pct=Decimal("0.024")))
show("-11 % depuis le plus haut", account=AccountState(EQUITY, EQUITY, drawdown_from_hwm_pct=Decimal("0.11")))
show("2 pertes d'affilée -> risque divisé",
     account=AccountState(EQUITY, EQUITY, consecutive_losses=2))
show("4 pertes d'affilée -> verrou", account=AccountState(EQUITY, EQUITY, consecutive_losses=4))
show("6 ordres dans l'heure -> coupure", account=AccountState(EQUITY, EQUITY, orders_last_hour=6))
show("plus de liquidités", account=AccountState(Decimal("300"), Decimal("4")))
print()
