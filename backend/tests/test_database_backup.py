"""La restauration optionnelle refuse une cible réelle et vérifie les données."""

from decimal import Decimal

import asyncpg
import pytest

from app.core.db import from_env
from scripts.database_backup import inventory, pg_environment, restore_guard


@pytest.mark.parametrize(("host", "database", "user"), [
    ("db.example", "trading_ninja_restore", "tn_restore_admin"),
    ("127.0.0.1", "postgres", "tn_restore_admin"),
    ("127.0.0.1", "trading_ninja_restore", "postgres"),
])
def test_restore_refuses_remote_or_non_disposable_database(host, database, user):
    config = from_env({"DATABASE_URL": f"postgresql://{user}:fake@{host}/{database}?sslmode=disable"})
    with pytest.raises(ValueError, match="cluster local neuf"):
        restore_guard(config)


def test_password_is_an_environment_value_not_a_connection_argument(monkeypatch):
    monkeypatch.setenv("PGSERVICE", "unrelated-database")
    monkeypatch.setenv("PGHOSTADDR", "192.0.2.1")
    config = from_env({"DATABASE_URL": "postgresql://postgres:fake-secret@127.0.0.1/test?sslmode=disable"})
    env = pg_environment(config)
    assert env["PGPASSWORD"] == "fake-secret" and env["PGDATABASE"] == "test"
    assert "fake-secret" not in config.redacted()
    assert "PGSERVICE" not in env and "PGHOSTADDR" not in env


@pytest.mark.postgres_integration
@pytest.mark.asyncio
async def test_inventory_fingerprints_actual_decimal_rows(postgres_test_url):
    conn = await asyncpg.connect(postgres_test_url)
    tx = conn.transaction()
    await tx.start()
    try:
        first = await inventory(conn)
        await conn.execute("UPDATE bot_state SET equity_hwm_quote=$1 WHERE mode='TESTNET'", Decimal('9.37'))
        second = await inventory(conn)
        before = next(r for r in first["relations"] if r["name"] == "bot_state")
        after = next(r for r in second["relations"] if r["name"] == "bot_state")
        assert before["rows"] == after["rows"] and before["rows_digest"] != after["rows_digest"]
        assert after["acl"] and after["owner"]
    finally:
        await tx.rollback()
        await conn.close()
