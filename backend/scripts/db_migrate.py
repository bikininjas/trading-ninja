#!/usr/bin/env python
"""Application des migrations SQL sur la base cible.

Usage :
    python scripts/db_migrate.py status     # état des migrations
    python scripts/db_migrate.py apply      # applique ce qui manque
    python scripts/db_migrate.py verify     # contrôle le schéma + test d'écriture

La connexion est résolue depuis DATABASE_URL, ou dérivée des variables
Supabase du fichier .env.local. Le mot de passe n'est jamais affiché.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import sys
import time
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.db import DatabaseConfig, DatabaseConfigError, from_env  # noqa: E402
from app.core.env import load_env  # noqa: E402

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "db" / "migrations"
LOCK_KEY = 873421  # identifiant arbitraire mais stable du verrou d'advisory lock

CREATE_MIGRATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version      TEXT PRIMARY KEY,
    checksum     CHAR(64) NOT NULL,
    applied_at   TIMESTAMPTZ NOT NULL DEFAULT CLOCK_TIMESTAMP(),
    execution_ms INTEGER NOT NULL
)
"""


def discover() -> list[Path]:
    return sorted(p for p in MIGRATIONS_DIR.glob("*.sql") if p.is_file())


def checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def connect(cfg: DatabaseConfig) -> asyncpg.Connection:
    return await asyncpg.connect(**cfg.asyncpg_kwargs())


async def applied_versions(conn: asyncpg.Connection) -> dict[str, str]:
    exists = await conn.fetchval("SELECT to_regclass('public.schema_migrations') IS NOT NULL")
    if not exists:
        return {}
    rows = await conn.fetch("SELECT version, checksum FROM schema_migrations")
    return {r["version"]: r["checksum"] for r in rows}


async def cmd_status(conn: asyncpg.Connection) -> int:
    applied = await applied_versions(conn)
    print(f"{'migration':40s} {'état':12s} {'appliquée le':20s}")
    print("-" * 78)
    drift = False
    for path in discover():
        prev = applied.get(path.name)
        if prev is None:
            print(f"{path.name:40s} {'EN ATTENTE':12s} {'-':20s}")
        elif prev != checksum(path):
            print(f"{path.name:40s} {'MODIFIÉE':12s} {'-':20s}")
            drift = True
        else:
            row = await conn.fetchrow(
                "SELECT applied_at FROM schema_migrations WHERE version = $1", path.name
            )
            print(f"{path.name:40s} {'appliquée':12s} {row['applied_at']:%Y-%m-%d %H:%M:%S}")
    orphan = set(applied) - {p.name for p in discover()}
    for name in sorted(orphan):
        print(f"{name:40s} {'ORPHELINE':12s} {'-':20s}")
        drift = True
    if drift:
        print("\n! Une migration appliquée a été modifiée sur disque : l'état de la base")
        print("  ne correspond plus aux fichiers. Créez une nouvelle migration.")
        return 2
    return 0


async def cmd_apply(conn: asyncpg.Connection) -> int:
    await conn.execute(CREATE_MIGRATIONS_TABLE)
    applied = await applied_versions(conn)
    pending = [p for p in discover() if p.name not in applied]
    if not pending:
        print("Rien à appliquer : la base est à jour.")
        return 0

    for path in pending:
        sql = path.read_text(encoding="utf-8")
        started = time.perf_counter()
        async with conn.transaction():
            await conn.execute(sql)
            elapsed = int((time.perf_counter() - started) * 1000)
            await conn.execute(
                "INSERT INTO schema_migrations (version, checksum, execution_ms) VALUES ($1, $2, $3)",
                path.name,
                checksum(path),
                elapsed,
            )
        print(f"  appliquée  {path.name}  ({elapsed} ms)")
    print(f"\n{len(pending)} migration(s) appliquée(s).")
    return 0


async def cmd_verify(conn: asyncpg.Connection) -> int:
    """Contrôle structurel + test d'écriture annulé (rien ne reste en base)."""
    failures: list[str] = []

    expected = {
        "llm_signals", "risk_evaluations", "positions", "orders", "profit_sweeps",
        "bot_state", "equity_snapshots", "system_state_logs", "schema_migrations",
        "runtime_settings", "shadow_trades", "lessons",
    }
    rows = await conn.fetch(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
    )
    tables = {r["tablename"] for r in rows}
    tables |= {
        r["relname"]
        for r in await conn.fetch(
            "SELECT relname FROM pg_class WHERE relkind = 'p' AND relnamespace = 'public'::regnamespace"
        )
    }
    missing = expected - tables
    if missing:
        failures.append(f"tables manquantes : {', '.join(sorted(missing))}")

    views = {r["viewname"] for r in await conn.fetch(
        "SELECT viewname FROM pg_views WHERE schemaname = 'public'"
    )}
    expected_views = {
        "v_dashboard_pnl_metrics", "v_dashboard_pnl_daily", "v_risk_rejection_stats",
        "v_open_positions", "v_system_health",
    }
    if expected_views - views:
        failures.append(f"vues manquantes : {', '.join(sorted(expected_views - views))}")

    parts = await conn.fetch(
        """SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid
           WHERE i.inhparent IN ('llm_signals'::regclass, 'risk_evaluations'::regclass)"""
    )
    partition_names = {r["relname"] for r in parts}
    if not any(n.endswith("_default") for n in partition_names):
        failures.append("partition DEFAULT absente : un INSERT pourrait échouer")
    if not any("_2026_" in n or "_2027_" in n for n in partition_names):
        failures.append("aucune partition mensuelle créée")

    enums = await conn.fetch(
        "SELECT t.typname, array_agg(e.enumlabel ORDER BY e.enumsortorder) AS labels "
        "FROM pg_type t JOIN pg_enum e ON e.enumtypid = t.oid "
        "WHERE t.typname IN ('risk_rejection_code','system_state','order_type','order_purpose') "
        "GROUP BY t.typname"
    )
    enum_map = {r["typname"]: r["labels"] for r in enums}
    if "EXECUTING" not in enum_map.get("system_state", []):
        failures.append("system_state.EXECUTING manquant")
    if "STOP_LOSS" not in enum_map.get("order_type", []):
        failures.append("order_type.STOP_LOSS manquant (type réel Binance spot)")

    # Test d'écriture : on insère dans les tables critiques puis on annule.
    tx = conn.transaction()
    await tx.start()
    try:
        signal_id = await conn.fetchval(
            """INSERT INTO llm_signals
               (symbol, model_name, action, confidence, market_regime, thesis,
                prompt_payload, raw_response, inference_time_ms)
               VALUES ('BTC/USDT','test','BUY',0.9,'TRENDING_UP','smoke',
                       '{"a":1}'::jsonb,'{"b":2}'::jsonb,10)
               RETURNING id"""
        )
        await conn.execute(
            """INSERT INTO risk_evaluations
               (symbol, signal_id, approved, market_price, ema_200_h4, atr_14_h4,
                atr_pct_of_price, account_equity_quote, free_quote_balance,
                computed_amount, computed_stop_loss, evaluation_time_ms)
               VALUES ('BTC/USDT',$1,TRUE,100,90,2,0.02,1000,1000,10,95,5)""",
            signal_id,
        )
        pos_id = await conn.fetchval(
            """INSERT INTO positions
               (symbol, entry_price, initial_amount, stop_loss_price, take_profit_price, risk_amount_quote)
               VALUES ('BTC/USDT',100,10,95,110,50) RETURNING id"""
        )
        await conn.execute(
            """INSERT INTO orders (position_id, client_order_id, symbol, side, order_type,
                                   purpose, requested_amount, stop_price)
               VALUES ($1,'smoke-1','BTC/USDT','SELL','STOP_LOSS','STOP_LOSS',10,95)""",
            pos_id,
        )
        await conn.execute(
            """INSERT INTO equity_snapshots (mode, equity_quote, free_quote, position_value_quote)
               VALUES ('PAPER',1000,1000,0)"""
        )
        # Le garde-fou monoposition doit refuser la seconde position ouverte.
        try:
            await conn.execute(
                """INSERT INTO positions
                   (symbol, entry_price, initial_amount, stop_loss_price, risk_amount_quote)
                   VALUES ('BTC/USDT',101,1,96,5)"""
            )
            failures.append("le garde-fou monoposition (index unique) ne s'est pas déclenché")
        except asyncpg.UniqueViolationError:
            pass
    finally:
        await tx.rollback()

    if failures:
        print("ÉCHEC")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("OK — schéma conforme, écriture validée, transactions annulées.")
    print(f"  {len(tables)} relations, {len(views)} vues, {len(partition_names)} partitions")
    return 0


async def main_async(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Migrations Trading Ninja")
    parser.add_argument("command", nargs="?", default="apply", choices=["apply", "status", "verify"])
    args = parser.parse_args(argv)

    env = load_env()
    try:
        cfg = from_env(env)
    except DatabaseConfigError as exc:
        print(f"Configuration base invalide : {exc}", file=sys.stderr)
        return 2

    print(f"Cible : {cfg.redacted()}")
    if cfg.is_ipv6_only_direct:
        print("        (connexion directe Supabase : IPv6 requis)")

    conn = await connect(cfg)
    try:
        await conn.execute("SELECT pg_advisory_lock($1)", LOCK_KEY)
        if args.command == "apply":
            return await cmd_apply(conn)
        if args.command == "status":
            return await cmd_status(conn)
        return await cmd_verify(conn)
    finally:
        try:
            await conn.execute("SELECT pg_advisory_unlock($1)", LOCK_KEY)
        finally:
            await conn.close()


def main() -> int:
    return asyncio.run(main_async(sys.argv[1:]))


if __name__ == "__main__":
    raise SystemExit(main())
