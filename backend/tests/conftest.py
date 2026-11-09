"""Les tests unitaires ne doivent jamais contacter un service externe."""

import os
import socket
from urllib.parse import urlsplit

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--postgres-test-url",
        default=None,
        help="PostgreSQL jetable local, base trading_ninja_review uniquement",
    )


@pytest.fixture
def postgres_test_url(request):
    url = request.config.getoption("--postgres-test-url")
    if not url:
        pytest.skip("PostgreSQL local non demandé (--postgres-test-url)")
    parsed = urlsplit(url)
    if (
        parsed.hostname not in {"127.0.0.1", "localhost"}
        or parsed.path != "/trading_ninja_review"
    ):
        pytest.fail(
            "les tests PostgreSQL exigent une base locale trading_ninja_review jetable"
        )
    return url


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    # Ne jamais laisser une clé réelle ou un réglage du shell contaminer un
    # test (ni apparaître dans le diff d'une assertion). Les tests de config
    # fournissent leurs fichiers/overrides et peuvent utiliser monkeypatch.setenv.
    monkeypatch.setattr(os, "environ", {})


@pytest.fixture(autouse=True)
def no_external_connections(monkeypatch, request):
    connect = socket.socket.connect
    connect_ex = socket.socket.connect_ex

    def refuse_connection(self, address):
        if (
            request.node.get_closest_marker("postgres_integration")
            and request.config.getoption("--postgres-test-url")
            and isinstance(address, tuple)
            and address[0] in {"127.0.0.1", "::1"}
        ):
            return connect(self, address)
        raise AssertionError("réseau interdit dans les tests : injecter un double")

    def refuse_connection_ex(self, address):
        if (
            request.node.get_closest_marker("postgres_integration")
            and request.config.getoption("--postgres-test-url")
            and isinstance(address, tuple)
            and address[0] in {"127.0.0.1", "::1"}
        ):
            return connect_ex(self, address)
        raise AssertionError("réseau interdit dans les tests : injecter un double")

    monkeypatch.setattr(socket.socket, "connect", refuse_connection)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse_connection_ex)


def pytest_collection_modifyitems(items):
    for item in items:
        if item.path.name == "test_capital_postgres.py":
            item.add_marker(pytest.mark.postgres_integration)
