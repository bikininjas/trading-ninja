"""Contre-exemples pré live convertis en contrats à respecter."""

from decimal import Decimal as D
import uuid
from unittest.mock import patch
import pytest
from tests.test_insights import config, FeatureLedger, NOW
from app.db.repository import PositionRow
from app.api.main import _default_kill_switch


@pytest.mark.asyncio
async def test_paper_kill_switch_never_contacts_exchange_and_keeps_stop_owner_running():
    ledger = FeatureLedger()
    ledger.current = "IN_POSITION"
    ledger.position = PositionRow(
        uuid.uuid4(), "BTC/EUR", D(100), D(1), D(95), None, NOW, D(5)
    )
    with patch(
        "app.exchange.kraken.build_exchange",
        side_effect=AssertionError("forbidden exchange call"),
    ):
        result = await _default_kill_switch(ledger, config())
    assert result["canceled_orders"] == [] and not ledger.control["enabled"]
    assert ledger.position.amount == 1 and ledger.current == "IN_POSITION"


def test_factory_cannot_promote_paper_configuration_to_live():
    from app.execution.factory import make_broker

    with pytest.raises(RuntimeError, match="non armée"):
        make_broker(config(), object(), mode="live")


@pytest.mark.asyncio
async def test_live_revalidation_retains_observed_exchange_balance():
    from tests.test_insights import historical_data
    from app.insights.experiments import HistoricalExchange, ExperimentSignal
    from app.execution.paper import PaperBroker
    from app.runtime.orchestrator import Orchestrator
    from datetime import datetime, timezone

    data = historical_data()
    now = datetime.fromtimestamp(
        (data["BTC/EUR"]["candles"]["4h"][259][0] + 14400000) / 1000, timezone.utc
    )

    class Ledger(FeatureLedger):
        async def insert_evaluation(self, *a, **kw):
            assert kw["account"].equity == D(990)
            return await super().insert_evaluation(*a, **kw)

    class Broker(PaperBroker):
        async def free_quote(self):
            return D(990)

    ledger = Ledger(now=now)
    runtime = Orchestrator(
        config(),
        ledger,
        HistoricalExchange(data, now),
        ExperimentSignal({"kind": "mechanical"}),
        Broker(D(".008"), 5),
        now=lambda: now,
    )
    runtime._mode = "LIVE"
    await runtime.run_once()
    assert ledger.position is not None


@pytest.mark.asyncio
async def test_old_closed_candles_cannot_open_a_position():
    from tests.test_insights import historical_data
    from app.insights.experiments import HistoricalExchange, ExperimentSignal
    from app.execution.paper import PaperBroker
    from app.runtime.orchestrator import Orchestrator
    from datetime import datetime, timezone, timedelta

    data = historical_data()
    now = datetime.fromtimestamp(
        (data["BTC/EUR"]["candles"]["4h"][-1][0] + 14400000) / 1000, timezone.utc
    ) + timedelta(days=20)
    ledger = FeatureLedger(now=now)
    report = await Orchestrator(
        config(),
        ledger,
        HistoricalExchange(data, now),
        ExperimentSignal({"kind": "mechanical"}),
        PaperBroker(D(".008"), 5),
        now=lambda: now,
    ).run_once()
    assert ledger.position is None and report.errors


@pytest.mark.asyncio
async def test_simulation_preserves_llm_halt_reason_for_auto_resume():
    from app.insights.experiments import ExperimentSignal
    from app.execution.paper import PaperBroker
    from app.runtime.orchestrator import Orchestrator
    from datetime import timedelta

    ledger = FeatureLedger()
    ledger.control = {"enabled": False, "revision": 1}
    await ledger.force_halt(
        "PAPER", "LLM HALT BTC/EUR", auto_resume_at=NOW - timedelta(seconds=1)
    )
    await Orchestrator(
        config(),
        ledger,
        object(),
        ExperimentSignal({"kind": "mechanical"}),
        PaperBroker(D(".008"), 5),
        now=lambda: NOW,
        insights=False,
    ).run_once()
    assert ledger.current == "IDLE"


def test_ranking_checks_the_book_age_again_at_selection():
    from datetime import timedelta
    from app.insights.ranking import rank_candidates

    row = {
        "symbol": "BTC/EUR",
        "observed_at": NOW,
        "approved": True,
        "cost": {"available": True, "as_of": NOW, "roundtrip_cost_pct": D(".02")},
    }
    assert rank_candidates([row], now=NOW)[0]["eligible"]
    assert not rank_candidates([row], now=NOW + timedelta(seconds=6))[0]["eligible"]


@pytest.mark.asyncio
async def test_health_reports_unknown_quota_and_normalizes_ollama_url():
    from app.api.main import _default_system_health

    class Repo:
        async def storage_state(self):
            raise RuntimeError("unavailable")

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    seen = []

    def request(url, **kw):
        seen.append(url)
        return Response()

    with (
        patch(
            "app.exchange.kraken.build_exchange",
            side_effect=RuntimeError("unavailable"),
        ),
        patch("urllib.request.urlopen", side_effect=request),
    ):
        health = await _default_system_health(
            Repo(), config(LLM_BASE_URL="http://localhost:11434/v1")
        )
    assert (
        health["supabase"]["status"] == "UNKNOWN"
        and health["supabase"]["free_space_bytes"] is None
    )
    assert health["supabase"]["quota_alert_triggered"] is True
    assert (
        seen == ["http://localhost:11434/api/tags"]
        and health["ollama"]["status"] == "UP"
    )


def test_quote_without_source_time_is_rejected():
    from app.market.feed import last_price,FeedError
    class Exchange:
        def fetch_ticker(self,symbol):return {'last':'100'}
    with pytest.raises(FeedError,match='heure source'):last_price(Exchange(),'BTC/EUR')


def test_recent_public_trade_can_supply_missing_ticker_time():
    from app.market.feed import last_price
    class Exchange:
        def fetch_ticker(self,symbol):return {'last':'999','timestamp':None}
        def fetch_trades(self,symbol,limit=1):return [{'timestamp':10000,'price':'100'}]
        def milliseconds(self):return 11000
    assert last_price(Exchange(),'BTC/EUR')==D(100)


def test_old_public_trade_cannot_refresh_a_quote():
    from app.market.feed import last_price,FeedError
    class Exchange:
        def fetch_ticker(self,symbol):return {'last':'999'}
        def fetch_trades(self,symbol,limit=1):return [{'timestamp':10000,'price':'100'}]
        def milliseconds(self):return 16000
    with pytest.raises(FeedError,match='périmé'):last_price(Exchange(),'BTC/EUR')
