"""Évalue le LLM sur les signaux swing D1, sans ordre ni écriture en base.

Le backtest mécanique (``backtest.py swing``) fournit les trades théoriques :
tous les setups pris avec une confiance de 1,0. Ce script rejoue le modèle sur
chaque date d'entrée, avec le vrai prompt Daily et le vrai snapshot D1, puis
mesure :

- la **rétention** : gagnants approuvés / gagnants théoriques ;
- la **filtration** : perdants rejetés / perdants théoriques.

Critère du brief : filtration ≥ 25 % sans perdre plus de 10 % des gagnants.
Aucune décision de ce script n'est exécutée.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.backtest.llm_eval import EvalRecord, compute_metrics
from app.backtest.swing import build_series, run_swing_backtest, snapshot_at
from app.core.settings import load_settings
from app.engine.risk_gate import EntryMode, StopMode, SwingExitMode
from app.exchange.kraken import build_exchange, market_info, resolve_taker_fee
from app.llm.client import SignalClient
from app.market.feed import D1_WARMUP
from app.market.history import aggregate, load_candles

D1_MS = 86_400_000


def _close_time(candle: list) -> datetime:
    return datetime.fromtimestamp((int(candle[0]) + D1_MS) / 1000, tz=timezone.utc)


def _format_rate(value: Decimal | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


async def evaluate(args: argparse.Namespace) -> int:
    import asyncpg

    settings = load_settings()
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    triggers = [t.strip().lower() for t in args.triggers.split(",") if t.strip()]
    valid = {"both", "donchian", "value"}
    if not triggers or set(triggers) - valid:
        raise SystemExit(
            f"--triggers invalide : {args.triggers!r} "
            f"(connus : {', '.join(sorted(valid))})"
        )
    try:
        exit_mode = SwingExitMode(args.exit_mode.upper())
    except ValueError as exc:
        raise SystemExit(
            f"--exit-mode invalide : {args.exit_mode!r} "
            f"(connus : {', '.join(m.value.lower() for m in SwingExitMode)})"
        ) from exc

    fee = Decimal(str(args.fee_bps)) / Decimal(10_000)
    flags = {
        "both": (True, True),
        "donchian": (True, False),
        "value": (False, True),
    }[triggers[0]]
    if len(triggers) > 1:
        raise SystemExit("--triggers n'accepte qu'un déclencheur à la fois ici")

    scenario = replace(
        settings,
        risk=replace(
            settings.risk,
            entry_mode=EntryMode.SWING_MACRO_D1,
            stop_mode=StopMode.STRUCTURAL_D1,
            take_profit_r_multiple=Decimal(0),
            swing_require_donchian=flags[0],
            swing_require_value_rebound=flags[1],
            swing_exit_mode=exit_mode,
            max_consecutive_losses=9999,
            max_drawdown_from_hwm_pct=Decimal("1.0"),
        ),
    )
    client = SignalClient(replace(settings.llm, temperature=0.0))
    exchange = build_exchange(
        api_key=settings.exchange.api_key, secret=settings.exchange.secret
    )
    exchange.load_markets()

    now = datetime.now(timezone.utc)
    start = now - timedelta(days=int(args.months * 30.44)) if args.months > 0 else None
    print(
        f"Modèle {settings.llm.model} · fenêtre "
        f"{start:%Y-%m-%d} → {now:%Y-%m-%d}" if start
        else f"Modèle {settings.llm.model} · tout l'historique"
    )
    print(
        f"Frais {args.fee_bps} pb/côté · sortie {exit_mode.value.lower()} · "
        f"déclencheur {triggers[0]}"
    )

    conn = await asyncpg.connect(**settings.database().asyncpg_kwargs())
    records: list[EvalRecord] = []
    try:
        for symbol in symbols:
            h4 = await load_candles(conn, symbol, "4h", start=start, end=now)
            if not h4:
                print(f"{symbol} : aucun H4 en base — ignoré")
                continue
            d1 = aggregate(h4, "4h", "1d")
            if len(d1) < D1_WARMUP + 30:
                print(f"{symbol} : {len(d1)} D1 seulement — ignoré")
                continue
            info = market_info(
                exchange, symbol,
                taker_fee=resolve_taker_fee(exchange, symbol, settings.exchange.taker_fee),
                maker_fee=settings.exchange.maker_fee,
            )
            result = run_swing_backtest(
                symbol=symbol, info=info, d1=d1, settings=scenario,
            )
            trades = result.trades[: args.limit] if args.limit > 0 else result.trades
            if not trades:
                print(f"{symbol} : aucun trade théorique")
                continue
            series = build_series(d1)
            index_by_close = {_close_time(candle): index for index, candle in enumerate(d1)}
            symbol_records: list[EvalRecord] = []
            for trade in trades:
                index = index_by_close.get(trade.entry_at)
                if index is None:
                    print(f"{symbol} : entrée {trade.entry_at} introuvable — ignorée")
                    continue
                snapshot, context = snapshot_at(
                    series, index, symbol,
                    chandelier_period=scenario.risk.swing_chandelier_period,
                )
                signal = await client.propose(
                    symbol, snapshot, context, has_position=False,
                    trend_buffer_pct=scenario.risk.trend_buffer_pct,
                    decision_timeframe="1d",
                )
                approved = (
                    signal.action == "BUY"
                    and not signal.degraded
                    and signal.confidence >= scenario.risk.min_confidence
                )
                symbol_records.append(
                    EvalRecord(
                        symbol=symbol,
                        entry_at=trade.entry_at,
                        exit_at=trade.exit_at,
                        pnl_quote=trade.pnl_quote,
                        action=signal.action,
                        confidence=signal.confidence,
                        market_regime=signal.market_regime,
                        thesis=signal.thesis,
                        inference_ms=signal.inference_ms,
                        degraded=signal.degraded,
                        approved=approved,
                    )
                )
            metrics = compute_metrics(symbol_records)
            print(
                f"{symbol} : {metrics.total} trades · gagnants {metrics.winners} · "
                f"approuvés {metrics.approved} · rétention "
                f"{_format_rate(metrics.retention)} · filtration "
                f"{_format_rate(metrics.filtration)} · {metrics.verdict_label}"
            )
            records.extend(symbol_records)
    finally:
        await conn.close()

    metrics = compute_metrics(records)
    print()
    print(
        f"Global : {metrics.total} trades · gagnants {metrics.winners} · "
        f"perdants {metrics.losers} · approuvés {metrics.approved} "
        f"({_format_rate(metrics.approval_rate)})"
    )
    print(
        f"Rétention {_format_rate(metrics.retention)} · "
        f"filtration {_format_rate(metrics.filtration)} · {metrics.verdict_label}"
    )
    print("Critère : filtration ≥ 25 % des perdants, rétention ≥ 90 % des gagnants.")

    if args.out:
        payload = {
            "model": settings.llm.model,
            "fee_bps": args.fee_bps,
            "exit_mode": exit_mode.value.lower(),
            "trigger": triggers[0],
            "metrics": {
                key: (str(value) if isinstance(value, Decimal) else value)
                for key, value in asdict(metrics).items()
            },
            "records": [
                {
                    **{
                        key: value
                        for key, value in asdict(record).items()
                        if key not in {"pnl_quote", "confidence", "entry_at", "exit_at"}
                    },
                    "entry_at": record.entry_at.isoformat(),
                    "exit_at": record.exit_at.isoformat(),
                    "pnl_quote": str(record.pnl_quote),
                    "confidence": str(record.confidence),
                }
                for record in records
            ],
        }
        Path(args.out).write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"Détail écrit dans {args.out}")

    if args.json:
        print(json.dumps({
            "metrics": {
                key: (str(value) if isinstance(value, Decimal) else value)
                for key, value in asdict(metrics).items()
            }
        }, indent=2, ensure_ascii=False))
    return 0 if metrics.verdict_label == "OK" else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Évalue le LLM sur les signaux swing D1 (aucun ordre)"
    )
    parser.add_argument(
        "--symbols", default="BTC/EUR,ETH/EUR,SOL/EUR",
        help="paires séparées par des virgules",
    )
    parser.add_argument(
        "--months", type=int, default=0,
        help="profondeur de la fenêtre en mois (0 = tout l'historique)",
    )
    parser.add_argument(
        "--fee-bps", type=int, default=80,
        help="frais par côté en points de base (80 = taker Kraken réel)",
    )
    parser.add_argument(
        "--exit-mode", default="ema20", choices=("ema20", "chandelier"),
        help="sortie utilisée pour calculer les trades théoriques",
    )
    parser.add_argument(
        "--triggers", default="both", choices=("both", "donchian", "value"),
        help="déclencheur utilisé pour calculer les trades théoriques",
    )
    parser.add_argument(
        "--limit", type=int, default=0,
        help="nombre maximum de trades évalués par paire (0 = tous)",
    )
    parser.add_argument("--out", default=None, help="fichier JSON de détail")
    parser.add_argument("--json", action="store_true", help="métriques JSON sur stdout")
    args = parser.parse_args()
    return asyncio.run(evaluate(args))


if __name__ == "__main__":
    raise SystemExit(main())
