"""Une intention persistée avant envoi ; aucune répétition après un doute."""

from app.core.coordination import assert_financial_guard


class ExecutionJournal:
    def __init__(self, repo, mode):
        self.repo, self.mode = repo, mode

    async def prepare(self, symbol, request):
        return await self.repo.prepare_request(self.mode, symbol, request)

    async def update(self, client_id, state, **kwargs):
        await self.repo.update_request(client_id, state, **kwargs)

    async def pending(self):
        return await self.repo.pending_requests(self.mode)

    async def assert_owned(self):
        await assert_financial_guard(self.repo, self.mode)
