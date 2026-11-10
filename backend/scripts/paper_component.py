#!/usr/bin/env python3
"""Lance un composant PAPER depuis un fichier privé, avec mode et clés verrouillés."""

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from urllib.parse import parse_qs, urlsplit

import asyncpg
import httpx

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from app.core.env import read_dotenv

COMPONENTS = ("engine", "api", "dashboard", "observer", "watchdog")
EXCHANGE_SECRETS = (
    "EXCHANGE_API_KEY", "EXCHANGE_SECRET", "KRAKEN_API_KEY", "KRAKEN_SECRET",
    "KRAKEN_SECRET_KEY", "BINANCE_API_KEY", "BINANCE_SECRET_KEY",
    "OBSERVER_API_KEY", "OBSERVER_SECRET",
)
SQL_ROLES = {'engine': 'tn_paper_engine', 'api': 'tn_paper_api',
             'observer': 'tn_paper_observer', 'watchdog': 'tn_paper_probe'}


async def verify_sql_role(conn, component):
    expected = SQL_ROLES[component]
    row = await conn.fetchrow(
        "SELECT rolsuper,rolbypassrls,rolcreaterole,rolcreatedb, "
        "has_schema_privilege(current_user,'public','CREATE') AS schema_write, "
        "has_function_privilege(current_user,'ensure_partitions(integer)','EXECUTE') AS maintenance "
        "FROM pg_roles WHERE rolname=current_user")
    memberships = [name for name in SQL_ROLES.values()
                   if await conn.fetchval('SELECT pg_has_role(current_user,$1,\'MEMBER\')', name)]
    if row is None or any(row.values()) or memberships != [expected]:
        raise ValueError('identité SQL PAPER isolée requise pour ce composant')
    if await conn.fetchval("SELECT EXISTS(SELECT 1 FROM bot_state WHERE mode<>'PAPER')"):
        raise ValueError('isolation SQL PAPER absente')


def launch_spec(component, values, inherited=None):
    if component not in COMPONENTS:
        raise ValueError("composant inconnu")
    original = os.environ if inherited is None else inherited
    keep = {"PATH", "HOME", "LANG", "LC_ALL", "TZ", "STATE_DIRECTORY", "TN_STATE_DIR",
            "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy", "no_proxy",
            "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "NODE_EXTRA_CA_CERTS"}
    env = {key: value for key, value in original.items() if key in keep}
    env.update(values)
    # Écrase aussi les éventuels secrets des .env historiques au chargement.
    env.update({key: "" for key in EXCHANGE_SECRETS})
    env.update(TRADING_MODE="paper", I_UNDERSTAND_REAL_MONEY="false",
               EXCHANGE_SANDBOX_MODE="true", EXCHANGE_ID="kraken",
               DASHBOARD_HOST="127.0.0.1", DASHBOARD_ALLOW_REMOTE="false",
               DASHBOARD_COOKIE_SECURE="true", API_BASE_URL="http://127.0.0.1:8080")
    state = Path(env.get("STATE_DIRECTORY") or env.get("TN_STATE_DIR") or BACKEND.parent / "state")
    python = str(BACKEND / ".venv/bin/python")
    if component != "dashboard":
        if not values.get("DATABASE_URL"):
            raise ValueError("DATABASE_URL requis dans le fichier privé du composant")
        parsed = urlsplit(values["DATABASE_URL"])
        if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname:
            raise ValueError("DATABASE_URL PostgreSQL invalide")
        if parsed.hostname not in {"127.0.0.1", "localhost", "::1"} and parse_qs(parsed.query).get("sslmode") not in (["require"], ["verify-ca"], ["verify-full"]):
            raise ValueError("TLS SQL requis pour une base distante")
    if component in {"observer", "watchdog"}:
        name = "OBSERVER_WEBHOOK_URL" if component == "observer" else "WATCHDOG_WEBHOOK_URL"
        parsed = urlsplit(values.get(name, ""))
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError(name + " HTTPS requis dans le fichier privé")
    commands = {
        "engine": [python, "-m", "app.main", "--serve"],
        "api": [python, "-m", "uvicorn", "app.api.main:create_app", "--factory",
                "--host", "127.0.0.1", "--port", "8080"],
        "dashboard": [shutil.which("node") or "/usr/bin/node", str(BACKEND.parent / "dashboard/server.js")],
        "observer": [python, "scripts/insights.py", "watch", "--serve", "--expect-runtime",
                     "--notification-state", str(state / "observer-paper.sqlite3")],
        "watchdog": [python, "scripts/watchdog.py", "--mode", "PAPER", "--interval", "15",
                     "--notification-state", str(state / "watchdog-paper.sqlite3")],
    }
    return commands[component], env


async def engine_state_check(conn):
    row = await conn.fetchrow(
        "SELECT state::text AS state, "
        "EXISTS(SELECT 1 FROM execution_requests WHERE mode='PAPER' AND state IN ('PREPARED','ACKNOWLEDGED','FILLED','UNKNOWN')) AS requests, "
        "EXISTS(SELECT 1 FROM pending_exits WHERE mode='PAPER') AS exits "
        "FROM bot_state WHERE mode='PAPER'"
    )
    if not row or row["state"] not in {"IDLE", "IN_POSITION", "COOLDOWN"} or row["requests"] or row["exits"]:
        raise ValueError("reprise PAPER bloquée : état ou intentions à rapprocher")


async def probe(component, env):
    from app.core.db import from_env
    from app.core.settings import load_settings

    result = {"component": component, "mode": "PAPER"}
    if component != "dashboard":
        kwargs = from_env(env).asyncpg_kwargs()
        kwargs.update(timeout=5, command_timeout=5)
        conn = await asyncpg.connect(**kwargs)
        try:
            expected = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (BACKEND / "db/migrations").glob("*.sql")}
            actual = {r["version"]: r["checksum"] for r in await conn.fetch("SELECT version,checksum FROM schema_migrations")}
            if actual != expected:
                raise ValueError("migrations absentes ou divergentes")
            await verify_sql_role(conn, component)
            if component == "engine":
                await engine_state_check(conn)
            size = await conn.fetchval("SELECT pg_database_size(current_database())")
            if size > int(env.get("SUPABASE_DB_QUOTA_MB", "500")) * 1_000_000 - 100_000_000:
                raise ValueError("plancher de stockage non respecté")
            result.update(database="VERIFIED", database_bytes=size, migrations=len(actual), sql_scope="ISOLATED_PAPER")
        finally:
            await conn.close(timeout=2)
    if component == "engine":
        settings = load_settings(overrides=env, use_environment=False)
        root = settings.llm.base_url.removesuffix("/v1").rstrip("/")
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(root + "/api/tags")
            response.raise_for_status()
        models = response.json().get("models", [])
        matching = [m for m in models if m.get("name") == settings.llm.model and m.get("digest")]
        if len(matching) != 1:
            raise ValueError("modèle Ollama absent ou identité inconnue")
        result["model_digest"] = matching[0]["digest"]
    if component == "api":
        from app.api.security import require_secrets
        require_secrets(env.get("JWT_SECRET_KEY", ""), env.get("TOTP_SECRET", ""), env.get("ADMIN_PASSWORD_HASH", ""))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("component", choices=COMPONENTS)
    parser.add_argument("--env", required=True, type=Path)
    parser.add_argument("--check", action="store_true", help="valide la configuration, sans lancer de processus")
    parser.add_argument("--probe", action="store_true", help="lectures SQL et modèle, sans ordre ni écriture")
    args = parser.parse_args(argv)
    try:
        if not args.env.is_file() or args.env.stat().st_mode & 0o077:
            raise ValueError("fichier de configuration privé requis (0600 ou 0400)")
        command, env = launch_spec(args.component, read_dotenv(args.env))
        if args.probe:
            print(json.dumps(asyncio.run(probe(args.component, env))))
            return 0
        if args.check:
            print(f"{args.component}: configuration PAPER valide ; aucune connexion ni exécution")
            return 0
        if args.component == "engine":
            asyncio.run(probe(args.component, env))
        os.chdir(BACKEND)
        os.execve(command[0], command, env)
    except Exception as exc:
        print(f"REFUS {args.component}: {type(exc).__name__} — vérifier fichier privé et dépendances", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
