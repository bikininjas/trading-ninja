"""Reproductions adverses en mémoire, aucun transport exchange autorisé."""

import os

os.environ = {}
import sys, socket, asyncio, json, uuid
from decimal import Decimal as D
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, Mock
from types import SimpleNamespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))
from tests.test_insights import config, FeatureLedger, historical_data, NOW
from app.insights.experiments import HistoricalExchange, ExperimentSignal
from app.insights.ranking import rank_candidates
from app.execution.paper import PaperBroker
from app.execution.live import LiveBroker, LiveExecutionError
from app.runtime.orchestrator import Orchestrator
from app.db.repository import PositionRow


# This script must stay unable to reach a real exchange, even with a shell key.
def forbidden(*a, **kw):
    raise AssertionError("network disabled for audit reproductions")


socket.socket.connect = forbidden
socket.socket.connect_ex = forbidden
RESULTS = []


def output(case, **data):
    RESULTS.append({"case": case, **data})


def market_context():
    data = historical_data()
    now = datetime.fromtimestamp(
        (data["BTC/EUR"]["candles"]["4h"][259][0] + 14400000) / 1000, timezone.utc
    )
    return data, now


async def live_balance_revalidation():
    from app.runtime import orchestrator as module

    data, now = market_context()
    ledger = FeatureLedger(now=now)
    evaluated = []
    original = module.evaluate

    def capture(intent, snapshot, account, constraints, risk):
        result = original(intent, snapshot, account, constraints, risk)
        if intent.confidence < 1.1:
            evaluated.append(
                {
                    "equity": str(account.equity_quote),
                    "free": str(account.free_quote),
                    "approved": result.approved,
                    "amount": str(result.amount),
                    "risk": str(result.effective_risk_quote),
                }
            )
        return result

    class Broker(PaperBroker):
        async def free_quote(self):
            return D("600")

    runtime = Orchestrator(
        config(),
        ledger,
        HistoricalExchange(data, now),
        ExperimentSignal({"kind": "mechanical"}),
        Broker(D(".008"), 5),
        now=lambda: now,
    )
    runtime._mode = (
        "LIVE"  # Only an in-memory state switch, never live config/credentials.
    )
    with patch.object(module, "evaluate", capture):
        await runtime.run_once()
    approved = [e for e in evaluated if e["approved"]]
    assert approved[0]["equity"] == "600" and approved[-1]["equity"] == "1000"
    assert D(approved[-1]["amount"]) > D(approved[0]["amount"])
    output("A01_LIVE_BALANCE_REVALIDATION", evaluations=approved, bug_reproduced=True)


async def kill_removes_protection():
    from app.api.main import _default_kill_switch

    ledger = FeatureLedger()
    ledger.position = PositionRow(
        uuid.uuid4(), "BTC/EUR", D(100), D(1), D(95), None, NOW, D(5)
    )

    class Exchange:
        def __init__(self):
            self.canceled = []

        def load_markets(self):
            return {}

        def fetch_open_orders(self):
            return [
                {
                    "id": "protective-stop",
                    "symbol": "BTC/EUR",
                    "type": "stop-loss",
                    "side": "sell",
                }
            ]

        def cancel_order(self, order_id, symbol):
            self.canceled.append(order_id)

    exchange = Exchange()
    settings = config(
        EXCHANGE_API_KEY="audit-fake", EXCHANGE_SECRET="audit-fake-secret"
    )
    with patch("app.exchange.kraken.build_exchange", return_value=exchange):
        result = await _default_kill_switch(ledger, settings)
    assert (
        exchange.canceled == ["protective-stop"]
        and ledger.position.amount == 1
        and ledger.current == "EMERGENCY_HALT"
    )
    output(
        "A02_KILL_REMOVES_PROTECTION",
        configured_mode=settings.mode,
        exchange_mutation="cancel_order",
        result=result,
        held_amount=str(ledger.position.amount),
        bug_reproduced=True,
    )


async def partial_exit_uncovered():
    class Exchange:
        def __init__(self):
            self.base = D(1)
            self.stop = True
            self.attempts = []
            self.canceled = []

        def amount_to_precision(self, symbol, amount):
            return str(amount)

        def fetch_open_orders(self, symbol):
            return [{"id": "old-stop", "symbol": symbol}] if self.stop else []

        def cancel_order(self, oid, symbol):
            self.canceled.append(oid)
            if oid == "old-stop":
                self.stop = False

        def create_order(self, symbol, type, side, amount, price=None, params=None):
            self.attempts.append({"type": type, "amount": str(amount)})
            if type == "market":
                self.base = D(".6")
                return {
                    "id": "partial-sale",
                    "status": "open",
                    "filled": ".4",
                    "amount": "1",
                    "remaining": ".6",
                    "average": "100",
                }
            if D(amount) > self.base:
                raise RuntimeError("insufficient base for stop")
            self.stop = True
            return {"id": "new-stop"}

    exchange = Exchange()
    broker = LiveBroker(
        exchange, quote_currency="EUR", taker_fee=D(".008"), timeout_seconds=0.01
    )
    with patch(
        "app.execution.live.time", SimpleNamespace(monotonic=Mock(side_effect=[0, 1]))
    ):
        try:
            await broker.exit(D(1), D(100), symbol="BTC/EUR", stop_price=D(95))
        except LiveExecutionError as exc:
            error = str(exc)
        else:
            raise AssertionError("expected unknown partial result")
    assert (
        exchange.base == D(".6")
        and not exchange.stop
        and exchange.attempts[-1]["amount"] == "1"
    )
    output(
        "A03_PARTIAL_EXIT_UNCOVERED",
        remaining=str(exchange.base),
        attempts=exchange.attempts,
        protected=exchange.stop,
        error=error,
        bug_reproduced=True,
    )


async def experiment_halt_semantics():
    data, now = market_context()

    async def check(correct):
        class Ledger(FeatureLedger):
            async def halt_details(self, *a):
                return (
                    "LLM HALT sur BTC/EUR"
                    if correct
                    else await super().halt_details(*a)
                )

        ledger = Ledger(now=now)
        ledger.control = {"enabled": False, "revision": 1}
        await ledger.force_halt(
            "PAPER", "LLM HALT sur BTC/EUR", auto_resume_at=now - timedelta(seconds=1)
        )
        report = await Orchestrator(
            config(),
            ledger,
            HistoricalExchange(data, now),
            ExperimentSignal({"kind": "mechanical"}),
            PaperBroker(D(".008"), 5),
            now=lambda: now,
            insights=False,
        ).run_once()
        return ledger.current, report.actions

    bad, actions = await check(False)
    expected, expected_actions = await check(True)
    assert bad == "EMERGENCY_HALT" and expected == "IDLE"
    output(
        "A04_EXPERIMENT_HALT",
        memory_state=bad,
        reason_preserved_state=expected,
        actions=actions,
        bug_reproduced=True,
    )


def stale_ranking_cost():
    row = {
        "symbol": "BTC/EUR",
        "observed_at": NOW,
        "approved": True,
        "structure_atr": D(1),
        "cost": {
            "available": True,
            "as_of": NOW,
            "age_ms": 0,
            "roundtrip_cost_pct": D(".02"),
        },
    }
    result = rank_candidates([row], now=NOW + timedelta(seconds=60))[0]
    assert result["eligible"] is True
    output(
        "A05_STALE_COST_RANKING",
        source_age_seconds=60,
        book_max_age_seconds=5,
        eligible=result["eligible"],
        bug_reproduced=True,
    )


async def suspension_race():
    """A DB write can suspend after the final permission check but before sending."""
    data, now = market_context()

    class Ledger(FeatureLedger):
        async def append_opportunity(self, cid, seq, mode, stage, at, **kw):
            await super().append_opportunity(cid, seq, mode, stage, at, **kw)
            if stage == "SUBMITTED":
                self.control = {"enabled": False, "revision": 1}

    ledger = Ledger(now=now)
    await Orchestrator(
        config(),
        ledger,
        HistoricalExchange(data, now),
        ExperimentSignal({"kind": "mechanical"}),
        PaperBroker(D(".008"), 5),
        now=lambda: now,
    ).run_once()
    assert ledger.position is not None and not ledger.control["enabled"]
    output(
        "A06_SUSPEND_AFTER_CHECK",
        entries_enabled=ledger.control["enabled"],
        position_open=True,
        bug_reproduced=True,
    )


async def stale_market_is_relabelled_fresh():
    data, then = market_context()
    now = then + timedelta(days=20)
    # Keep the complete historical response frozen at the old observation.
    for item in data.values():
        for timeframe, rows in item["candles"].items():
            from app.market.feed import TIMEFRAME_MS

            item["candles"][timeframe] = [
                r
                for r in rows
                if r[0] + TIMEFRAME_MS[timeframe] <= int(then.timestamp() * 1000)
            ]
    ledger = FeatureLedger(now=now)
    await Orchestrator(
        config(),
        ledger,
        HistoricalExchange(data, now),
        ExperimentSignal({"kind": "mechanical"}),
        PaperBroker(D(".008"), 5),
        now=lambda: now,
    ).run_once()
    assert ledger.position is not None
    output(
        "A08_STALE_MARKET_ACCEPTED",
        data_age_days=20,
        position_open=True,
        bug_reproduced=True,
    )


async def ambiguous_probe_succeeds():
    import importlib.util, io
    from contextlib import redirect_stdout

    spec = importlib.util.spec_from_file_location(
        "audit_probe", str(ROOT / "backend/scripts/live_probe.py")
    )
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)

    class Exchange:
        markets = {"BTC/EUR": {}}

        def load_markets(self):
            return self.markets

        def market(self, symbol):
            return {"limits": {"amount": {"min": ".01"}, "cost": {"min": "1"}}}

        def amount_to_precision(self, symbol, amount):
            return str(amount)

        def fetch_balance(self):
            return {"free": {"EUR": "100"}}

        def fetch_ticker(self, symbol):
            return {"last": "100"}

        def create_order(self, *a, **kw):
            raise TimeoutError("request timed out; exchange result unknown")

        def fetch_open_orders(self, symbol):
            return []

    settings = config(
        EXCHANGE_API_KEY="audit-fake", EXCHANGE_SECRET="audit-fake-secret"
    )
    captured = io.StringIO()
    with (
        patch.object(probe, "load_settings", return_value=settings),
        patch.object(probe, "build_exchange", return_value=Exchange()),
        redirect_stdout(captured),
    ):
        result = probe._run("BTC/EUR")
    assert result == 0 and "aucun ordre n'a été créé" in captured.getvalue()
    output(
        "A07_PROBE_AMBIGUOUS_SUCCESS",
        exit_code=result,
        result_unknown=True,
        false_success=True,
        bug_reproduced=True,
    )


async def main():
    for fn in (
        live_balance_revalidation,
        kill_removes_protection,
        partial_exit_uncovered,
        experiment_halt_semantics,
        suspension_race,
        stale_market_is_relabelled_fresh,
        ambiguous_probe_succeeds,
    ):
        print("running", fn.__name__, file=sys.stderr, flush=True)
        await fn()
    stale_ranking_cost()
    print(json.dumps(RESULTS, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
