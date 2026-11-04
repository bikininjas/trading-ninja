"""Résolution de la connexion PostgreSQL (Supabase).

Deux sources possibles, dans cet ordre :

1. `DATABASE_URL` explicite — prioritaire, jamais réécrite.
2. Dérivation depuis les variables Supabase (`NEXT_PUBLIC_SUPABASE_URL`,
   `SUPABASE_*_PWD`) quand `DATABASE_URL` est absent.

Le mot de passe n'apparaît dans aucune représentation texte de l'objet : les
journaux et les messages d'erreur utilisent `redacted()`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Mapping
from urllib.parse import quote, unquote, urlsplit

__all__ = ["DatabaseConfig", "DatabaseConfigError", "from_env"]

DEFAULT_PORT = 5432
PROJECT_URL_RE = re.compile(r"^https?://([a-z0-9]{20})\.supabase\.(co|in)$")


class DatabaseConfigError(RuntimeError):
    """Configuration de base incomplète ou incohérente."""


@dataclass(frozen=True, slots=True)
class DatabaseConfig:
    host: str
    user: str
    database: str
    password: str = field(repr=False)
    port: int = DEFAULT_PORT
    sslmode: str = "require"

    # -- représentations -----------------------------------------------------

    def redacted(self) -> str:
        return f"postgresql://{self.user}:***@{self.host}:{self.port}/{self.database}?sslmode={self.sslmode}"

    def asyncpg_kwargs(self) -> dict[str, object]:
        """Paramètres pour `asyncpg.connect(**kwargs)`.

        `statement_cache_size=0` est obligatoire derrière un pooler en mode
        transaction (Supavisor, port 6543) : les prepared statements y sont
        réutilisés entre clients et provoquent des erreurs aléatoires.
        """
        kwargs: dict[str, object] = {
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "password": self.password,
            "database": self.database,
            "ssl": self.sslmode if self.sslmode not in ("disable", "allow") else None,
            "timeout": 15,
            "command_timeout": 60,
        }
        if "pooler.supabase.com" in self.host:
            kwargs["statement_cache_size"] = 0
        return kwargs

    def sqlalchemy_url(self) -> str:
        """URL SQLAlchemy (asyncpg). Le mot de passe est encodé pour l'URL."""
        return (
            f"postgresql+asyncpg://{quote(self.user, safe='')}:{quote(self.password, safe='')}"
            f"@{self.host}:{self.port}/{self.database}"
        )

    def sqlalchemy_connect_args(self) -> dict[str, object]:
        args: dict[str, object] = {}
        if self.sslmode not in ("disable", "allow"):
            args["ssl"] = self.sslmode
        if "pooler.supabase.com" in self.host:
            args["statement_cache_size"] = 0
        return args

    @property
    def is_pooled(self) -> bool:
        return "pooler.supabase.com" in self.host

    @property
    def is_ipv6_only_direct(self) -> bool:
        """Connexion directe Supabase = AAAA uniquement sur les projets récents."""
        return self.host.endswith(".supabase.co")


def _pick(env: Mapping[str, str], *names: str) -> str | None:
    for name in names:
        value = env.get(name)
        if value:
            return value
    return None


def _from_url(url: str) -> DatabaseConfig:
    parts = urlsplit(url)
    if not parts.hostname:
        raise DatabaseConfigError("DATABASE_URL malformée : hôte introuvable")
    scheme = parts.scheme.split("+")[0]
    if scheme not in ("postgresql", "postgres"):
        raise DatabaseConfigError(f"DATABASE_URL doit être postgresql://, reçu {parts.scheme!r}")
    query = dict(
        pair.split("=", 1) for pair in parts.query.split("&") if "=" in pair
    )
    return DatabaseConfig(
        host=parts.hostname,
        port=parts.port or DEFAULT_PORT,
        user=unquote(parts.username or "postgres"),
        password=unquote(parts.password or ""),
        database=(parts.path or "/postgres").lstrip("/") or "postgres",
        sslmode=query.get("sslmode", "require"),
    )


def from_env(env: Mapping[str, str]) -> DatabaseConfig:
    """Construit la configuration de connexion. Lève si rien d'exploitable."""
    url = env.get("DATABASE_URL")
    if url:
        return _from_url(url)

    project_url = _pick(env, "NEXT_PUBLIC_SUPABASE_URL", "SUPABASE_URL")
    password = _pick(
        env,
        "SUPABASE_DB_PASSWORD",
        "SUPABASE_TRNINJA_PWD",
        "SUPABASE_PWD",
        "SUPABASE_PASSWORD",
        "POSTGRES_PASSWORD",
    )
    if not project_url:
        raise DatabaseConfigError(
            "DATABASE_URL absent et NEXT_PUBLIC_SUPABASE_URL introuvable : "
            "impossible de localiser la base."
        )
    if not password:
        raise DatabaseConfigError(
            "Mot de passe de base introuvable (SUPABASE_DB_PASSWORD / SUPABASE_*_PWD)."
        )

    match = PROJECT_URL_RE.match(project_url.strip())
    if not match:
        raise DatabaseConfigError(f"NEXT_PUBLIC_SUPABASE_URL inattendue : {project_url!r}")
    project_ref = match.group(1)

    host = _pick(env, "SUPABASE_DB_HOST") or f"db.{project_ref}.supabase.co"
    # La connexion directe Supabase récente est IPv6-only. Le pooler est la
    # solution si le réseau d'exécution est IPv4 (Docker sans IPv6, VPS...).
    if host == "pooler" or env.get("SUPABASE_USE_POOLER", "").lower() in {"1", "true", "yes"}:
        region = env.get("SUPABASE_POOLER_REGION", "eu-west-1")
        host = f"aws-0-{region}.pooler.supabase.com"

    user = _pick(env, "SUPABASE_DB_USER") or (
        f"postgres.{project_ref}" if "pooler.supabase.com" in host else "postgres"
    )

    return DatabaseConfig(
        host=host,
        port=int(env.get("SUPABASE_DB_PORT") or DEFAULT_PORT),
        user=user,
        password=password,
        database=env.get("SUPABASE_DB_NAME") or "postgres",
        sslmode=env.get("SUPABASE_DB_SSLMODE") or "require",
    )
