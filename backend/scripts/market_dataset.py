#!/usr/bin/env python3
"""Export public uniquement, snapshot SQL cohérent ; audit local reproductible."""

import argparse
import asyncio
from datetime import datetime
import json
from pathlib import Path
import sys

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.settings import load_settings
from app.exchange.kraken import build_exchange, market_info
from app.market.dataset import build_manifest, load_dataset, coverage


async def export(args):
    config = load_settings()
    exchange = build_exchange(api_key='', secret='')
    exchange.load_markets()
    markets = {}
    for symbol in args.symbols.split(','):
        info = market_info(exchange, symbol, taker_fee=config.exchange.taker_fee)
        markets[symbol] = {key: str(getattr(info.constraints, key)) for key in ('amount_step', 'price_tick', 'min_amount', 'min_notional')}
        markets[symbol]['taker_fee'] = str(config.exchange.taker_fee)
    args.directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    conn = await asyncpg.connect(**config.database().asyncpg_kwargs())
    try:
        async with conn.transaction(isolation='repeatable_read', readonly=True):
            for symbol in markets:
                for tf in ('1h', '4h'):
                    rows = await conn.fetch(
                        "SELECT (extract(epoch FROM open_time)*1000)::bigint AS ms,open_price,high_price,low_price,close_price,volume "
                        "FROM market_candles_history WHERE symbol=$1 AND timeframe=$2 AND open_time>=$3 ORDER BY open_time",
                        symbol, tf, datetime.fromisoformat(args.start))
                    for page, offset in enumerate(range(0, len(rows), 5000)):
                        payload = {'symbol': symbol, 'timeframe': tf,
                                   'candles': [[r['ms'], *(str(r[k]) for k in ('open_price','high_price','low_price','close_price','volume'))] for r in rows[offset:offset+5000]]}
                        (args.directory / f"part-{symbol.replace('/','-')}-{tf}-{page:04d}.json").write_text(json.dumps(payload))
        return build_manifest(args.directory, markets, source='PostgreSQL public market_candles_history', consistency='repeatable-read')
    finally:
        await conn.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('export', 'audit'))
    parser.add_argument('--directory', required=True, type=Path)
    parser.add_argument('--start', default='2022-09-01T00:00:00+00:00')
    parser.add_argument('--symbols', default='BTC/EUR,ETH/EUR,SOL/EUR')
    args = parser.parse_args(argv)
    try:
        if args.action == 'export':
            manifest = asyncio.run(export(args))
            print(json.dumps({'dataset_digest': manifest['dataset_digest'], 'coverage': manifest['coverage']}))
        else:
            data, manifest = load_dataset(args.directory)
            print(json.dumps({'dataset_digest': manifest['dataset_digest'], 'coverage': [
                {'symbol': s, 'timeframe': tf, **coverage(rows, tf)} for s, item in data.items() for tf, rows in item['candles'].items()]}))
        return 0
    except Exception as exc:
        print(json.dumps({'status': 'FAILED', 'error_type': type(exc).__name__}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
