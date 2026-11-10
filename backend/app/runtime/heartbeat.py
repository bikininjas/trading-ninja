"""Preuve de vie du processus moteur, indépendante de sa cadence de décision."""

import asyncio
import logging

import asyncpg

INTERVAL_SECONDS = 10
MAX_AGE_SECONDS = 30
IO_TIMEOUT_SECONDS = 5


async def run_with_heartbeat(work, config, mode, *, interval=INTERVAL_SECONDS,
                             connect=asyncpg.connect):
    """Une erreur de heartbeat arrête le travail ; aucun ordre n'est créé ici.

    La connexion est distincte du ledger : jamais d'usage asyncpg concurrent sur
    sa connexion, ni de transaction financière conservée pendant la veille H4/D1.
    Le même event loop doit avancer pour renouveler cette preuve de vie.
    """
    kwargs = config.asyncpg_kwargs()
    kwargs.update(timeout=IO_TIMEOUT_SECONDS, command_timeout=IO_TIMEOUT_SECONDS)
    conn = await connect(**kwargs)
    latest = None
    pulse = worker = None

    async def renew():
        nonlocal latest
        latest = await conn.fetchval(
            "UPDATE bot_state SET last_heartbeat_at=CLOCK_TIMESTAMP() "
            "WHERE mode=$1::execution_mode RETURNING last_heartbeat_at", mode,
        )
        if latest is None:
            raise RuntimeError("heartbeat : mode absent en base")

    async def beat():
        while True:
            await asyncio.sleep(interval)
            await renew()

    try:
        # Pas de travail sans première preuve durable ; l'horloge vient du SQL.
        await renew()
        pulse = asyncio.create_task(beat(), name="engine-heartbeat")
        worker = asyncio.create_task(work(), name="engine-work")
        done, _ = await asyncio.wait((pulse, worker), return_when=asyncio.FIRST_COMPLETED)
        if pulse in done:
            await pulse
            raise RuntimeError("heartbeat interrompu")
        return await worker
    finally:
        tasks = [task for task in (pulse, worker) if task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            if latest is not None:
                # Ne pas effacer la preuve d'un autre processus ayant repris.
                await conn.execute(
                    "UPDATE bot_state SET last_heartbeat_at=NULL "
                    "WHERE mode=$1::execution_mode AND last_heartbeat_at=$2", mode, latest,
                )
        except Exception as exc:
            logging.getLogger(__name__).warning("heartbeat cleanup: %s", type(exc).__name__)
        finally:
            await conn.close(timeout=2)
