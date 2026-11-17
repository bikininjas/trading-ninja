#!/usr/bin/env python3
"""Worker PUBLIC de veille : aucune connexion exchange et aucun droit d'ordre.

MARKET_INTEL_ENABLED=true python scripts/market_intel.py --once
Utiliser un LOGIN privé membre de tn_market_intel_writer après migration 0016.
"""
import argparse
import asyncio
import logging
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import asyncpg
from app.core.settings import load_settings
from app.intel.store import store_within_budget
from app.intel.worker import analyze
from app.llm.client import SignalClient


async def run(args):
    settings = load_settings(env_file=args.config)
    if not settings.intel.enabled or settings.mode != "paper":
        raise SystemExit("Worker réservé au PAPER ; MARKET_INTEL_ENABLED=true requis")
    while True:
        try:
            # Le même 9B peut être déchargé entre deux collectes horaires.
            # Préchauffage séparé des timeouts réseau/JSON, sans achat ni pull.
            ready = await SignalClient(replace(settings.llm, model=settings.intel.model)).warmup()
            if not ready:
                logging.warning("market_intel_model_warmup unavailable")
            row = await analyze(settings, now=datetime.now(timezone.utc))
            conn = await asyncpg.connect(**settings.database().asyncpg_kwargs())
            try:
                if await conn.fetchval(
                    "SELECT current_user='postgres' OR NOT pg_has_role(current_user,'tn_market_intel_writer','MEMBER') "
                    "OR has_table_privilege(current_user,'orders','INSERT') "
                    "OR has_table_privilege(current_user,'positions','UPDATE')"):
                    raise RuntimeError("identité SQL du worker non isolée ou privilégiée")
                await store_within_budget(conn, row,
                    quota_mb=int(settings._raw_env.get("SUPABASE_DB_QUOTA_MB", "500")),
                    min_free_mb=int(settings._raw_env.get("SUPABASE_DB_MIN_FREE_MB", "100")))
            finally:
                await conn.close()
            logging.info("market_intel_digest status=%s articles=%d", row["status"], len(row["source_hashes"]))
        except Exception as exc:
            logging.warning("market_intel_unavailable kind=%s", type(exc).__name__)
            if not args.serve:
                return 1
        if not args.serve:
            return 0
        await asyncio.sleep(settings.intel.interval_seconds)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--once", action="store_true")
    group.add_argument("--serve", action="store_true")
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(asyncio.run(run(parser.parse_args())))
