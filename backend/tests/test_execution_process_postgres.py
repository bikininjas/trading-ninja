"""Arrêt brutal réel : verrou interprocessus et reçus SQL, sans exchange."""

import asyncio
from pathlib import Path
import sys

import pytest

from app.core.db import from_env
from app.db.repository import PostgresRepository, StateTransitionError

pytestmark = [pytest.mark.postgres_integration, pytest.mark.asyncio]

CHILD = '''
import asyncio, os, sys
from app.core.db import from_env
from app.db.repository import PostgresRepository

async def main():
    repo = await PostgresRepository.connect(from_env({'DATABASE_URL': sys.argv[1]}))
    await repo.ensure_mode('PAPER')
    await repo.transition('PAPER', 'IDLE', 'EXECUTING', 'test interruption')
    async with repo.cycle_lock('PAPER'):
        rid = await repo.prepare_request('PAPER', 'BTC/EUR',
            {'type': 'market', 'side': 'buy', 'amount': '1'})
        if sys.argv[2] != 'PREPARED':
            await repo.update_request(rid, sys.argv[2], response={'id': 'process-' + rid})
        print(rid, flush=True)
        os.read(0, 1)  # Le parent tue le processus, sans finally ni fermeture propre.

asyncio.run(main())
'''


@pytest.mark.parametrize('boundary', ['PREPARED', 'ACKNOWLEDGED', 'FILLED'])
async def test_process_loss_preserves_receipt_and_releases_financial_lock(postgres_test_url, boundary):
    repo = await PostgresRepository.connect(from_env({'DATABASE_URL': postgres_test_url}))
    child = None
    rid = None
    try:
        await repo.ensure_mode('PAPER')
        assert (await repo.state('PAPER'))[0] == 'IDLE'
        assert not await repo.pending_requests('PAPER')
        child = await asyncio.create_subprocess_exec(
            sys.executable, '-c', CHILD, postgres_test_url, boundary,
            cwd=Path(__file__).resolve().parents[1], env={},
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        rid = (await asyncio.wait_for(child.stdout.readline(), timeout=15)).decode().strip()
        if not rid:
            raise AssertionError((await child.stderr.read()).decode())
        with pytest.raises(StateTransitionError, match='verrou'):
            async with repo.cycle_lock('PAPER'):
                pytest.fail('le processus enfant détient encore le verrou')
        # Une urgence persiste immédiatement, même si le cycle est encore occupé.
        await asyncio.wait_for(repo.force_halt('PAPER', 'test arrêt brutal'), timeout=3)
        assert (await repo.state('PAPER'))[0] == 'EMERGENCY_HALT'
        child.kill()
        await asyncio.wait_for(child.wait(), timeout=5)
        # Nouvelle connexion + vrai nouveau verrou après la mort du propriétaire.
        await repo.close()
        repo = await PostgresRepository.connect(from_env({'DATABASE_URL': postgres_test_url}))
        async with asyncio.timeout(5):
            async with repo.financial_lock('PAPER', wait=True):
                pending = await repo.pending_requests('PAPER')
                assert len(pending) == 1
                assert pending[0]['client_id'] == rid and pending[0]['state'] == boundary
                assert pending[0]['request']['authorized_revision'] >= 0
        with pytest.raises(StateTransitionError, match='non rapprochées'):
            await repo.force_resume('PAPER', 'reprise interdite')
    finally:
        if child and child.returncode is None:
            child.kill()
            await child.wait()
        if rid:
            await repo._conn.execute('DELETE FROM execution_requests WHERE client_id=$1', rid)
        await repo._conn.execute("UPDATE bot_state SET state='IDLE' WHERE mode='PAPER'")
        await repo.close()
