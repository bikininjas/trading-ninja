#!/usr/bin/env python3
"""Comparer les budgets sur un historique figé, sans réseau, LLM ni ordre.

Ce court historique est une démonstration mécanique, jamais une validation de
rentabilité. Les contraintes de marché sont celles du fichier, pas celles du jour.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.backtest.engine import run_backtest
from app.backtest.golden import load_bundle, market_from_bundle
from app.core.settings import load_settings


async def compare(golden: Path, budgets: list[Decimal], profiles: list[str]) -> list[dict]:
    bundle = load_bundle(golden)
    info = market_from_bundle(bundle)
    rows = []
    for profile in profiles:
        for budget in budgets:
            if not budget.is_finite() or budget <= 0:
                raise ValueError("chaque budget doit être fini et > 0")
            settings = load_settings(use_environment=False, overrides={
                "RISK_PROFILE": profile, "EXCHANGE_ID": "kraken",
                "TRADING_SYMBOL": bundle.symbol, "PAPER_START_EQUITY": str(budget),
                "TAKER_FEE_PCT": str(info.taker_fee),
            })
            result = await run_backtest(
                symbol=bundle.symbol, info=info, h4=bundle.h4, h1=bundle.h1,
                settings=settings, propose=None, mechanical=True,
                start_index=bundle.start_index, end_index=bundle.end_index,
                counterfactuals=False,
            )
            # En mode mécanique le moteur expose le ledger baseline.
            trades = result.baseline_trades
            rows.append({
                "profile": profile, "budget": str(budget),
                "symbol": bundle.symbol, "start": result.period_start.isoformat(),
                "end": result.period_end.isoformat(), "trades": len(trades),
                "net_pnl": str(result.baseline_equity_end - budget),
                "equity_end": str(result.baseline_equity_end),
                "max_drawdown_pct": str(result.baseline_max_drawdown_pct),
                "rejections": dict(result.rejections),
                "warning": "Fenêtre courte, achats mécaniques sans LLM ; aucune validation de rentabilité.",
            })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--golden", type=Path, default=Path(__file__).resolve().parents[1]
                        / "tests/fixtures/backtest_golden.json")
    parser.add_argument("--budgets", default="9.37,25,50,1000")
    parser.add_argument("--profiles", default="prudent,micro,progressif")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    rows = asyncio.run(compare(args.golden, [Decimal(v) for v in args.budgets.split(",")],
                               args.profiles.split(",")))
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        print(rows[0]["warning"])
        print(f"{rows[0]['symbol']} : {rows[0]['start']} → {rows[0]['end']}")
        print(f"{'profil':12} {'budget':>9} {'trades':>7} {'net EUR':>12} {'DD':>9}")
        for row in rows:
            print(f"{row['profile']:12} {Decimal(row['budget']):9.2f} {row['trades']:7d} "
                  f"{Decimal(row['net_pnl']):12.4f} {Decimal(row['max_drawdown_pct']):9.2%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
