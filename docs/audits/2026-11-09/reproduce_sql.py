import asyncio, json, sys
from decimal import Decimal as D
from pathlib import Path
import argparse
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))
import asyncpg
from app.db.repository import PostgresRepository

parser = argparse.ArgumentParser(
    description="Reproductions SQL locales, toutes les écritures annulées"
)
parser.add_argument("--postgres-test-url", required=True)
DSN = parser.parse_args().postgres_test_url
parsed = urlsplit(DSN)
assert (
    parsed.hostname in ("127.0.0.1", "localhost")
    and parsed.path == "/trading_ninja_review"
), "base locale jetable uniquement"


async def main():
    conn = await asyncpg.connect(DSN)
    tx = conn.transaction()
    await tx.start()
    repo = PostgresRepository(conn)
    try:
        await conn.execute("SET LOCAL ROLE anon")
        created = await conn.fetchval("SELECT public.ensure_partitions(7)")
        assert created == 2
        await conn.execute("RESET ROLE")
        await repo.ensure_mode("PAPER")
        pid = await repo.create_position(
            "PAPER",
            symbol="BTC/EUR",
            entry_price=D(100),
            amount=D(1),
            notional_quote=D(100),
            stop_loss_price=D(95),
            take_profit_price=D(110),
            risk_amount_quote=D(5),
            r_multiple_at_tp=D(2),
            signal_id=None,
            evaluation_id=None,
            protective_order_id="test-stop",
        )
        await conn.execute(
            "INSERT INTO orders(position_id,client_order_id,symbol,mode,side,order_type,purpose,status,requested_amount,filled_amount,average_fill_price,fee_amount,fee_currency) VALUES($1,'audit-canceled-partial','BTC/EUR','PAPER','SELL','MARKET','LLM_EXIT','CANCELED',1,.4,110,.1,'EUR')",
            pid,
        )
        data = await repo.diagnostics_input("PAPER", pid)
        assert not data["orders"]
        print(
            json.dumps(
                {
                    "A09_ANON_SECURITY_DEFINER": {
                        "anon_created_partitions": created,
                        "rolled_back": True,
                    },
                    "A10_DIAGNOSTICS_CANCELED_PARTIAL": {
                        "actual_filled_amount": ".4",
                        "diagnostic_order_count": len(data["orders"]),
                        "rolled_back": True,
                    },
                },
                indent=2,
            )
        )
    finally:
        await tx.rollback()
        await conn.close()


asyncio.run(main())
