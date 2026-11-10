"""Préflight public : pas de clé et refus des données OHLC périmées."""

from decimal import Decimal
from types import SimpleNamespace
import sys

import pytest

from app.engine.risk_gate import ExchangeConstraints
from scripts import check_kraken


class PublicExchange:
    def __init__(self):
        self.session = SimpleNamespace(trust_env=False)
        self.now = 100 * 14_400_000
        self.candles = [
            [i * 14_400_000, 100, 102, 98, 100, 10]
            for i in range(84, 101)
        ]

    def load_markets(self):
        pass

    def milliseconds(self):
        return self.now

    def parse_timeframe(self, timeframe):
        assert timeframe == "4h"
        return 14_400

    def fetch_ohlcv(self, symbol, timeframe, limit):
        return self.candles

    def fetch_ticker(self, symbol):
        return {"last": "100"}


def test_public_live_risk_never_loads_credentials(monkeypatch, capsys):
    exchange = PublicExchange()
    credentials = []
    monkeypatch.setattr(check_kraken, "load_env", lambda: pytest.fail("accès aux secrets"))
    monkeypatch.setattr(check_kraken, "build_exchange", lambda **kw: credentials.append(kw) or exchange)
    monkeypatch.setattr(check_kraken, "market_info", lambda *a, **kw: SimpleNamespace(
        quote="EUR", constraints=ExchangeConstraints(
            amount_step=Decimal("0.001"), min_amount=Decimal("0.001"),
            min_notional=Decimal("10"), price_tick=Decimal("0.01"))))
    monkeypatch.setattr(sys, "argv", ["check_kraken.py", "--public-live-risk", "--affordable",
                                     "--equity", "9.37", "--profile", "prudent",
                                     "--symbol", "BTC/EUR"])
    assert check_kraken.main() == 1  # minimum non finançable
    assert credentials == [{"api_key": "", "secret": ""}]
    assert exchange.session.trust_env
    assert "aucun compte ni ordre" in capsys.readouterr().out


def test_diagnostic_refuses_stale_and_discontinuous_candles():
    exchange = PublicExchange()
    assert len(check_kraken._closed_candles(exchange, "BTC/EUR")) == 16
    exchange.candles = exchange.candles[:-2]
    with pytest.raises(ValueError, match="dernière bougie"):
        check_kraken._closed_candles(exchange, "BTC/EUR")
    exchange = PublicExchange()
    del exchange.candles[-5]
    with pytest.raises(ValueError, match="trou"):
        check_kraken._closed_candles(exchange, "BTC/EUR")
