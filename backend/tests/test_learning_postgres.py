"""Contrats SQL du laboratoire, uniquement PostgreSQL local jetable."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from uuid import uuid4
from dataclasses import asdict
import json

import asyncpg
import pytest
import pytest_asyncio

from app.db.repository import PostgresRepository,StateTransitionError
from app.learning.worker import POLICY,run_learning_once
from app.learning.simulation import TrainingOutcome
from tests.test_learning import Client,SETTINGS

pytestmark=[pytest.mark.postgres_integration,pytest.mark.asyncio]


@pytest_asyncio.fixture
async def repo(postgres_test_url):
    conn=await asyncpg.connect(postgres_test_url)
    tx=conn.transaction();await tx.start()
    async def connection():return await asyncpg.connect(postgres_test_url)
    try:yield PostgresRepository(conn,lock_connection_factory=connection)
    finally:await tx.rollback();await conn.close()


async def sample(repo,*,eligible=True):
    at=datetime.now(timezone.utc)
    context=dict(version='forward-v1',timeframe='4h',observed_at=at.isoformat(),
                 horizon_bars=42,taker_fee='.008',slippage_bps=5,eligible=eligible)
    key=await repo.insert_shadow_trade('PAPER','BTC/EUR',kind='MODEL_HOLD',action='HOLD',
      signal_id=uuid4(),evaluation_id=None,rejection_code='NONE',rejection_details='fixture',
      decision_price=D('100'),amount=D('.05'),notional_quote=D('5'),stop_loss_price=D('95'),
      take_profit_price=D('110'),risk_amount_quote=D('.25'),plan_status='OPEN',simulation_context=context)
    return key


async def test_prospective_outcomes_are_separate_and_only_eligible_results_are_learned(repo):
    key=await sample(repo)
    row=(await repo.training_shadows())[0]
    assert row['id']==key and not await repo.open_shadow_trades('PAPER')
    outcome=TrainingOutcome('TAKE_PROFIT',datetime.now(timezone.utc),D('110'),D('.4'),'TAKE_PROFIT',True)
    await repo.update_training_shadow(row,outcome)
    # Une seconde évaluation ne peut pas réécrire un résultat terminal.
    await repo.update_training_shadow(row,TrainingOutcome('STOP_LOSS',outcome.exit_at,D('95'),D('-.4'),'STOP_LOSS',True))
    positions,shadows=await repo.training_samples()
    assert not positions and len(shadows)==1 and shadows[0]['pnl_quote']==D('.4')
    state=await repo.learning_status()
    assert state['simulations']['eligible']==1 and state['simulations']['pending']==0


async def test_full_worker_persists_a_version_and_audit_in_the_same_transaction(repo):
    for _ in range(20):
        await sample(repo)
    for row in await repo.training_shadows():
        await repo.update_training_shadow(row,TrainingOutcome('STOP_LOSS',datetime.now(timezone.utc),D('95'),D('-.3'),'STOP_LOSS',True))
    client=Client()
    result=await run_learning_once(repo,None,client,SETTINGS)
    assert result['status']=='LEARNED' and client.calls==1
    assert (await repo.active_lessons('PAPER'))==client.lessons
    assert not await repo.active_lessons('LIVE')
    run=(await repo.learning_status())['run']
    assert run['lesson_id'] and run['status']=='LEARNED' and run['source_count']==20
    assert (await run_learning_once(repo,None,client,SETTINGS))['reason']=='NOT_ENOUGH_NEW_RESULTS'
    assert client.calls==1


async def test_compare_and_swap_preserves_a_concurrent_manual_memory(repo):
    await repo.insert_lessons('PAPER',lessons=['Observation manuelle suffisamment longue.'],source_trade_ids=[],
       source_count=20,model_name='fixture',prompt_version='fixture',valid_until=None,review_sha256='a'*64)
    with pytest.raises(StateTransitionError,match='modifiée'):
        await repo.insert_lessons('PAPER',lessons=['Observation automatique suffisamment longue.'],source_trade_ids=[],
          source_count=20,model_name='fixture',prompt_version='fixture',valid_until=None,review_sha256='b'*64,expected_version=0)
    assert await repo.active_lessons('PAPER')==['Observation manuelle suffisamment longue.']


async def test_worker_lock_excludes_another_worker_without_taking_the_financial_lock(repo,postgres_test_url):
    async with repo.learning_lock() as first:
        assert first
        async with repo.learning_lock() as second:assert not second
        async with repo.financial_lock('PAPER'):pass
    async with repo.learning_lock() as released:assert released


async def test_learner_role_has_no_order_rights_and_cannot_see_or_write_live(repo):
    await repo._conn.execute('SET LOCAL ROLE tn_paper_learner')
    key=await repo.start_learning_run('fixture',asdict(POLICY))
    await repo.finish_learning_run(key,status='WAITING',reason='NOT_ENOUGH_RESULTS')
    assert (await repo.learning_status())['run']['status']=='WAITING'
    assert not await repo._conn.fetchval("SELECT count(*) FROM positions WHERE mode='LIVE'")
    for sql in ("INSERT INTO positions(mode) VALUES('PAPER')", "SELECT * FROM orders",
                "UPDATE bot_state SET state='IDLE' WHERE mode='PAPER'",
                "INSERT INTO learning_runs(mode,status,model_name,policy) VALUES('LIVE','RUNNING','x','{}')",
                "INSERT INTO lessons(mode,version,lessons,source_count,model_name,prompt_version) VALUES('LIVE',1,ARRAY['x'],20,'x','x')"):
        with pytest.raises((asyncpg.InsufficientPrivilegeError,asyncpg.CheckViolationError)):
            async with repo._conn.transaction():await repo._conn.execute(sql)


async def test_public_roles_cannot_read_learning_and_api_cannot_mutate_runs(repo):
    for role in ['anon','authenticated']:
        assert not await repo._conn.fetchval("SELECT has_table_privilege($1,'learning_runs','SELECT')",role)
    await repo._conn.execute('SET LOCAL ROLE tn_paper_api')
    await repo.learning_status()
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with repo._conn.transaction():
            await repo.start_learning_run('fixture',asdict(POLICY))


async def test_interrupted_run_cannot_publish_even_with_an_unchanged_lesson_version(repo):
    from app.llm.lessons import LessonsReview
    run=await repo.start_learning_run('fixture',asdict(POLICY))
    await repo.start_learning_run('fixture',asdict(POLICY))
    review=LessonsReview(['Observation suffisamment longue.'],[],20,'fixture','c'*64)
    with pytest.raises(RuntimeError,match='interrompu'):
        await repo.publish_training_review(run,review,SETTINGS,expected_version=0,
            report=dict(evaluated_count=0,source_count=20,new_source_count=20),now=datetime.now(timezone.utc))
    assert not await repo.active_lessons('PAPER')
