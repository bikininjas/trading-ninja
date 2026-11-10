#!/usr/bin/env python3
"""Observation et recherche : aucun chemin d'ordre ni d'armement live."""

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.insights.codec import bounded, public
from app.insights.notifications import NotificationOutbox, notification_path


def read_json(path):
    p = Path(path)
    if p.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("fichier limité à 8 Mio")
    return json.loads(p.read_text())


async def watch(config, args):
    from app.db.repository import PostgresRepository
    from app.insights.watch import observe_protection, notify_incident
    from app.exchange.kraken import build_exchange

    path = args.notification_state or notification_path("observer", config.execution_mode)
    exchange = None
    if config.mode == "live":
        if not config.observer_api_key or config.observer_api_key == config.exchange.api_key:
            raise ValueError("clé OBSERVER distincte de la clé de trading requise")
        exchange = build_exchange(api_key=config.observer_api_key, secret=config.observer_secret)
    repo = None
    with NotificationOutbox(path, source="observer", mode=config.execution_mode) as notifications:
        def stage(result):
            notifications.observe(result)
            result["observation"]["notification_pending"] = notifications.pending

        try:
            while True:
                try:
                    if repo is None:
                        repo = await PostgresRepository.connect(config.database())
                    result = await observe_protection(
                        repo, exchange, config.execution_mode,
                        expect_runtime=args.expect_runtime,
                        timeframe=config.exchange.daily_timeframe_decision,
                        universe=config.exchange.allowed_symbols,
                        on_observation=stage,
                    )
                except Exception as exc:
                    result = {
                        "observation": {"observed_at": datetime.now(timezone.utc),
                                        "mode": config.execution_mode, "complete": False},
                        "issues": [{"key": "OBSERVATION_UNKNOWN", "severity": "CRITICAL",
                                    "reason": "lecture ou persistance inconnue",
                                    "error_type": type(exc).__name__}],
                    }
                    if repo is not None:
                        try:
                            await repo.close()
                        except Exception:
                            pass
                        repo = None
                stage(result)
                delivery_failed = False
                if args.webhook and notifications.pending:
                    try:
                        await notifications.deliver(args.webhook, result, notify_incident)
                    except Exception as exc:
                        delivery_failed = True
                        print(json.dumps({"notification": "FAILED", "error_type": type(exc).__name__}),
                              file=sys.stderr, flush=True)
                result["observation"]["notification_pending"] = notifications.pending
                if repo is not None:
                    try:
                        await repo.protection_notification_state(
                            config.execution_mode, result["observation"]["observed_at"], notifications.pending,
                        )
                    except Exception:
                        # L'observation publiée reste conservatrice (pending),
                        # et une sonde distincte détectera l'arrêt du surveillant.
                        raise RuntimeError("état de livraison non persisté") from None
                print(json.dumps(public(result), ensure_ascii=False), flush=True)
                if not args.serve:
                    unhealthy = bool(result["issues"]) or delivery_failed or notifications.pending
                    return {"status": "ALERT" if unhealthy else "OBSERVED", "issues": len(result["issues"])}
                await asyncio.sleep(config.observer_interval_seconds)
        finally:
            if repo is not None:
                await repo.close()


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
    if args.action == "watch":
        return await watch(config, args)
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
    p.add_argument("--notification-state", help="file SQLite sur volume persistant privé")
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(run(args))
        raw = bounded(result if args.action == "export" else public(result))
        if getattr(args, "out", None):
            Path(args.out).write_text(raw + "\n")
        else:
            print(raw)
        return 1 if args.action == "watch" and result.get("status") == "ALERT" else 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
