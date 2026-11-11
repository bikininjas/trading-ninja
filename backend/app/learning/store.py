"""Lectures bornées, journal et verrou d'apprentissage, distincts du ledger."""
from contextlib import asynccontextmanager
from datetime import timedelta
import asyncio
import json


class LearningStore:
    @asynccontextmanager
    async def learning_lock(self):
        # Connexion dédiée + verrou transactionnel : compatible pooler, aucun
        # verrou financier et aucune transaction du portefeuille pendant le LLM.
        if self._lock_connection_factory is None:
            raise RuntimeError('connexion dédiée requise pour l’apprentissage')
        conn = await self._lock_connection_factory()
        acquired = False
        previous = getattr(self,'_learning_owner',None)
        try:
            async with conn.transaction():
                acquired = await conn.fetchval('SELECT pg_try_advisory_xact_lock(1414416962,1)')
                if acquired:
                    self._learning_owner = (asyncio.current_task(),conn)
                yield bool(acquired)
        finally:
            if acquired:
                self._learning_owner = previous
            await conn.close()

    async def assert_learning_owner(self):
        owner = getattr(self,'_learning_owner',None)
        if owner is None or owner[0] is not asyncio.current_task():
            raise RuntimeError('publication sans verrou de mémoire')
        await owner[1].fetchval('SELECT 1')

    async def training_shadows(self, limit=100):
        rows = await self._conn.fetch(
            "SELECT * FROM shadow_trades WHERE mode='PAPER' AND plan_status='OPEN' "
            "AND simulation_context->>'version'='forward-v1' "
            "ORDER BY COALESCE(evaluated_at,created_at),created_at,id LIMIT $1", limit)
        out = []
        for row in rows:
            row = dict(row)
            if isinstance(row['simulation_context'], str):
                row['simulation_context'] = json.loads(row['simulation_context'])
            out.append(row)
        return out

    async def update_training_shadow(self, row, outcome):
        context = dict(row['simulation_context'])
        if outcome is None:
            await self._conn.execute(
                "UPDATE shadow_trades SET evaluated_at=CLOCK_TIMESTAMP() "
                "WHERE mode='PAPER' AND id=$1 AND created_at=$2 AND plan_status='OPEN'",
                row['id'], row['created_at'])
            return
        context['eligible'] = outcome.eligible
        context['exit_at'] = outcome.exit_at.isoformat()
        await self._conn.execute(
            "UPDATE shadow_trades SET plan_status=$3,exit_price=$4,pnl_quote=$5,"
            "outcome_reason=$6,evaluated_at=CLOCK_TIMESTAMP(),simulation_context=$7::jsonb "
            "WHERE mode='PAPER' AND id=$1 AND created_at=$2 AND plan_status='OPEN'",
            row['id'], row['created_at'], outcome.status, outcome.exit_price,
            outcome.pnl_quote, outcome.reason, json.dumps(context))

    async def training_samples(self, limit=100):
        positions = await self.closed_positions_for_lessons('PAPER', limit)
        shadows = await self._conn.fetch(
            "SELECT * FROM shadow_trades WHERE mode='PAPER' AND pnl_quote IS NOT NULL "
            "AND plan_status IN ('STOP_LOSS','TAKE_PROFIT','END_OF_DATA') "
            "AND simulation_context->>'version'='forward-v1' "
            "AND simulation_context->>'eligible'='true' ORDER BY evaluated_at DESC,id LIMIT $1", limit)
        return positions, [dict(row) for row in shadows]

    async def latest_training_lesson(self):
        row = await self._conn.fetchrow(
            "SELECT id,version,created_at,source_trade_ids,model_name,review_sha256 FROM lessons "
            "WHERE mode='PAPER' ORDER BY version DESC LIMIT 1")
        return dict(row) if row else None

    async def publish_training_review(self, run_id, review, settings, *, expected_version, report, now):
        async with self._conn.transaction():
            run = await self._conn.fetchval(
                "SELECT status FROM learning_runs WHERE id=$1 AND mode='PAPER' FOR UPDATE",run_id)
            if run != 'RUNNING':
                raise RuntimeError('passage interrompu ou remplacé')
            lesson_id, _ = await self.insert_lessons(
                'PAPER',lessons=review.lessons,source_trade_ids=review.source_ids,
                source_count=review.source_count,model_name=settings.llm.model,
                prompt_version='paper-memory-v1',valid_until=now+timedelta(days=30),
                review_sha256=review.sha256,expected_version=expected_version)
            await self.finish_learning_run(run_id,status='LEARNED',reason='NEW_LESSONS',
                                           lesson_id=lesson_id,**report)

    async def start_learning_run(self, model_name, policy):
        # Un ancien RUNNING sans verrou propriétaire était interrompu.
        async with self._conn.transaction():
            await self._conn.execute(
                "UPDATE learning_runs SET status='FAILED',reason='INTERRUPTED',finished_at=CLOCK_TIMESTAMP() "
                "WHERE mode='PAPER' AND status='RUNNING'")
            return await self._conn.fetchval(
                "INSERT INTO learning_runs(status,model_name,policy) VALUES('RUNNING',$1,$2::jsonb) RETURNING id",
                model_name, json.dumps(policy))

    async def finish_learning_run(self, run_id, *, status, reason, evaluated_count=0,
                                  source_count=0, new_source_count=0, lesson_id=None):
        await self._conn.execute(
            "UPDATE learning_runs SET finished_at=CLOCK_TIMESTAMP(),status=$2,reason=$3,"
            "evaluated_count=$4,source_count=$5,new_source_count=$6,lesson_id=$7 "
            "WHERE id=$1 AND mode='PAPER' AND status='RUNNING'",
            run_id,status,reason,evaluated_count,source_count,new_source_count,lesson_id)

    async def learning_status(self):
        run = await self._conn.fetchrow("SELECT * FROM learning_runs WHERE mode='PAPER' ORDER BY started_at DESC LIMIT 1")
        counts = await self._conn.fetchrow(
            "SELECT COUNT(*) AS total,COUNT(*) FILTER(WHERE plan_status='OPEN') AS pending,"
            "COUNT(*) FILTER(WHERE simulation_context->>'eligible'='true') AS eligible,"
            "COUNT(*) FILTER(WHERE simulation_context->>'eligible'='false') AS excluded,"
            "COUNT(*) FILTER(WHERE plan_status='NO_PLAN') AS no_plan "
            "FROM shadow_trades WHERE mode='PAPER' AND simulation_context->>'version'='forward-v1'")
        history = await self._conn.fetch(
            "SELECT id,version,created_at,lessons,source_count,active,valid_until,model_name "
            "FROM lessons WHERE mode='PAPER' ORDER BY version DESC LIMIT 5")
        return {'run':dict(run) if run else None, 'simulations':dict(counts),
                'history':[dict(row) for row in history]}
