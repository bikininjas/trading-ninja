"""Tests du choix de broker : papier par défaut, live seulement sur demande."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.execution.factory import make_broker
from app.execution.live import LiveBroker
from app.execution.paper import PaperBroker


class FeeExchange:
    def fetch_trading_fee(self, symbol):
        return {"taker": "0.008", "maker": "0.004"}


def settings(mode: str = "paper"):
    return SimpleNamespace(
        mode=mode,
        paper_slippage_bps=5,
        exchange=SimpleNamespace(
            allowed_symbols=("BTC/EUR",),
            taker_fee=Decimal("0.0026"),
            quote_currency="EUR",
        ),
    )


def test_paper_mode_builds_the_paper_broker():
    broker = make_broker(settings("paper"), FeeExchange())
    assert isinstance(broker, PaperBroker)


def test_live_mode_builds_the_real_broker():
    broker = make_broker(settings("live"), FeeExchange(), repo=object())
    assert isinstance(broker, LiveBroker)


def test_testnet_is_refused_because_kraken_has_no_testnet_in_ccxt():
    with pytest.raises(RuntimeError, match="testnet"):
        make_broker(settings("testnet"), FeeExchange())


def test_live_factory_refuses_missing_durable_repository():
    with pytest.raises(RuntimeError,match='journal durable'):
        make_broker(settings('live'),FeeExchange())
