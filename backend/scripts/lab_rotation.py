#!/usr/bin/env python3
"""Laboratoire de rotation momentum D1 — aucun ordre, aucune écriture.

    .venv/bin/python scripts/lab_rotation.py --months 60
    .venv/bin/python scripts/lab_rotation.py --months 60 --abs-filter dual

Univers : BTC/EUR, ETH/EUR, SOL/EUR, plus CASH/EUR. Toutes les 7 ou 14 bougies
D1, le laboratoire classe les actifs par moyenne des rendements 30 D1 et 60 D1.
Le filtre absolu décide ensuite si le leader reçoit l'allocation : ``none``
(relatif pur), ``ema200`` (clôture au-dessus de l'EMA200 D1), ``roc60`` (ROC60
positif) ou ``dual`` (les deux). Si le filtre échoue, 100 % cash.

Le D1 est agrégé depuis le H4 en base : une journée incomplète est ignorée,
jamais interpolée. Les frais sont appliqués à chaque rotation (vente puis
achat), avec le slippage du broker papier. C'est une mesure de laboratoire, pas
un chemin d'exécution.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.backtest.rotation import ABS_FILTERS, CASH, run_rotation_backtest  # noqa: E402
from app.core.settings import load_settings  # noqa: E402
from app.market.history import aggregate, load_candles  # noqa: E402

DEFAULT_SYMBOLS = "BTC/EUR,ETH/EUR,SOL/EUR"
WARMUP_DAYS = 300


def _decimal_list(raw: str) -> list[Decimal]:
    values = [Decimal(part.strip()) for part in raw.split(",") if part.strip()]
    if not values:
        raise SystemExit("liste de frais vide")
    return values


def _int_list(raw: str) -> list[int]:
    values = [int(part.strip()) for part in raw.split(",") if part.strip()]
    if not values or any(value <= 0 for value in values):
        raise SystemExit("cadence invalide : entiers positifs attendus")
    return values


async def _load(args) -> tuple[dict[str, list[list]], datetime | None, datetime]:
    import asyncpg

    settings = load_settings()
    symbols = [item.strip() for item in args.symbols.split(",") if item.strip()]
    now = datetime.now(timezone.utc)
    start = (
        now - timedelta(days=int(args.months * 30.44)) if args.months > 0 else None
    )
    load_start = start - timedelta(days=WARMUP_DAYS) if start else None

    conn = await asyncpg.connect(**settings.database().asyncpg_kwargs())
    candles: dict[str, list[list]] = {}
    try:
        for symbol in symbols:
            h4 = await load_candles(conn, symbol, "4h", start=load_start, end=now)
            if not h4:
                raise SystemExit(f"{symbol} : aucun H4 en base")
            d1 = aggregate(h4, "4h", "1d")
            if len(d1) < 260:
                raise SystemExit(
                    f"{symbol} : {len(d1)} bougies D1 seulement, 260 requises"
                )
            candles[symbol] = d1
            print(
                f"{symbol:9s} {len(h4):6d} H4 → {len(d1):5d} D1 "
                f"({datetime.fromtimestamp(d1[0][0] / 1000, tz=timezone.utc):%Y-%m-%d} → "
                f"{datetime.fromtimestamp(d1[-1][0] / 1000, tz=timezone.utc):%Y-%m-%d})"
            )
    finally:
        await conn.close()
    return candles, start, now


def _print_result(result, *, abs_filter: str, fee_bps: Decimal, cadence: int,
                  slippage: int, verbose: bool) -> None:
    print()
    print(
        f"filtre {abs_filter:6s} · cadence {cadence:2d} j · "
        f"frais {fee_bps} pb/côté · slippage {slippage} pb · "
        f"{result.candles} bougies D1"
    )
    print(
        f"  équité {result.equity_start:.2f} → {result.equity_end:.2f} EUR "
        f"({result.return_pct:+.2%}) · DD max {result.max_drawdown_pct:.2%} · "
        f"net {result.net_pnl:+.2f} EUR"
    )
    print(
        f"  rotations {len(result.rotations)} "
        f"({result.rotations_per_year:.1f}/an) · "
        f"exposition {result.exposure_pct:.1%} · "
        f"cash {result.cash_pct:.1%} · "
        f"vers cash {result.rotations_to_cash} / crypto "
        f"{result.rotations_to_crypto} · frais {result.fees_quote:.2f} EUR · "
        f"position finale {result.final_symbol}"
    )
    holdings = " · ".join(
        f"{symbol.replace('/EUR', '')} {value:+.1%}"
        for symbol, value in result.buy_and_hold
    )
    print(f"  buy & hold (même fenêtre) : {holdings} · cash 0.0%")
    yearly = " · ".join(
        f"{year} {cash_days / (market_days + cash_days):.0%} cash"
        for year, market_days, cash_days in result.yearly
        if market_days + cash_days
    )
    if yearly:
        print(f"  cash par année : {yearly}")

    if verbose:
        print()
        print(
            f"  {'date':10s} {'de':>9s} → {'vers':>9s} "
            f"{'équité avant':>13s} {'équité après':>12s} {'frais':>8s}  motif"
        )
        for rotation in result.rotations:
            leader = max(rotation.scores, key=lambda item: item.score)
            if rotation.to_symbol == CASH and abs_filter != "none":
                reason = (
                    f"{leader.symbol} rejeté par {abs_filter} "
                    f"(EMA200 {'oui' if leader.above_ema else 'non'}, "
                    f"ROC60 {leader.ret_long:+.2%})"
                )
            else:
                reason = (
                    f"score {leader.score:+.2%} "
                    f"(30d {leader.ret_short:+.2%}, "
                    f"60d {leader.ret_long:+.2%})"
                )
            print(
                f"  {rotation.at:%Y-%m-%d} "
                f"{rotation.from_symbol.replace('/EUR', ''):>9s} → "
                f"{rotation.to_symbol.replace('/EUR', ''):>9s} "
                f"{rotation.equity_before:13.2f} {rotation.equity_after:12.2f} "
                f"{rotation.fee_quote:8.2f}  {reason}"
            )


async def _run(args) -> int:
    candles, start, _now = await _load(args)
    settings = load_settings()
    symbol_list = tuple(candles)
    fees = _decimal_list(args.fee_bps)
    cadences = _int_list(args.cadence_days)
    slippage = (
        args.slippage_bps
        if args.slippage_bps is not None
        else settings.paper_slippage_bps
    )
    if slippage < 0:
        raise SystemExit("--slippage-bps ne peut pas être négatif")

    print()
    print(
        "Rotation momentum D1 · univers "
        f"{', '.join(symbol_list)} + {CASH} · "
        f"momentum {args.momentum_short}/{args.momentum_long} D1 · "
        f"filtre absolu {args.abs_filter}"
    )
    payload = []
    summary = []
    for fee_bps in fees:
        for cadence in cadences:
            result = run_rotation_backtest(
                candles_by_symbol=candles,
                equity_start=Decimal(str(args.equity)),
                universe=symbol_list,
                fee_bps=fee_bps,
                slippage_bps=slippage,
                cadence_days=cadence,
                momentum_short=args.momentum_short,
                momentum_long=args.momentum_long,
                abs_filter=args.abs_filter,
                start_at=start,
            )
            _print_result(
                result, abs_filter=args.abs_filter, fee_bps=fee_bps,
                cadence=cadence, slippage=slippage, verbose=args.verbose,
            )
            summary.append((fee_bps, cadence, result))
            payload.append({
                "abs_filter": args.abs_filter,
                "fee_bps": str(fee_bps),
                "cadence_days": cadence,
                "period_start": result.period_start.isoformat(),
                "period_end": result.period_end.isoformat(),
                "candles": result.candles,
                "equity_start": str(result.equity_start),
                "equity_end": str(result.equity_end),
                "return_pct": str(result.return_pct),
                "max_drawdown_pct": str(result.max_drawdown_pct),
                "rotations": len(result.rotations),
                "rotations_per_year": str(result.rotations_per_year),
                "exposure_pct": str(result.exposure_pct),
                "cash_pct": str(result.cash_pct),
                "rotations_to_cash": result.rotations_to_cash,
                "rotations_to_crypto": result.rotations_to_crypto,
                "fees_quote": str(result.fees_quote),
                "final_symbol": result.final_symbol,
                "yearly": [
                    {"year": year, "market_days": market_days,
                     "cash_days": cash_days}
                    for year, market_days, cash_days in result.yearly
                ],
                "buy_and_hold": {
                    symbol: str(value) for symbol, value in result.buy_and_hold
                },
            })

    if args.summary_table:
        _print_summary(args.abs_filter, summary)
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def _print_summary(abs_filter: str, rows) -> None:
    """Tableau Markdown des scénarios cadence × frais d'un filtre."""
    print()
    print(f"### Récapitulatif — filtre `{abs_filter}`")
    print()
    print(
        "| cadence | frais | équité finale | retour | DD max | rotations | "
        "N/an | cash | frais payés | position finale |"
    )
    print(
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|"
    )
    for fee_bps, cadence, result in rows:
        print(
            f"| {cadence} j | {fee_bps} pb | {result.equity_end:.2f} | "
            f"{result.return_pct:+.2%} | {result.max_drawdown_pct:.2%} | "
            f"{len(result.rotations)} | {result.rotations_per_year:.1f} | "
            f"{result.cash_pct:.1%} | {result.fees_quote:.2f} | "
            f"{result.final_symbol} |"
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Laboratoire de rotation momentum D1 (BTC/ETH/SOL vs cash)"
    )
    parser.add_argument("--symbols", default=DEFAULT_SYMBOLS,
                        help="paires de l'univers (séparées par des virgules)")
    parser.add_argument("--months", type=int, default=60,
                        help="fenêtre mesurée en mois (0 = tout l'historique)")
    parser.add_argument("--equity", type=float, default=1000.0,
                        help="équité virtuelle de départ (défaut 1000 EUR)")
    parser.add_argument("--fees-bps", "--fee-bps", dest="fee_bps",
                        default="25,80",
                        help="frais par côté en points de base (défaut 25,80 : "
                             "maker puis taker Kraken)")
    parser.add_argument("--slippage-bps", type=int, default=None,
                        help="slippage par côté (défaut : PAPER_SLIPPAGE_BPS)")
    parser.add_argument("--cadences", "--cadence-days", dest="cadence_days",
                        default="7,14",
                        help="cadences testées en jours D1 (défaut 7,14)")
    parser.add_argument("--momentum-short", type=int, default=30,
                        help="fenêtre du momentum court, en D1 (défaut 30)")
    parser.add_argument("--momentum-long", type=int, default=60,
                        help="fenêtre du momentum long, en D1 (défaut 60)")
    parser.add_argument("--abs-filter", choices=ABS_FILTERS, default="none",
                        help="filtre absolu du leader : none (relatif pur), "
                             "ema200, roc60 ou dual (défaut none)")
    parser.add_argument("--summary-table", action="store_true",
                        help="affiche un récapitulatif Markdown des scénarios "
                             "cadence × frais")
    parser.add_argument("--verbose", action="store_true",
                        help="affiche chaque rotation avec son motif")
    parser.add_argument("--json", action="store_true",
                        help="sortie JSON en plus du tableau")
    args = parser.parse_args()
    if args.momentum_short >= args.momentum_long or args.momentum_short <= 0:
        parser.error("il faut 0 < --momentum-short < --momentum-long")
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
