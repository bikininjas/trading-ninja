"""Tests de la résolution de connexion base (aucun accès réseau)."""

from __future__ import annotations

import pytest

from app.core.db import DatabaseConfig, DatabaseConfigError, from_env

SUPABASE_ENV = {
    "NEXT_PUBLIC_SUPABASE_URL": "https://abcdefghijklmnopqrst.supabase.co",
    "SUPABASE_TRNINJA_PWD": "s3cr3t/p@ss:w0rd",
}


def test_explicit_database_url_wins():
    cfg = from_env({"DATABASE_URL": "postgresql://u:p@h.example:6543/db", "SUPABASE_TRNINJA_PWD": "x"})
    assert (cfg.host, cfg.port, cfg.user, cfg.database) == ("h.example", 6543, "u", "db")


def test_defaults_filled_from_url():
    cfg = from_env({"DATABASE_URL": "postgresql://postgres:pw@localhost/db"})
    assert cfg.port == 5432
    assert cfg.sslmode == "require"


def test_sslmode_read_from_query():
    cfg = from_env({"DATABASE_URL": "postgresql://u:p@h:5432/db?sslmode=disable"})
    assert cfg.sslmode == "disable"
    assert cfg.asyncpg_kwargs()["ssl"] is None


def test_password_with_special_characters_is_url_encoded():
    cfg = DatabaseConfig(host="h", user="postgres", database="postgres", password="s3cr3t/p@ss:w0rd")
    url = cfg.sqlalchemy_url()
    assert "s3cr3t%2Fp%40ss%3Aw0rd" in url
    assert cfg.password not in url


def test_password_never_leaks_in_text_representations():
    cfg = DatabaseConfig(host="h", user="postgres", database="postgres", password="TOPSECRET")
    assert "TOPSECRET" not in cfg.redacted()
    assert "TOPSECRET" not in repr(cfg)


def test_derived_from_supabase_project():
    cfg = from_env(SUPABASE_ENV)
    assert cfg.host == "db.abcdefghijklmnopqrst.supabase.co"
    assert cfg.user == "postgres"
    assert cfg.database == "postgres"
    assert cfg.password == "s3cr3t/p@ss:w0rd"
    assert cfg.is_ipv6_only_direct


def test_pooler_switch_uses_project_scoped_user():
    cfg = from_env({**SUPABASE_ENV, "SUPABASE_USE_POOLER": "true", "SUPABASE_POOLER_REGION": "eu-west-3"})
    assert cfg.host == "aws-0-eu-west-3.pooler.supabase.com"
    assert cfg.user == "postgres.abcdefghijklmnopqrst"
    assert cfg.is_pooled
    # Prepared statements interdits derrière un pooler en mode transaction.
    assert cfg.asyncpg_kwargs()["statement_cache_size"] == 0


def test_missing_password_raises():
    with pytest.raises(DatabaseConfigError, match="Mot de passe"):
        from_env({"NEXT_PUBLIC_SUPABASE_URL": SUPABASE_ENV["NEXT_PUBLIC_SUPABASE_URL"]})


def test_missing_everything_raises():
    with pytest.raises(DatabaseConfigError):
        from_env({})


def test_malformed_project_url_raises():
    with pytest.raises(DatabaseConfigError, match="NEXT_PUBLIC_SUPABASE_URL"):
        from_env({"NEXT_PUBLIC_SUPABASE_URL": "https://example.com", "SUPABASE_TRNINJA_PWD": "p"})
