from decimal import Decimal as D

import pytest

from app.api.feasibility import estimate


class Exchange:
    def market(self, symbol):
        return {'symbol': symbol, 'id': 'XBT', 'base': 'BTC', 'quote': 'EUR', 'active': True,
                'spot': True, 'precision': {'amount': '0.01', 'price': '0.01'},
                'limits': {'amount': {'min': '0.02'}, 'cost': {'min': '5'}}, 'taker': '.008'}

    def fetch_ticker(self, symbol):
        return {'last': '100', 'timestamp': 1000}


def test_minimum_rounded_to_step_and_fee_uses_applied_rates():
    result = estimate(Exchange(), 'BTC/EUR', 'EUR', D('5.05'),
                      {'risk': {'execution_fee_pct': '.008', 'execution_slippage_pct': '.01'}})
    assert result['minimum_quantity'] == D('.05')
    assert result['minimum_order_quote'] == D('5')
    assert result['estimated_entry_fee'] == D('.0404')
    assert result['estimated_entry_total'] == D('5.0904')
    assert result['cash_sufficient'] is False


def test_missing_fee_or_balance_never_produces_cash_verdict():
    missing_fee = estimate(Exchange(), 'BTC/EUR', 'EUR', D('100'), None)
    missing_balance = estimate(Exchange(), 'BTC/EUR', 'EUR', None,
                               {'risk': {'execution_fee_pct': '.008', 'execution_slippage_pct': '0'}})
    assert missing_fee['cash_sufficient'] is None and missing_fee['estimated_entry_fee'] is None
    assert missing_balance['cash_sufficient'] is None and missing_balance['free_quote'] is None


def test_invalid_price_fails_closed():
    exchange = Exchange()
    exchange.fetch_ticker = lambda symbol: {'last': 'NaN'}
    with pytest.raises(ValueError):
        estimate(exchange, 'BTC/EUR', 'EUR', D('100'), None)
