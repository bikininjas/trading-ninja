#!/usr/bin/env python3
"""Sonde séparée du surveillant : lecture SQL et notification, jamais d'ordre.

À exécuter périodiquement sur une autre machine/processus avec une sonde externe
de sa propre disponibilité. La cible HTTPS vient de WATCHDOG_WEBHOOK_URL ; elle
ne doit pas apparaître dans la ligne de commande ou les journaux.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.db import from_env
from app.core.env import load_env
from app.insights.watch import notify_incident


def classify(record, *, now, max_age_seconds, require_runtime=True):
    """Traite l'absence de preuve comme UNKNOWN, jamais comme un état sain."""
    if not record:
        return {"status": "UNKNOWN", "reason": "OBSERVER_MISSING"}
    observed_at = record["observed_at"]
    if not isinstance(observed_at, datetime) or observed_at.utcoffset() is None:
        return {"status": "UNKNOWN", "reason": "INVALID_TIMESTAMP"}
    age = (now - observed_at).total_seconds()
    if age < -5:
        return {"status": "UNKNOWN", "reason": "CLOCK_SKEW"}
    if age > max_age_seconds:
        return {"status": "UNKNOWN", "reason": "OBSERVER_STALE"}
    body = record["body"]
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except (TypeError, ValueError):
            return {"status": "UNKNOWN", "reason": "INVALID_OBSERVATION"}
    if not isinstance(body, dict) or type(body.get("complete")) is not bool:
        return {"status": "UNKNOWN", "reason": "INVALID_OBSERVATION"}
    issues = body.get("issues")
    if type(issues) is not int or issues < 0:
        return {"status": "UNKNOWN", "reason": "INVALID_OBSERVATION"}
    if not body["complete"]:
        return {"status": "ALERT", "reason": "INCOMPLETE_OBSERVATION"}
    if issues:
        return {"status": "ALERT", "reason": "ACTIVE_INCIDENT", "issues": issues}
    if require_runtime and body.get("runtime_expected") is not True:
        return {"status": "UNKNOWN", "reason": "RUNTIME_NOT_MONITORED"}
    return {"status": "OK", "reason": "OBSERVER_CURRENT"}


async def read_observation(config, mode):
    kwargs = config.asyncpg_kwargs()
    kwargs.update(timeout=5, command_timeout=5)
    conn = await asyncpg.connect(**kwargs)
    try:
        return await conn.fetchrow(
            "SELECT observed_at,body FROM protection_observations WHERE mode=$1::execution_mode",
            mode,
        )
    finally:
        await conn.close(timeout=2)


async def check(mode, *, interval, webhook, read=read_observation,
                notify=notify_incident, now=None, env=None, require_runtime=True):
    now = now or datetime.now(timezone.utc)
    try:
        config = from_env(env if env is not None else load_env())
        record = await read(config, mode)
        report = classify(record, now=now, max_age_seconds=2 * interval + 5,
                          require_runtime=require_runtime)
    except Exception as exc:
        report = {"status": "UNKNOWN", "reason": "DATABASE_UNAVAILABLE",
                  "error_type": type(exc).__name__}
    report.update(mode=mode, checked_at=now.isoformat())
    if report["status"] != "OK":
        try:
            await notify(webhook, {"source": "observer_watchdog", **report})
        except Exception as exc:
            report["notification"] = "FAILED"
            report["notification_error_type"] = type(exc).__name__
        else:
            report["notification"] = "SENT"
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True, choices=("PAPER", "LIVE"))
    parser.add_argument("--interval", type=int, default=15,
                        help="intervalle du surveillant en secondes (5–300, défaut 15)")
    parser.add_argument("--allow-idle", action="store_true",
                        help="ne pas exiger que le surveillant contrôle le moteur")
    args = parser.parse_args(argv)
    if not 5 <= args.interval <= 300:
        parser.error("--interval doit être entre 5 et 300 secondes")
    if args.mode == "LIVE" and args.allow_idle:
        parser.error("--allow-idle est réservé au mode PAPER")
    webhook = os.environ.get("WATCHDOG_WEBHOOK_URL", "")
    parsed = urlsplit(webhook)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        print("WATCHDOG_WEBHOOK_URL HTTPS requis dans l'environnement", file=sys.stderr)
        return 2
    report = asyncio.run(check(args.mode, interval=args.interval, webhook=webhook,
                               require_runtime=not args.allow_idle))
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "OK" else 1


if __name__ == "__main__":
    raise SystemExit(main())
