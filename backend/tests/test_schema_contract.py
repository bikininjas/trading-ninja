"""Contrat entre le schéma SQL et le code Python.

Le pare-feu émet des `RejectionCode` qui finissent dans une colonne typée
`risk_rejection_code`. Si les deux listes divergent — même d'une seule valeur — le
système fonctionne parfaitement jusqu'au jour où une décision valide provoque une
erreur d'écriture, c'est-à-dire au pire moment.

Ces tests rendent la divergence impossible à committer : ils lisent le SQL sur disque et
le comparent au `StrEnum` Python, sans base de données ni réseau.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.engine.risk_gate import RejectionCode

MIGRATIONS = Path(__file__).resolve().parents[1] / "db" / "migrations"
INIT_SQL = (MIGRATIONS / "0001_init.sql").read_text(encoding="utf-8")
ALL_SQL = "\n".join(p.read_text(encoding="utf-8") for p in sorted(MIGRATIONS.glob("*.sql")))


def enum_labels(type_name: str) -> set[str]:
    """Libellés d'un ENUM, à travers **toutes** les migrations.

    Le type est créé dans 0001 puis complété par des `ALTER TYPE … ADD VALUE` dans les
    migrations suivantes : ne lire que 0001 donnerait un faux négatif.
    """
    match = re.search(
        rf"CREATE\s+TYPE\s+{type_name}\s+AS\s+ENUM\s*\((.*?)\)\s*;",
        ALL_SQL,
        re.DOTALL | re.IGNORECASE,
    )
    assert match, f"type {type_name} introuvable dans les migrations"
    labels = set(re.findall(r"'([^']*)'", match.group(1)))
    labels |= {
        m.group(1)
        for m in re.finditer(
            rf"ALTER\s+TYPE\s+{type_name}\s+ADD\s+VALUE\s+(?:IF\s+NOT\s+EXISTS\s+)?'([^']*)'",
            ALL_SQL,
            re.IGNORECASE,
        )
    }
    return labels


# ---------------------------------------------------------------------------
# risk_rejection_code : la contrainte la plus critique du système
# ---------------------------------------------------------------------------


def test_python_codes_all_exist_in_sql():
    sql = enum_labels("risk_rejection_code")
    missing = {c.value for c in RejectionCode} - sql
    assert not missing, (
        f"ces codes existent en Python mais pas dans l'ENUM SQL, "
        f"ils feraient échouer l'écriture : {sorted(missing)}"
    )


def test_sql_codes_all_exist_in_python():
    sql = enum_labels("risk_rejection_code")
    python = {c.value for c in RejectionCode}
    orphan = sql - python
    assert not orphan, (
        f"ces codes existent dans l'ENUM SQL mais ne sont produits par aucun code "
        f"Python : {sorted(orphan)}"
    )


def test_rejection_code_has_no_empty_label():
    assert "" not in enum_labels("risk_rejection_code")


# ---------------------------------------------------------------------------
# Écarts assumés par rapport à la spec d'origine
# ---------------------------------------------------------------------------


def test_system_state_includes_executing():
    # La doc décrivait une machine à états passant par EXECUTING, mais l'ENUM du DDL
    # d'origine ne le contenait pas : l'état n'était pas persistable.
    assert "EXECUTING" in enum_labels("system_state")


def test_order_type_supports_binance_spot_stop():
    # STOP_MARKET n'existe pas sur le spot Binance ; le type réellement utilisable
    # est STOP_LOSS (déclenchement au marché). Les deux sont présents.
    order_types = enum_labels("order_type")
    assert "STOP_LOSS" in order_types
    assert "STOP_MARKET" in order_types


def test_order_type_never_used_for_entry_is_declared():
    assert {"MARKET", "LIMIT"} <= enum_labels("order_type")


# ---------------------------------------------------------------------------
# Hygiène des migrations
# ---------------------------------------------------------------------------


def test_migration_files_are_ordered_and_named():
    files = sorted(p.name for p in MIGRATIONS.glob("*.sql"))
    assert files, "aucune migration trouvée"
    for name in files:
        assert re.fullmatch(r"\d{4}_[a-z0-9_]+\.sql", name), f"nom de migration invalide : {name}"


def test_no_migration_uses_timescaledb():
    # Supabase PostgreSQL 17 n'a pas l'extension : une migration qui l'appelle
    # échouerait intégralement.
    for path in MIGRATIONS.glob("*.sql"):
        sql = path.read_text(encoding="utf-8")
        assert "CREATE EXTENSION IF NOT EXISTS \"timescaledb\"" not in sql, (
            f"{path.name} active timescaledb, indisponible sur Supabase PG17"
        )


def test_every_partitioned_table_declares_a_default_partition():
    # Sans partition DEFAULT, un INSERT échoue si la maintenance des partitions a
    # pris du retard — un journal d'audit qui refuse d'écrire arrête le bot.
    for table in ("llm_signals", "risk_evaluations"):
        assert f"CREATE TABLE {table}" in INIT_SQL
        assert re.search(rf"CREATE TABLE {table}_default PARTITION OF {table} DEFAULT", INIT_SQL), (
            f"{table} n'a pas de partition DEFAULT"
        )


@pytest.mark.parametrize(
    "guard",
    [
        "ux_positions_one_open_per_symbol",   # monoposition garantie par la base
        "idx_orders_mode_created",            # coupe-circuit « max ordres / heure »
        "take_profit_price",                  # TP persisté, pas implicite
        "equity_hwm_quote",                   # drawdown depuis le plus haut
        "transfer_status",                    # un sweep raté ne peut pas mentir
    ],
)
def test_guardrails_are_enforced_in_the_database(guard):
    assert guard in INIT_SQL, f"garde-fou absent du schéma : {guard}"


def test_execution_mode_separates_paper_from_live():
    assert enum_labels("execution_mode") == {"PAPER", "TESTNET", "LIVE"}
