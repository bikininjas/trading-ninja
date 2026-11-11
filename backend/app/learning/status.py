"""Présentation factuelle du dernier passage du worker, sans faux voyant vert."""
from datetime import datetime, timedelta, timezone
from dataclasses import asdict
from app.learning.worker import POLICY


def learning_view(data, *, now=None):
    now = now or datetime.now(timezone.utc)
    run = data.get('run')
    state, valid_until = 'NOT_STARTED', None
    if run:
        if run['status']=='RUNNING':
            valid_until = run['started_at']+timedelta(seconds=180)
        else:
            valid_until = (run['finished_at'] or run['started_at'])+timedelta(hours=2)
        state = run['status'] if now < valid_until else 'STALE'
    return {**data,'state':state,'valid_until':valid_until,'policy':asdict(POLICY)}
