"""Boucle de mémoire PAPER, indépendante du processus d'exécution.

20 résultats minimum, dont 5 nouveaux après la première version, au plus une
relecture par jour. Ce sont des observations de contexte, pas un entraînement
des poids ni une démonstration d'amélioration de la stratégie.
"""
import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

from app.learning.simulation import evaluate_training_shadows
from app.llm.lessons import review_lessons


@dataclass(frozen=True)
class LearningPolicy:
    min_samples: int = 20
    min_new_samples: int = 5
    cooldown_hours: int = 24
    max_samples_per_kind: int = 30
    version: str = 'paper-memory-v1'


POLICY = LearningPolicy()


async def run_learning_once(repo, exchange, client, settings, *, now=None):
    if settings.execution_mode != 'PAPER':
        raise ValueError('apprentissage automatique réservé à PAPER')
    now = now or datetime.now(timezone.utc)
    async with repo.learning_lock() as acquired:
        if not acquired:
            return {'status':'WAITING','reason':'BUSY'}
        run_id = await repo.start_learning_run(settings.llm.model,asdict(POLICY))
        report = {'evaluated_count':0,'source_count':0,'new_source_count':0}
        async def finish(status, reason):
            await repo.finish_learning_run(run_id,status=status,reason=reason,**report)
            return {'status':status,'reason':reason,**report}
        try:
            async with asyncio.timeout(120):
                report['evaluated_count'] = await evaluate_training_shadows(repo,exchange,now=now)
                positions,shadows = await repo.training_samples(POLICY.max_samples_per_kind)
                source_ids = {str(row['id']) for row in positions+shadows}
                report['source_count'] = len(source_ids)
                last = await repo.latest_training_lesson()
                previous_ids = {str(v) for v in last['source_trade_ids']} if last else set()
                report['new_source_count'] = len(source_ids-previous_ids)
                if len(source_ids) < POLICY.min_samples:
                    return await finish('WAITING','NOT_ENOUGH_RESULTS')
                if last and report['new_source_count'] < POLICY.min_new_samples:
                    return await finish('WAITING','NOT_ENOUGH_NEW_RESULTS')
                if last and now < last['created_at'] + timedelta(hours=POLICY.cooldown_hours):
                    return await finish('WAITING','COOLDOWN')
                review = await review_lessons(repo,client,settings,source_rows=(positions,shadows))
                await repo.assert_learning_owner()
                # CAS : ne pas écraser une mémoire modifiée durant l'inférence.
                await repo.publish_training_review(run_id,review,settings,
                    expected_version=last['version'] if last else 0,report=report,now=now)
                return {'status':'LEARNED','reason':'NEW_LESSONS',**report}
        except asyncio.CancelledError:
            await asyncio.shield(finish('FAILED','INTERRUPTED'))
            raise
        except Exception as exc:
            # Ne pas publier d'URL SQL, de prompt ou de secret dans le journal.
            return await finish('FAILED',type(exc).__name__)
