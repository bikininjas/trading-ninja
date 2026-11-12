import asyncpg
import pytest

from app.db.repository import PostgresRepository


class Connection:
    def __init__(self, failure=None):
        self.failure = failure
        self.closed = False

    def is_closed(self):
        return self.closed

    async def fetchval(self, _query):
        if self.failure:
            raise self.failure
        return 1

    async def close(self, timeout=None):
        self.closed = True


@pytest.mark.asyncio
async def test_repository_reconnects_after_idle_connection_drop():
    stale = Connection(asyncpg.ConnectionDoesNotExistError("peer reset"))
    fresh = Connection()
    repository = PostgresRepository(stale, reconnect_factory=lambda: _return(fresh))

    assert await repository.ensure_connected() is True
    assert stale.closed
    assert repository._conn is fresh
    assert await repository.ensure_connected() is False


async def _return(value):
    return value
