"""Rejeu mécanique isolé des frais ; lecture SQL seule, aucun appel exchange/LLM.

Depuis la racine : backend/.venv/bin/python docs/audits/2026-11-17/requalify_fees.py
Le résultat est exploratoire : il ne prétend pas reproduire les configurations ni
les réponses du LLM de 2026-11-05 qui ne sont pas intégralement conservées.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import asyncpg

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))

from app.backtest.mean_reversion import run_mean_reversion_backtest  # noqa: E402
from app.backtest.swing import run_swing_backtest  # noqa: E402
from app.core.db import from_env  # noqa: E402
from app.core.env import load_env  # noqa: E402
from app.core.settings import load_settings  # noqa: E402
from app.engine.risk_gate import EntryMode, ExchangeConstraints, StopMode, SwingExitMode  # noqa: E402
from app.execution.paper import PaperBroker  # noqa: E402
from app.market.history import aggregate, detect_gaps, load_candles  # noqa: E402

START = datetime(2021, 11, 5, 8, tzinfo=timezone.utc)
END = datetime(2026, 11, 5, 4, tzinfo=timezone.utc)
SYMBOLS = ("BTC/EUR", "ETH/EUR", "SOL/EUR")
OUT = ROOT / "docs/audits/2026-11-17/fee-requalification.json"


def digest(candles):
    payload = [[str(value) for value in row] for row in candles]
    return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()


def summarise(result, fee):
    # La comptabilité conserve les frais d'entrée et de sortie par trade.
    for trade in result.trades:
        expected = (trade.entry_price * trade.amount + trade.exit_price * trade.amount) * fee
        if abs(trade.fees_quote - expected) > Decimal("1e-18"):
            raise AssertionError("frais incohérents avec les fills enregistrés")
    return {
        "trades": len(result.trades),
        "wins": len(result.wins),
        "net_pnl": str(result.net_pnl),
        "profit_factor": str(result.profit_factor) if result.profit_factor is not None else None,
        "max_drawdown_pct": str(result.max_drawdown_pct),
        "fees_paid": str(sum((t.fees_quote for t in result.trades), Decimal(0))),
        "fees_checked_per_trade": True,
    }


async def main():
    # L'environnement réel sert uniquement à choisir la connexion ; la politique
    # financière ci-dessous est isolée et ne lit aucun fichier de configuration.
    db = from_env(load_env())
    settings = load_settings(overrides={
        "TRADING_MODE": "paper", "EXCHANGE_ID": "kraken", "RISK_PROFILE": "prudent",
        "PAPER_START_EQUITY": "1000", "PAPER_SLIPPAGE_BPS": "5",
    }, use_environment=False)
    markets = json.loads((ROOT / "docs/audits/2026-11-10/market-dataset-manifest.json").read_text())["markets"]
    sources = {}
    rows = []
    conn = await asyncpg.connect(**db.asyncpg_kwargs())
    try:
        async with conn.transaction(isolation="repeatable_read", readonly=True):
            data = {symbol: await load_candles(conn, symbol, "4h", start=START, end=END)
                    for symbol in SYMBOLS}
    finally:
        await conn.close()
    for symbol, h4 in data.items():
        d1 = aggregate(h4, "4h", "1d")
        sources[symbol] = {
            "h4_count": len(h4), "d1_count": len(d1), "h4_gaps": len(detect_gaps(h4, "4h")),
            "h4_sha256": digest(h4), "d1_sha256": digest(d1), "market": markets[symbol],
        }
        constraints = markets[symbol]
        info = SimpleNamespace(taker_fee=Decimal("0.008"), constraints=ExchangeConstraints(
            amount_step=Decimal(constraints["amount_step"]), min_amount=Decimal(constraints["min_amount"]),
            min_notional=Decimal(constraints["min_notional"]), price_tick=Decimal(constraints["price_tick"]),
        ))
        for aware in (False, True):
            base = replace(settings, risk=replace(settings.risk, cost_aware=aware))
            swing = replace(base, risk=replace(
                base.risk, entry_mode=EntryMode.SWING_MACRO_D1, stop_mode=StopMode.STRUCTURAL_D1,
                take_profit_r_multiple=Decimal(0), swing_exit_mode=SwingExitMode.CHANDELIER,
                swing_require_donchian=True, swing_require_value_rebound=True,
                max_consecutive_losses=9999, max_drawdown_from_hwm_pct=Decimal("1"),
            ))
            for bps in (25, 80):
                fee = Decimal(bps) / Decimal(10000)
                result = run_swing_backtest(symbol=symbol, info=info, d1=d1, settings=swing,
                                            broker=PaperBroker(fee, 5))
                rows.append({"strategy": "swing_chandelier_both", "symbol": symbol,
                             "timeframe": "1d", "cost_aware": aware, "fee_bps": bps,
                             **summarise(result, fee)})
                for timeframe, candles in (("4h", h4), ("1d", d1)):
                    result = run_mean_reversion_backtest(
                        symbol=symbol, info=info, candles=candles, settings=base,
                        broker=PaperBroker(fee, 5), rsi_max=Decimal("32"),
                        exit_mode="touch", allow_counter_trend=False,
                    )
                    rows.append({"strategy": "mean_reversion_rsi32_touch_gated", "symbol": symbol,
                                 "timeframe": timeframe, "cost_aware": aware, "fee_bps": bps,
                                 **summarise(result, fee)})
        print(f"{symbol} : {len(h4)} H4, {len(d1)} D1, simulations terminées", flush=True)
    payload = {
        "purpose": "Exploration mécanique ; pas de reproduction certifiée des résultats ni des réponses LLM historiques.",
        "window": {"start_inclusive": START.isoformat(), "end_exclusive": END.isoformat(),
                   "last_closed_h4_only": True},
        "settings": {"profile": "prudent", "mode": "paper", "equity_eur": "1000",
                     "slippage_bps_per_side": 5, "cost_aware": [False, True],
                     "swing_breakers": {"max_consecutive_losses": 9999, "max_drawdown_from_hwm_pct": "1"},
                     "mean_reversion_breakers": "prudent defaults"},
        "market_constraints_source": "docs/audits/2026-11-10/market-dataset-manifest.json",
        "sources": sources, "rows": rows,
    }
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    print(f"Écrit : {OUT.relative_to(ROOT)} ({len(rows)} scénarios)", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
