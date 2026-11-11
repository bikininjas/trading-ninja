#!/usr/bin/env python3
"""Évalue les achats fictifs puis mémorise leurs résultats, exclusivement PAPER."""
import argparse
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.core.settings import load_settings
from app.db.repository import PostgresRepository
from app.exchange.kraken import build_exchange
from app.learning.worker import run_learning_once
from app.llm.client import SignalClient


async def run(args):
    while True:
        # Reconnexion à chaque lot : une panne SQL ne tue pas le service à vie.
        repo = None
        try:
            settings = load_settings(env_file=args.config)
            if settings.execution_mode != 'PAPER':
                print(json.dumps({'status':'FAILED','reason':'PAPER_ONLY'}),flush=True)
                return 2
            repo = await PostgresRepository.connect(settings.database())
            exchange = build_exchange(api_key='',secret='')
            result = await run_learning_once(repo,exchange,SignalClient(settings.llm),settings)
        except Exception as exc:
            result = {'status':'FAILED','reason':type(exc).__name__}
        finally:
            if repo is not None:
                await repo.close()
        print(json.dumps(result),flush=True)
        if not args.serve:
            return 1 if result['status']=='FAILED' else 0
        await asyncio.sleep(args.interval)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',help='fichier de configuration privé')
    parser.add_argument('--serve',action='store_true',help='lot périodique, sans bloquer le moteur')
    parser.add_argument('--interval',type=int,default=3600,help='secondes entre les lots (minimum 60)')
    args = parser.parse_args(argv)
    if args.interval < 60:
        parser.error('intervalle minimum : 60 secondes')
    return asyncio.run(run(args))


if __name__=='__main__':
    raise SystemExit(main())
