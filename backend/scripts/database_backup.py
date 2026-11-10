#!/usr/bin/env python3
"""Sauvegarde logique complète et restauration sur un cluster local neuf uniquement.

Les mots de passe SQL restent dans l'environnement des clients PostgreSQL.
Le dump contient les données privées : répertoire 0700, fichiers 0600, hors Git.
"""

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.db import from_env
from app.core.env import read_dotenv


def quote_identifier(value):
    return '"' + value.replace('"', '""') + '"'


def pg_environment(config):
    # Un service libpq hérité ne doit pas détourner les clients vers une autre
    # base que celle contrôlée par le garde de restauration.
    inherited = {key: value for key, value in os.environ.items() if not key.startswith("PG")}
    return {**inherited, "PGHOST": config.host, "PGPORT": str(config.port),
            "PGUSER": config.user, "PGPASSWORD": config.password, "PGDATABASE": config.database,
            "PGSSLMODE": config.sslmode, "PGCONNECT_TIMEOUT": "10"}


def restore_guard(config):
    if (config.host not in {"127.0.0.1", "localhost", "::1"}
            or config.database != "trading_ninja_restore" or config.user != "tn_restore_admin"):
        raise ValueError("restauration limitée au cluster local neuf trading_ninja_restore, utilisateur tn_restore_admin")


async def client(name, args, config, *, pg_bin=None):
    binary = str(Path(pg_bin) / name) if pg_bin else shutil.which(name)
    if not binary:
        raise ValueError("client PostgreSQL 17 absent : " + name)
    version = await asyncio.create_subprocess_exec(binary, "--version", stdout=asyncio.subprocess.PIPE)
    output, _ = await version.communicate()
    if version.returncode or " 17." not in output.decode():
        raise ValueError("client PostgreSQL 17 requis : " + name)
    process = await asyncio.create_subprocess_exec(binary, *args, env=pg_environment(config),
                                                 stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _, errors = await process.communicate()
    if process.returncode:
        # Le stderr peut contenir données privées, identités et détails réseau.
        raise RuntimeError(f"{name} a échoué ({process.returncode}); aucun succès de sauvegarde/restauration")


async def inventory(conn):
    await conn.execute("SET TIME ZONE 'UTC'")
    tables = await conn.fetch(
        "SELECT n.nspname AS schema,c.relname AS name,c.relkind::text AS relkind,c.relrowsecurity,c.relforcerowsecurity,"
        "pg_get_userbyid(c.relowner) AS owner,"
        "ARRAY(SELECT a::text FROM unnest(COALESCE(c.relacl,acldefault('r',c.relowner))) a ORDER BY a::text) AS acl,c.reloptions "
        "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE c.relkind IN ('r','p','v','m') AND n.nspname NOT IN ('pg_catalog','information_schema') "
        "AND n.nspname NOT LIKE 'pg_toast%' AND n.nspname NOT LIKE 'pg_temp_%' ORDER BY n.nspname,c.relname"
    )
    records = []
    for table in tables:
        item = dict(table)
        if table["relkind"] in {"r", "p", "m"}:
            relation = quote_identifier(table["schema"]) + "." + quote_identifier(table["name"])
            fingerprint = await conn.fetchrow(
                "SELECT count(*) AS rows,md5(COALESCE(string_agg(h,'' ORDER BY h),'')) AS rows_digest "
                f"FROM (SELECT md5(to_jsonb(t)::text) AS h FROM {relation} t) fingerprints"
            )
            item.update(dict(fingerprint))
        records.append(item)
    roles = [dict(r) for r in await conn.fetch(
        "SELECT rolname,rolsuper,rolinherit,rolcreaterole,rolcreatedb,rolcanlogin,rolreplication,"
        "rolbypassrls,rolconnlimit,rolvaliduntil::text,rolconfig FROM pg_roles WHERE rolname NOT LIKE 'pg_%' ORDER BY rolname"
    )]
    extensions = [dict(r) for r in await conn.fetch("SELECT extname,extversion FROM pg_extension ORDER BY extname")]
    return {"relations": records, "roles": roles, "extensions": extensions}


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


async def create(config, directory, *, pg_bin=None):
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    conn = await asyncpg.connect(**config.asyncpg_kwargs())
    try:
        if int(await conn.fetchval("SHOW server_version_num")) // 10000 != 17:
            raise ValueError("PostgreSQL source 17 requis")
        async with conn.transaction(isolation="repeatable_read", readonly=True):
            snapshot = await conn.fetchval("SELECT pg_export_snapshot()")
            before = await inventory(conn)
            await client("pg_dump", ["--format=custom", "--snapshot=" + snapshot,
                                     "--file=" + str(directory / "database.dump")], config, pg_bin=pg_bin)
            await client("pg_dumpall", ["--roles-only", "--no-role-passwords",
                                        "--file=" + str(directory / "roles.sql")], config, pg_bin=pg_bin)
        # Les rôles sont globaux : détecter une modification pendant leur export.
        after_roles = [dict(r) for r in await conn.fetch(
            "SELECT rolname,rolsuper,rolinherit,rolcreaterole,rolcreatedb,rolcanlogin,rolreplication,"
            "rolbypassrls,rolconnlimit,rolvaliduntil::text,rolconfig FROM pg_roles WHERE rolname NOT LIKE 'pg_%' ORDER BY rolname"
        )]
        if before["roles"] != after_roles:
            raise ValueError("rôles modifiés pendant la sauvegarde")
        manifest = {"format": 1, "created_at": datetime.now(timezone.utc).isoformat(),
                    "scope": "all_database_schemas_data_roles_acl_except_role_passwords",
                    "source_database": config.database, "inventory": before,
                    "files": {name: sha256(directory / name) for name in ("database.dump", "roles.sql")},
                    "restoration": "NOT_VERIFIED"}
        (directory / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        for file in directory.iterdir():
            file.chmod(0o600)
        return {"status": "BACKUP_CREATED", "restoration": "NOT_VERIFIED", "directory": str(directory),
                "relations": len(before["relations"]), "files": manifest["files"]}
    finally:
        await conn.close(timeout=5)


async def restore(config, directory, *, pg_bin=None):
    restore_guard(config)
    manifest = json.loads((directory / "manifest.json").read_text())
    if manifest.get("format") != 1 or set(manifest.get("files", {})) != {"database.dump", "roles.sql"}:
        raise ValueError("manifest de sauvegarde invalide")
    for name, expected in manifest["files"].items():
        if sha256(directory / name) != expected:
            raise ValueError("empreinte de sauvegarde divergente")
    if any(r["rolname"] == "tn_restore_admin" for r in manifest["inventory"]["roles"]):
        raise ValueError("conflit avec le rôle bootstrap de restauration")
    conn = await asyncpg.connect(**config.asyncpg_kwargs())
    try:
        existing = await inventory(conn)
        if existing["relations"] or [r["rolname"] for r in existing["roles"]] != ["tn_restore_admin"]:
            raise ValueError("cluster de restauration neuf requis ; aucun nettoyage automatique")
        await client("psql", ["--no-psqlrc", "--set=ON_ERROR_STOP=on", "--file=" + str(directory / "roles.sql")], config, pg_bin=pg_bin)
        await client("pg_restore", ["--exit-on-error", "--dbname=" + config.database,
                                    str(directory / "database.dump")], config, pg_bin=pg_bin)
        actual = await inventory(conn)
        actual["roles"] = [r for r in actual["roles"] if r["rolname"] != "tn_restore_admin"]
        if actual != manifest["inventory"]:
            raise ValueError("inventaire restauré divergent (données, rôles, droits ou extensions)")
        report = {"status": "RESTORE_VERIFIED", "verified_at": datetime.now(timezone.utc).isoformat(),
                  "relations": len(actual["relations"]), "files": manifest["files"]}
        (directory / "restore-verification.json").write_text(json.dumps(report, indent=2) + "\n")
        (directory / "restore-verification.json").chmod(0o600)
        return report
    finally:
        await conn.close(timeout=5)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("create", "restore-test"))
    parser.add_argument("--env", type=Path, required=True, help="fichier privé contenant DATABASE_URL ou RESTORE_DATABASE_URL")
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--pg-bin", help="répertoire des clients PostgreSQL 17")
    args = parser.parse_args(argv)
    try:
        if args.env.stat().st_mode & 0o077:
            raise ValueError("fichier privé 0600/0400 requis")
        values = read_dotenv(args.env)
        key = "DATABASE_URL" if args.action == "create" else "RESTORE_DATABASE_URL"
        config = from_env({"DATABASE_URL": values.get(key, "")})
        # Les fichiers partiels restent privés même si un client échoue.
        os.umask(0o077)
        fn = create if args.action == "create" else restore
        result = asyncio.run(fn(config, args.directory, pg_bin=args.pg_bin))
        print(json.dumps(result))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "FAILED", "error_type": type(exc).__name__}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
