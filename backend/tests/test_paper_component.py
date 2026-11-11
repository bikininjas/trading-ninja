"""Le service PAPER ne peut hériter d'un armement réel ni reprendre une intention inconnue."""

import pytest

from scripts.paper_component import EXCHANGE_SECRETS, engine_state_check, launch_spec, main

DB = "postgresql://postgres:test@127.0.0.1/trading_ninja_review?sslmode=disable"


@pytest.mark.parametrize("component", ["engine", "api", "observer", "watchdog", "dashboard", "learner"])
def test_all_services_force_paper_and_remove_inherited_trading_credentials(component):
    values = {"DATABASE_URL": DB, "TRADING_MODE": "live", "I_UNDERSTAND_REAL_MONEY": "true",
              "EXCHANGE_SANDBOX_MODE": "false", "OBSERVER_WEBHOOK_URL": "https://alerts.example",
              "WATCHDOG_WEBHOOK_URL": "https://alerts.example", **dict.fromkeys(EXCHANGE_SECRETS, "fake")}
    command, env = launch_spec(component, values, {"HTTP_PROXY": "http://proxy.example:8080", "OTHER_APP_SECRET": "fake"})
    assert env["TRADING_MODE"] == "paper" and env["I_UNDERSTAND_REAL_MONEY"] == "false"
    assert env["EXCHANGE_SANDBOX_MODE"] == "true"
    assert all(env[key] == "" for key in EXCHANGE_SECRETS)
    assert "OTHER_APP_SECRET" not in env and env["HTTP_PROXY"] == "http://proxy.example:8080"
    assert "https://alerts.example" not in command
    assert env["DASHBOARD_HOST"] == "127.0.0.1" and env["DASHBOARD_COOKIE_SECURE"] == "true"


@pytest.mark.parametrize("query", ["sslmode=disable", "sslmode=require&sslmode=disable", "sslmode=require-bogus", ""])
def test_remote_sql_tls_cannot_be_disabled_or_overridden(query):
    with pytest.raises(ValueError, match="TLS SQL"):
        launch_spec("engine", {"DATABASE_URL": "postgresql://postgres:fake@db.example/postgres?" + query}, {})


@pytest.mark.parametrize("row", [None,
    {"state": "EMERGENCY_HALT", "requests": False, "exits": False},
    {"state": "EXECUTING", "requests": False, "exits": False},
    {"state": "IDLE", "requests": True, "exits": False},
    {"state": "IN_POSITION", "requests": False, "exits": True},
])
@pytest.mark.asyncio
async def test_restart_requires_known_state_without_unresolved_intent(row):
    class Conn:
        async def fetchrow(self, sql):
            assert "mode='PAPER'" in sql
            return row

    with pytest.raises(ValueError, match="bloquée"):
        await engine_state_check(Conn())


def test_missing_or_public_configuration_refuses_without_executing(tmp_path):
    path = tmp_path / "engine.env"
    assert main(["engine", "--env", str(path), "--check"]) == 2
    path.write_text("DATABASE_URL=" + DB)
    path.chmod(0o644)
    assert main(["engine", "--env", str(path), "--check"]) == 2
    path.chmod(0o600)
    assert main(["engine", "--env", str(path), "--check"]) == 0


@pytest.mark.postgres_integration
@pytest.mark.asyncio
async def test_real_sql_preflight_checks_all_migration_checksums(postgres_test_url, monkeypatch):
    from scripts.paper_component import probe
    import asyncpg
    connect = asyncpg.connect
    async def isolated_connect(**kwargs):
        conn = await connect(**kwargs)
        await conn.execute('SET ROLE tn_paper_observer')
        return conn
    monkeypatch.setattr(asyncpg, 'connect', isolated_connect)
    _, env = launch_spec("observer", {"DATABASE_URL": postgres_test_url,
                                       "OBSERVER_WEBHOOK_URL": "https://alerts.example"}, {})
    result = await probe("observer", env)
    from pathlib import Path
    assert result["database"] == "VERIFIED" and result["migrations"] == len(list((Path(__file__).parents[1] / 'db/migrations').glob('*.sql')))
    assert result['sql_scope'] == 'ISOLATED_PAPER'
