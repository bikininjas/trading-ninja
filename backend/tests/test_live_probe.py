"""La sonde ne constitue jamais un chemin de trading hors journal."""

from decimal import Decimal as D
from types import SimpleNamespace

import ccxt
import pytest

from scripts import live_probe


@pytest.mark.parametrize('response', ['validated', 'rejected', 'timeout'])
def test_probe_always_requests_validation_and_never_places_a_compensating_sale(monkeypatch,response):
    calls = []
    class Exchange:
        markets = {'BTC/EUR':{}}
        def load_markets(self): pass
        def fetch_balance(self): return {'free':{'EUR':'10'}}
        def fetch_ticker(self,symbol): return {'last':'100'}
        def fetch_open_orders(self,symbol): return []
        def create_order(self,*args):
            calls.append(args)
            if response == 'rejected': raise ccxt.InvalidOrder('minimum volume')
            if response == 'timeout': raise ccxt.RequestTimeout('coupure')
            return {}
    config = SimpleNamespace(mode='paper', exchange=SimpleNamespace(api_key='fake',secret='fake',
                             symbol='BTC/EUR',quote_currency='EUR'))
    monkeypatch.setattr(live_probe,'load_settings',lambda:config)
    monkeypatch.setattr(live_probe,'build_exchange',lambda **kw:Exchange())
    monkeypatch.setattr(live_probe,'_invalid_amount',lambda *a:D('.0001'))
    assert live_probe._run(None) == (1 if response == 'timeout' else 0)
    assert len(calls) == 1
    assert calls[0][-1] == {'validate':True}
    assert calls[0][2] == 'buy'
