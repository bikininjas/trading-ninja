#!/usr/bin/env python3
"""Comparaison exploratoire hors ligne, sans SQL, clé, modèle ou ordre réel."""

import argparse
import asyncio
from copy import deepcopy
from datetime import datetime
from decimal import Decimal as D, ROUND_DOWN
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.settings import load_settings
from app.insights.experiments import HistoricalExchange, run_experiment
from app.market.dataset import load_dataset, coverage


def buy_and_hold(data, start, finish, capital, fee, bps):
    """Pondération égale, lots arrondis vers le bas, reliquats en cash."""
    exchange = HistoricalExchange(data, start)
    slip = D(bps) / 10000
    equity = D(0)
    legs = []
    allocation = capital / len(data)
    for index, (symbol, item) in enumerate(sorted(data.items())):
        leg_allocation = allocation if index < len(data) - 1 else capital - allocation * (len(data) - 1)
        exchange.now = start
        first = D(str(exchange.fetch_ticker(symbol)['last']))
        market = item['market']
        step = D(market['amount_step'])
        quantity = (leg_allocation / (first * (1 + slip) * (1 + fee)) / step).to_integral_value(rounding=ROUND_DOWN) * step
        eligible = quantity >= D(market['min_amount']) and quantity * first >= D(market['min_notional'])
        if not eligible:
            quantity = D(0)
        debit = quantity * first * (1 + slip) * (1 + fee)
        exchange.now = finish
        last = D(str(exchange.fetch_ticker(symbol)['last']))
        final = leg_allocation - debit + quantity * last * (1 - slip) * (1 - fee)
        equity += final
        legs.append({'symbol': symbol, 'quantity': str(quantity), 'financed': eligible,
                     'initial_price': str(first), 'final_price': str(last),
                     'unfinanced_market_return': str(last / first - 1),
                     'equity_end': str(final)})
    return {'equity_start': str(capital), 'equity_end': str(equity),
            'net_return': str(equity / capital - 1), 'legs': legs,
            'current_exchange_minima_used_retrospectively': True,
            'final_liquidation_cost_included': True}


async def qualify(directory, plan):
    start, end, warmup = (datetime.fromisoformat(plan[k]) for k in ('start_at', 'end_at', 'warmup_at'))
    dataset, manifest = load_dataset(directory, start_ms=int(warmup.timestamp() * 1000),
                                      end_ms=int(end.timestamp() * 1000))
    if manifest['dataset_digest'] != plan['dataset_digest']:
        raise ValueError('jeu différent du protocole gelé')
    capital = D(plan['capital'])
    results = []
    for costs in plan['costs']:
        data = deepcopy(dataset)
        for item in data.values():
            item['market']['taker_fee'] = costs['fee']
        for policy in plan['policies']:
            settings = load_settings(use_environment=False, overrides={
                'TRADING_MODE': 'paper', 'EXCHANGE_ID': 'kraken', 'EXCHANGE_SANDBOX_MODE': 'true',
                'RISK_PROFILE': 'prudent', 'TRADING_SYMBOL': 'BTC/EUR',
                'TRADING_SYMBOLS': ','.join(sorted(data)), 'QUOTE_CURRENCY': 'EUR',
                'DECISION_TIMEFRAME': '4h', 'ENTRY_TIMEFRAME': '1h',
                'RISK_PER_TRADE_PCT': policy['risk'], 'MAX_NOTIONAL_PCT': policy['exposure'],
                'MAX_OPEN_POSITIONS': '1', 'COST_AWARE': 'true', 'CAPITAL_STEPS_ENABLED': 'false',
                'PAPER_START_EQUITY': str(capital), 'TAKER_FEE_PCT': costs['fee'],
                'PAPER_SLIPPAGE_BPS': str(costs['slippage_bps']),
            })
            spec = {'schema_version': 1, 'variants': [{'name': policy['name'], 'kind': 'mechanical'}],
                    'data': data, 'start_at': start.isoformat(), 'end_at': end.isoformat(),
                    'equity': str(capital), 'hypothesis': plan['hypothesis']}
            result = await run_experiment(spec, settings)
            variant = result['variants'][0]
            finish = variant['curve'][-1][0]
            first = variant['curve'][0][0]
            detail = {k: v for k, v in variant.items() if k not in ('curve', 'closed')}
            detail.update({'costs': costs, 'policy': policy,
                           'first_decision_at': first.isoformat(), 'last_decision_at': finish.isoformat(),
                           'cash_baseline': str(capital),
                           'buy_and_hold': buy_and_hold(data, first, finish, capital, D(costs['fee']), costs['slippage_bps']),
                           'manifest_digest': result['manifest_digest'],
                           'code_fingerprint': result['code_fingerprint'],
                           'base_settings': result['base_settings']})
            results.append(detail)
            print(f"{costs['name']} / {policy['name']}: {variant['trades']} trades, {variant['equity_end']} EUR, {variant['rejections']}", file=sys.stderr)
    return {'schema_version': 1, 'status': 'EXPLORATORY_ONLY_NO_GO',
            'llm_inference_performed': False, 'llm_performance_validated': False,
            'out_of_calibration_validated': False, 'real_orders': 0,
            'dataset_digest': manifest['dataset_digest'],
            'plan_sha256': hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest(),
            'full_dataset_coverage': manifest['coverage'],
            'evaluated_data_coverage': [{'symbol': s, 'timeframe': tf, **coverage(rows, tf)}
                for s, item in dataset.items() for tf, rows in item['candles'].items()],
            'results': results,
            'limitations': plan['limitations']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text())
    result = asyncio.run(qualify(args.directory, plan))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, default=str) + '\n')


if __name__ == '__main__':
    main()
