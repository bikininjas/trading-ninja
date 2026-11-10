from datetime import datetime, timezone, timedelta
from decimal import Decimal as D

from scripts.qualify_economics import buy_and_hold


def data(minimum, symbols=('BTC/EUR',)):
    return {s: {'market': {'amount_step': '.01', 'price_tick': '.01', 'min_amount': '.01',
                        'min_notional': minimum, 'taker_fee': '.008'},
                'candles': {'1h': [[0, 100, 100, 100, 100, 1], [3_600_000, 100, 100, 100, 100, 1]]}}
            for s in symbols}


def test_unfinanceable_benchmark_keeps_all_cash_without_rounding_loss():
    start = datetime.fromtimestamp(3600, timezone.utc)
    result = buy_and_hold(data('4', ('BTC/EUR', 'ETH/EUR', 'SOL/EUR')),
                         start, start + timedelta(hours=1), D('9.37'), D('.008'), 5)
    assert D(result['equity_end']) == D('9.37')
    assert D(result['net_return']) == 0
    assert all(not leg['financed'] for leg in result['legs'])


def test_financed_benchmark_includes_both_fees_slippage_and_cash_remainder():
    start = datetime.fromtimestamp(3600, timezone.utc)
    result = buy_and_hold(data('1'), start, start + timedelta(hours=1), D(100), D('.01'), 10)
    quantity = D(result['legs'][0]['quantity'])
    assert quantity == D('.98')
    assert D(result['equity_end']) == 100 - quantity * 100 * D('1.001') * D('1.01') + quantity * 100 * D('.999') * D('.99')
    assert D(result['net_return']) < 0
