from datetime import datetime, timezone
from decimal import Decimal as D
import json

import pytest

from app.market.dataset import build_manifest, load_dataset, coverage
from app.insights.experiments import HistoricalExchange


def fixture_dataset(path):
    for tf, step in [('1h', 3_600_000), ('4h', 14_400_000)]:
        item = {'symbol': 'BTC/EUR', 'timeframe': tf,
                'candles': [[i * step, '100', '101', '99', '100', '0.3'] for i in range(3)]}
        (path / f'part-BTC-{tf}.json').write_text(json.dumps(item))
    return build_manifest(path, {'BTC/EUR': {'taker_fee': '.008'}}, source='test', consistency='test')


def test_dataset_exact_decimal_and_filtered_coverage(tmp_path):
    manifest = fixture_dataset(tmp_path)
    data, checked = load_dataset(tmp_path, start_ms=3_600_000, end_ms=7_200_000)
    assert checked['dataset_digest'] == manifest['dataset_digest']
    assert data['BTC/EUR']['candles']['1h'] == [[3_600_000, D(100), D(101), D(99), D(100), D('.3')]]


@pytest.mark.parametrize('target', ['manifest.json', 'part-BTC-1h.json'])
def test_modified_manifest_or_part_is_rejected(tmp_path, target):
    fixture_dataset(tmp_path)
    path = tmp_path / target
    path.write_text(path.read_text().replace('0.3', '0.4') if target.startswith('part')
                    else path.read_text().replace('".008"', '".009"'))
    with pytest.raises(ValueError, match='modifi'):
        load_dataset(tmp_path)


def test_incomplete_export_is_rejected(tmp_path):
    fixture_dataset(tmp_path)
    (tmp_path / 'part-BTC-4h.json').unlink()
    with pytest.raises(ValueError, match='incomplet'):
        build_manifest(tmp_path, {'BTC/EUR': {}}, source='test', consistency='test')


def test_gaps_are_measured_without_inventing_candles():
    rows = [[i * 3_600_000, 100, 101, 99, 100, 1] for i in (0, 1, 4)]
    assert coverage(rows, '1h')['missing'] == 2
    with pytest.raises(ValueError, match='dupliqu'):
        coverage([rows[0], rows[0]], '1h')


def test_historical_exchange_excludes_future_and_unfinished_candles():
    rows = [[i * 3_600_000, 100, 101, 99, 100, 1] for i in range(5)]
    data = {'BTC/EUR': {'market': {'amount_step': '.01', 'price_tick': '.01',
            'min_amount': '.01', 'min_notional': '1', 'taker_fee': '.008'}, 'candles': {'1h': rows}}}
    exchange = HistoricalExchange(data, datetime.fromtimestamp(7199.999, timezone.utc))
    assert exchange.fetch_ohlcv('BTC/EUR', '1h') == rows[:1]
    exchange.now = datetime.fromtimestamp(7200, timezone.utc)
    assert exchange.fetch_ohlcv('BTC/EUR', '1h', limit=1) == rows[1:2]
