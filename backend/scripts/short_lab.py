#!/usr/bin/env python
"""Mesure le mandat short sur le golden, sans réseau ni ordre.

Usage :
    .venv/bin/python scripts/short_lab.py
    .venv/bin/python scripts/short_lab.py --require-negative-h1
    .venv/bin/python scripts/short_lab.py --stop-atr 3 --take-profit-r 2
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.backtest.golden import load_bundle
from app.backtest.short_lab import simulate_short


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bundle", default=str(ROOT / "tests/fixtures/backtest_golden.json")
    )
    parser.add_argument("--slope-max", default="0",
                        help="pente EMA200 max (0 = plate ou baissière ; 'none' = pas de filtre)")
    parser.add_argument("--rsi-min", default="30")
    parser.add_argument("--rsi-max", default="65")
    parser.add_argument("--stop-atr", default="2.5")
    parser.add_argument("--take-profit-r", default="1.5")
    parser.add_argument("--require-negative-h1", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    bundle = load_bundle(args.bundle)
    slope_max = None if args.slope_max.lower() == "none" else Decimal(args.slope_max)
    result = simulate_short(
        bundle,
        slope_max=slope_max,
        rsi_min=Decimal(args.rsi_min),
        rsi_max=Decimal(args.rsi_max),
        require_negative_h1=args.require_negative_h1,
        stop_atr=Decimal(args.stop_atr),
        take_profit_r=Decimal(args.take_profit_r),
    )
    if args.json:
        print(json.dumps({
            "symbol": result.symbol,
            "trades": [asdict(t) for t in result.trades],
            "equity_start": str(result.equity_start),
            "equity_end": str(result.equity_end),
            "pnl_quote": str(result.pnl_quote),
            "return_pct": str(result.return_pct),
            "win_rate": str(result.win_rate),
            "buy_and_hold_pct": str(result.buy_and_hold_pct),
            "skipped_below_minimum": result.skipped_below_minimum,
        }, default=str, indent=2))
    else:
        print(result.summary())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
