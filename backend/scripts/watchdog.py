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
from app.insights.notifications import NotificationOutbox, notification_path
from app.insights.health import classify


async def read_observation(config, mode):
    kwargs = config.asyncpg_kwargs()
    kwargs.update(timeout=5, command_timeout=5)
    conn = await asyncpg.connect(**kwargs)
    try:
        return await conn.fetchrow(
            "SELECT p.observed_at,p.body,b.last_heartbeat_at AS runtime_heartbeat_at, "
            "c.started_at AS runtime_cycle_started_at,c.status AS runtime_cycle_status "
            "FROM protection_observations p LEFT JOIN bot_state b ON b.mode=p.mode "
            "LEFT JOIN LATERAL (SELECT started_at,status FROM decision_cycles "
            "WHERE mode=p.mode ORDER BY started_at DESC,id LIMIT 1) c ON true "
            "WHERE p.mode=$1::execution_mode",
            mode,
        )
    finally:
        await conn.close(timeout=2)


async def check(mode, *, interval, webhook, read=read_observation,
                notify=notify_incident, now=None, env=None, require_runtime=True,
                notification_state=None):
    checked_at = now
    try:
        config = from_env(env if env is not None else load_env())
        record = await read(config, mode)
        checked_at = now or datetime.now(timezone.utc)
        report = classify(record, now=checked_at, max_age_seconds=2 * interval,
                          require_runtime=require_runtime)
    except Exception as exc:
        report = {"status": "UNKNOWN", "reason": "DATABASE_UNAVAILABLE",
                  "error_type": type(exc).__name__}
    checked_at = checked_at or datetime.now(timezone.utc)
    report.update(mode=mode, checked_at=checked_at.isoformat())
    path = notification_state or notification_path("watchdog", mode)
    with NotificationOutbox(path, source="observer_watchdog", mode=mode) as outbox:
        outbox.observe({
            "observation": {"observed_at": checked_at},
            "issues": [] if report["status"] == "OK" else [{"key": report["reason"], **report}],
        })
        if outbox.pending:
            try:
                await outbox.deliver(webhook, report, notify)
            except Exception as exc:
                report["notification"] = "FAILED"
                report["notification_error_type"] = type(exc).__name__
                if report["status"] == "OK":
                    report.update(status="ALERT", reason="NOTIFICATION_PENDING")
            else:
                report["notification"] = "SENT"
                if outbox.pending and report["status"] == "OK":
                    report.update(status="ALERT", reason="NOTIFICATION_PENDING")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True, choices=("PAPER", "LIVE"))
    parser.add_argument("--interval", type=int, default=15,
                        help="intervalle du surveillant en secondes (5–300, défaut 15)")
    parser.add_argument("--allow-idle", action="store_true",
                        help="ne pas exiger que le surveillant contrôle le moteur")
    parser.add_argument("--notification-state", help="file SQLite sur volume persistant privé")
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
                               require_runtime=not args.allow_idle,
                               notification_state=args.notification_state))
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "OK" else 1


if __name__ == "__main__":
    raise SystemExit(main())
