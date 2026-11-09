#!/usr/bin/env python3
"""Observation et recherche : aucun chemin d'ordre ni d'armement live."""

import argparse
import asyncio
from datetime import datetime
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.insights.codec import bounded, public


def read_json(path):
    p = Path(path)
    if p.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("fichier limité à 8 Mio")
    return json.loads(p.read_text())


async def run(args):
    if args.action == "replay":
        from app.insights.replay import replay_cycle

        return await replay_cycle(read_json(args.input))
    if args.action == "experiment":
        from app.insights.experiments import run_experiment

        return await run_experiment(read_json(args.input))
    if args.action == "confidence":
        from app.insights.calibration import confidence_report

        records = read_json(args.input)
        for r in records:
            if r.get("closed_at"):
                r["closed_at"] = datetime.fromisoformat(r["closed_at"])
        return confidence_report(
            records,
            until=datetime.fromisoformat(args.until),
            probabilistic_contract=args.probabilistic_contract,
        )
    from app.core.settings import load_settings
    from app.db.repository import PostgresRepository

    config = load_settings()
    repo = await PostgresRepository.connect(config.database())
    try:
        if args.action == "export":
            from uuid import UUID

            result = await repo.cycle_packet(config.execution_mode, UUID(args.cycle))
            if result is None:
                raise ValueError("cycle introuvable")
            return result
        if args.action == "diagnose":
            from uuid import UUID
            from app.insights.diagnostics import trade_diagnostics

            data = await repo.diagnostics_input(
                config.execution_mode, UUID(args.position)
            )
            if data is None:
                raise ValueError("position introuvable")
            return trade_diagnostics(data, config.exchange.taker_fee)
        from app.insights.watch import observe_protection, notify_incident
        from app.exchange.kraken import build_exchange

        exchange = None
        if config.mode == "live":
            if (
                not config.observer_api_key
                or config.observer_api_key == config.exchange.api_key
            ):
                raise ValueError("clé OBSERVER distincte de la clé de trading requise")
            exchange = build_exchange(
                api_key=config.observer_api_key, secret=config.observer_secret
            )
            await asyncio.to_thread(exchange.load_markets)
        previous = set()
        while True:
            result = await observe_protection(
                repo,
                exchange,
                config.execution_mode,
                expect_runtime=args.expect_runtime,
                timeframe=config.exchange.daily_timeframe_decision,
                universe=config.exchange.allowed_symbols,
            )
            active = {i["key"] for i in result["issues"]}
            fresh = active - previous
            print(json.dumps(public(result), ensure_ascii=False), flush=True)
            if args.webhook and fresh:
                try:
                    await notify_incident(
                        args.webhook, {"new_incidents": sorted(fresh), **result}
                    )
                except Exception as exc:
                    print(
                        json.dumps(
                            {"notification": "FAILED", "error_type": type(exc).__name__}
                        ),
                        file=sys.stderr,
                        flush=True,
                    )
            previous = active
            if not args.serve:
                return {"status": "OBSERVED", "issues": len(active)}
            await asyncio.sleep(config.observer_interval_seconds)
    finally:
        await repo.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    for name in ("replay", "experiment", "confidence"):
        p = commands.add_parser(name)
        p.add_argument("input")
        p.add_argument("--out")
        if name == "confidence":
            p.add_argument("--until", required=True)
            p.add_argument("--probabilistic-contract", action="store_true")
    p = commands.add_parser("export")
    p.add_argument("cycle")
    p.add_argument("--out", required=True)
    p = commands.add_parser("diagnose")
    p.add_argument("position")
    p.add_argument("--out")
    p = commands.add_parser("watch")
    p.add_argument("--serve", action="store_true")
    p.add_argument("--expect-runtime", action="store_true")
    p.add_argument("--webhook")
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(run(args))
        raw = bounded(result if args.action == "export" else public(result))
        if getattr(args, "out", None):
            Path(args.out).write_text(raw + "\n")
        else:
            print(raw)
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
