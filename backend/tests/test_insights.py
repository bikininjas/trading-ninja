"""Contrats des dix briefs : calculs purs, frontières de rejeu et runtime réel."""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from itertools import permutations
import uuid

import pytest

from app.core.settings import load_settings
from app.db.repository import PositionRow
from app.execution.paper import PaperBroker
from app.insights.codec import encode, decode, digest
from app.insights.costs import estimate_costs
from app.insights.ranking import rank_candidates
from app.insights.policy import position_policy, apply_policy
from app.insights.diagnostics import trade_diagnostics, opportunity_summary
from app.insights.replay import replay_cycle, Unreplayable
from app.insights.experiments import (
    MemoryLedger,
    HistoricalExchange,
    ExperimentSignal,
    run_experiment,
)
from app.insights.calibration import confidence_report
from app.insights.watch import observe_protection, verify_observer_key
from app.runtime.orchestrator import Orchestrator

NOW = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)


def config(**overrides):
    return load_settings(
        use_environment=False,
        overrides={
            "TRADING_MODE": "paper",
            "EXCHANGE_ID": "kraken",
            "EXCHANGE_SANDBOX_MODE": "true",
            "RISK_PROFILE": "standard",
            "PAPER_START_EQUITY": "1000",
            "ENTRY_RULE_ENABLED": "false",
            "TRADING_SYMBOL": "BTC/EUR",
            "TRADING_SYMBOLS": "BTC/EUR",
            **overrides,
        },
    )


class FeatureLedger(MemoryLedger):
    def __init__(self, equity=D(1000), now=NOW):
        super().__init__(equity, now)
        self.packets = {}
        self.events = []
        self.control = {"enabled": True, "revision": 0}
        self.protection = {"latest": None, "incidents": []}
        self.experiment_rows = []
        self.settings_version = 0

    async def begin_cycle(self, cid, mode, at, manifest):
        self.packets[cid] = {"status": "RUNNING", "manifest": deepcopy(manifest)}

    async def finish_cycle(self, cid, packet):
        self.packets[cid] = deepcopy(packet)

    async def append_opportunity(self, cid, seq, mode, stage, at, **kw):
        self.events.append(
            {"cycle_id": cid, "seq": seq, "stage": stage, "observed_at": at, **kw}
        )

    async def entry_control(self, *a):
        return deepcopy(self.control)

    async def set_entry_permission(self, mode, enabled, revision, **kw):
        from app.db.repository import StateTransitionError

        if revision != self.control["revision"]:
            raise StateTransitionError("révision obsolète")
        if enabled and self.current in ("EMERGENCY_HALT", "LOCKED_CIRCUIT_BREAKER"):
            raise StateTransitionError("urgence")
        self.control = {"enabled": enabled, "revision": revision + 1}
        return self.control

    async def consume_observation_commands(self, *a):
        return 0

    async def settings_state(self, *a):
        return {
            "revision": self.settings_version,
            "applied_revision": 0,
            "applied_at": None,
            "apply_error": None,
        }

    async def acknowledge_settings(self, *a, **kw):
        pass

    async def opportunities(self, *a, **kw):
        return self.events

    async def recent_cycles(self, *a):
        return []

    async def experiments(self, *a):
        return self.experiment_rows

    async def protection_health(self, *a):
        return self.protection

    async def runtime_heartbeat(self, *a):
        return self.now

    async def record_protection(self, mode, observation, issues):
        self.protection = {"latest": observation, "incidents": issues}

    async def cycle_packet(self, mode, cid):
        return self.packets.get(cid)

    async def cycle_events(self, mode, cid):
        return [e for e in self.events if e["cycle_id"] == cid]


def historical_data(count=270):
    from tests.test_backtest import candles, START, H4_MS

    h4 = candles(START, count, H4_MS)
    h1 = []
    for c in h4:
        for h in range(4):
            h1.append([c[0] + h * 3600000, *c[1:]])
    return {
        "BTC/EUR": {
            "market": {
                "amount_step": "0.000001",
                "price_tick": ".01",
                "min_amount": ".000001",
                "min_notional": "1",
                "taker_fee": ".008",
            },
            "candles": {"4h": h4, "1h": h1},
        }
    }


def test_codec_preserves_money_types_and_redacts_credentials():
    settings = config(
        EXCHANGE_API_KEY="never-export-this", EXCHANGE_SECRET="also-private"
    )
    encoded = encode(settings)
    assert "never-export-this" not in str(encoded) and "also-private" not in str(
        encoded
    )
    assert decode(encode({"literal": {"@": "decimal", "value": "1.0"}})) == {
        "literal": {"@": "decimal", "value": "1.0"}
    }
    rebuilt = decode(encoded)
    assert rebuilt.risk.risk_per_trade_pct == settings.risk.risk_per_trade_pct
    assert isinstance(rebuilt.risk.risk_per_trade_pct, D)
    from app.insights.replay import Recorder

    recorder = Recorder(settings)
    error = RuntimeError("transport failed for never-export-this / also-private")
    recorder.call("exchange", "query", (), {}, error=error)
    assert "never-export-this" not in str(recorder.packet(error=error))
    assert "also-private" not in str(recorder.packet(error=error))
    with pytest.raises(ValueError, match="non autorisé"):
        decode({"@": "dataclass", "type": "os.system", "fields": {}})


def test_cost_model_consumes_depth_without_counting_spread_twice():
    book = {
        "timestamp": int(NOW.timestamp() * 1000),
        "bids": [["99", "1"], ["98", "2"]],
        "asks": [["101", "1"], ["102", "2"]],
    }
    result = estimate_costs(book, D(2), D(".01"), now=NOW)
    assert result["entry_vwap"] == D("101.5") and result["exit_vwap_scenario"] == D(
        "98.5"
    )
    assert result["spread_quote"] == D(4) and result["impact_quote"] == D(2)
    assert result["fees_quote"] == D(4) and result["roundtrip_cost_quote"] == D(10)


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"timestamp": None}, "SOURCE_TIME_MISSING"),
        (
            {"timestamp": int((NOW - timedelta(seconds=6)).timestamp() * 1000)},
            "STALE_BOOK",
        ),
        ({"asks": [["101", ".001"]]}, "INSUFFICIENT_DEPTH"),
        ({"bids": [["102", "10"]]}, "INVALID_BOOK"),
    ],
)
def test_costs_never_replace_missing_or_bad_depth_with_zero(change, reason):
    book = {
        "timestamp": int(NOW.timestamp() * 1000),
        "bids": [["99", "10"]],
        "asks": [["101", "10"]],
        **change,
    }
    result = estimate_costs(book, D(1), D(".008"), now=NOW)
    assert result["available"] is False and result["reason"] == reason


def test_ranking_does_not_depend_on_symbol_configuration_order():
    candidates = [
        {
            "symbol": s,
            "observed_at": NOW,
            "approved": True,
            "cost": {"available": True, "roundtrip_cost_pct": cost},
            "structure_atr": D(1),
        }
        for s, cost in [
            ("BTC/EUR", D(".02")),
            ("ETH/EUR", D(".01")),
            ("SOL/EUR", D(".01")),
        ]
    ]
    results = [
        tuple(r["symbol"] for r in rank_candidates(list(p), now=NOW))
        for p in permutations(candidates)
    ]
    assert len(set(results)) == 1 and results[0][0] == "ETH/EUR"
    stale = deepcopy(candidates[0])
    stale["observed_at"] = NOW - timedelta(seconds=200)
    assert rank_candidates([stale], now=NOW)[0]["eligible"] is False


def test_position_policy_keeps_strategy_and_current_safety_limits_separate():
    before = config()
    policy = position_policy(before)
    changed = replace(
        before,
        risk=replace(
            before.risk, swing_breakeven_r=D(8), max_daily_drawdown_pct=D(".08")
        ),
    )
    managed = apply_policy(changed, policy)
    assert managed.risk.swing_breakeven_r == before.risk.swing_breakeven_r
    assert managed.risk.max_daily_drawdown_pct == D(".08")
    tampered = deepcopy(policy)
    tampered["body"]["parameters"]["max_daily_drawdown_pct"] = encode(D(".5"))
    tampered["id"] = digest(tampered["body"])
    with pytest.raises(ValueError, match="sécurité"):
        apply_policy(changed, tampered)


def test_trade_diagnostics_respects_exit_time_and_actual_partial_cashflows():
    pid = uuid.uuid4()
    position = {
        "id": pid,
        "symbol": "BTC/EUR",
        "entry_price": D(100),
        "initial_amount": D(1),
        "opened_at": NOW,
        "closed_at": NOW + timedelta(hours=12),
        "exit_price": D(110),
        "net_pnl_quote": D("3.8"),
        "risk_amount_quote": D(10),
    }
    orders = [
        {
            "side": "BUY",
            "fee_amount": D(1),
            "fee_currency": "EUR",
            "created_at": NOW,
            "filled_amount": D(1),
            "average_fill_price": D(100),
            "slippage_quote": D(0),
        },
        {
            "side": "SELL",
            "fee_amount": D(".1"),
            "fee_currency": "EUR",
            "created_at": NOW + timedelta(hours=4),
            "filled_amount": D(".5"),
            "average_fill_price": D(100),
            "slippage_quote": D(0),
        },
        {
            "side": "SELL",
            "fee_amount": D(".1"),
            "fee_currency": "EUR",
            "created_at": NOW + timedelta(hours=12),
            "filled_amount": D(".5"),
            "average_fill_price": D(110),
            "slippage_quote": D(0),
        },
    ]
    data = {
        "position": position,
        "orders": orders,
        "timeframe": "4h",
        "until": position["closed_at"],
        "marks": [],
        "candles": [
            {"open_time": NOW + timedelta(hours=4), "close_price": D(90)},
            {"open_time": NOW + timedelta(hours=12), "close_price": D(999)},
        ],
    }
    result = trade_diagnostics(data)
    assert result["mae_quote"] == D("-6.1") and result["realized_net_pnl_quote"] == D(
        "3.8"
    )
    assert all(x["price"] != 999 for x in result["series"])
    assert result["fees_quote"] == D("1.2") and result["realized_r"] == D(".38")
    assert result["coverage_status"] == "PARTIAL"
    foreign = deepcopy(data)
    foreign["orders"][0]["fee_currency"] = "BTC"
    unknown = trade_diagnostics(foreign)
    assert unknown["mfe_quote"] is None and unknown["fees_quote"] is None
    assert unknown["realized_net_pnl_quote"] == D("3.8")


@pytest.mark.asyncio
async def test_real_runtime_cycle_replays_offline_and_detects_tampering():
    data = historical_data()
    now = datetime.fromtimestamp(
        (data["BTC/EUR"]["candles"]["4h"][259][0] + 14400000) / 1000, timezone.utc
    )
    ledger = FeatureLedger(now=now)
    exchange = HistoricalExchange(data, now)
    client = ExperimentSignal({"kind": "mechanical"})
    runtime = Orchestrator(
        config(),
        ledger,
        exchange,
        client,
        PaperBroker(D(".008"), 5),
        now=lambda: ledger.now,
        settings_loader=lambda overrides=None: config(),
    )
    report = await runtime.run_once()
    assert not report.errors
    assert report.position_opened is not None
    packet = next(iter(ledger.packets.values()))
    assert packet["status"] == "COMPLETE" and packet["trace"]
    replayed = await replay_cycle(packet)
    assert replayed["status"] == "IDENTICAL"
    altered = deepcopy(packet)
    altered["trace"][0]["method"] = "wrong"
    with pytest.raises(Unreplayable, match="empreinte"):
        await replay_cycle(altered)
    assert all("never-export" not in str(v) for v in packet.values())


@pytest.mark.asyncio
async def test_suspending_entries_keeps_the_position_stop_managed():
    from tests.test_orchestrator import symbol_state, FakeLLM, llm_signal
    from app.runtime import orchestrator as module
    from unittest.mock import patch

    cfg = config()
    ledger = FeatureLedger(now=NOW)
    ledger.control = {"enabled": False, "revision": 1}
    pos = PositionRow(
        uuid.uuid4(),
        "BTC/EUR",
        D(100),
        D(1),
        D(95),
        D(110),
        NOW - timedelta(hours=4),
        D(5),
        NOW - timedelta(hours=4),
    )
    ledger.position = pos
    ledger.current = "IN_POSITION"
    await ledger.attach_policy("PAPER", pos.id, position_policy(cfg))
    candle = [int((NOW - timedelta(hours=4)).timestamp() * 1000), 100, 105, 90, 92, 1]
    runtime = Orchestrator(
        cfg,
        ledger,
        object(),
        FakeLLM(llm_signal("HOLD")),
        PaperBroker(D(".008"), 5),
        now=lambda: NOW,
        insights=False,
    )
    with (
        patch.object(
            module,
            "build_snapshot",
            lambda *a, **kw: replace(symbol_state(), last_close_at=NOW),
        ),
        patch.object(module, "closed_candles", lambda *a: [candle]),
    ):
        await runtime.run_once()
        assert ledger.position is None and len(ledger.closed) == 1
        ledger.cooldown = NOW - timedelta(seconds=1)
        await runtime.run_once()
    assert ledger.position is None and ledger.control["enabled"] is False


@pytest.mark.asyncio
async def test_experiments_keep_portfolios_and_capital_independent():
    data = historical_data(264)
    rows = data["BTC/EUR"]["candles"]["4h"]
    rows[261][3] = rows[261][4] * 0.90
    start = datetime.fromtimestamp((rows[259][0] + 14400000) / 1000, timezone.utc)
    end = datetime.fromtimestamp((rows[263][0] + 28800000) / 1000, timezone.utc)
    spec = {
        "schema_version": 1,
        "start_at": start.isoformat(),
        "end_at": end.isoformat(),
        "equity": "1000",
        "data": data,
        "variants": [
            {"name": "one", "kind": "mechanical"},
            {"name": "same", "kind": "mechanical"},
        ],
    }
    result = await run_experiment(spec, config())
    one, two = result["variants"]
    assert one["trades"] >= 1
    assert (
        one["equity_end"] == two["equity_end"]
        and one["net_realized"] == two["net_realized"]
    )
    assert one["curve"] == two["curve"] and result["promotion_allowed"] is False
    assert result["comparisons"][0]["final_equity_difference"] == 0
    spec["data"]["BTC/EUR"]["candles"]["1h"].pop(10)
    with pytest.raises(ValueError, match="trou"):
        await run_experiment(spec, config())


def test_confidence_separates_cohorts_and_excludes_unresolved_and_degraded():
    records = [
        {
            "action": "BUY",
            "confidence": str(i / 10),
            "model_name": "m",
            "prompt_sha256": "p",
            "raw_response": {"model_digest": "weights"},
            "closed_at": NOW - timedelta(hours=1),
            "net_pnl_quote": D(1) if i > 5 else D(-1),
            "market_regime": "RANGING",
        }
        for i in range(10)
    ]
    records += [
        {**records[0], "closed_at": NOW + timedelta(hours=1)},
        {**records[0], "degraded": True},
        {**records[0], "raw_response": {"model_digest": "different"}},
    ]
    result = confidence_report(records, until=NOW)
    assert (
        len(result["cohorts"]) == 2
        and result["excluded"]["UNRESOLVED_OR_NOT_EXECUTED"] == 1
    )
    assert all(c["brier"] is None for c in result["cohorts"])
    assert result["auto_threshold_update"] is False and result["selection_bias"] is True


@pytest.mark.asyncio
async def test_observer_never_calls_exchange_mutations_and_distinguishes_partial_protection():
    class ReadOnly:
        def privatePostGetApiKeyInfo(self):
            return {"result": {"permissions": ["query-funds", "query-open-orders"]}}

        def fetch_balance(self):
            return {"total": {"BTC": "1"}}

        def fetch_open_orders(self):
            return [
                {
                    "symbol": "BTC/EUR",
                    "side": "sell",
                    "type": "stop-loss",
                    "stopLossPrice": "95",
                    "amount": "1",
                    "remaining": ".4",
                    "status": "open",
                }
            ]

    ledger = FeatureLedger()
    ledger.position = PositionRow(
        uuid.uuid4(), "BTC/EUR", D(100), D(1), D(95), None, NOW, D(5)
    )
    result = await observe_protection(ledger, ReadOnly(), "LIVE", now=NOW)
    assert any(i["key"].startswith("STOP:") for i in result["issues"])
    assert ledger.current == "EMERGENCY_HALT"

    class Unsafe(ReadOnly):
        def privatePostGetApiKeyInfo(self):
            return {
                "result": {
                    "permissions": ["query-funds", "query-open-orders", "cancel-orders"]
                }
            }

    with pytest.raises(ValueError, match="lecture"):
        verify_observer_key(Unsafe())


def test_opportunity_counts_do_not_sum_approvals_into_executed_orders():
    events = [
        {"stage": "APPROVED", "terminal": False, "opportunity_id": "a"},
        {"stage": "NOT_SELECTED", "terminal": True, "opportunity_id": "a"},
        {"stage": "FILLED", "terminal": True, "opportunity_id": "b"},
    ]
    result = opportunity_summary(events)
    assert result["unique_opportunities"] == 2 and result["terminal_attempts"] == 2
    assert result["terminal_reasons"]["FILLED"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,expected", [("observe", "BTC/EUR"), ("paper_select", "ETH/EUR")]
)
async def test_runtime_ranking_observes_by_default_and_selects_only_opt_in_paper(
    mode, expected
):
    data = historical_data()
    data["ETH/EUR"] = deepcopy(data["BTC/EUR"])
    now = datetime.fromtimestamp(
        (data["BTC/EUR"]["candles"]["4h"][259][0] + 14400000) / 1000, timezone.utc
    )
    price = D(str(data["BTC/EUR"]["candles"]["4h"][259][4]))
    for symbol, spread in [("BTC/EUR", D(".005")), ("ETH/EUR", D(".001"))]:
        data[symbol]["books"] = [
            {
                "timestamp": int(now.timestamp() * 1000),
                "asks": [[str(price * (1 + spread)), "10000"]],
                "bids": [[str(price * (1 - spread)), "10000"]],
            }
        ]
    cfg = config(TRADING_SYMBOLS="BTC/EUR,ETH/EUR", RANKING_MODE=mode)
    ledger = FeatureLedger(now=now)
    runtime = Orchestrator(
        cfg,
        ledger,
        HistoricalExchange(data, now),
        ExperimentSignal({"kind": "mechanical"}),
        PaperBroker(D(".008"), 5),
        now=lambda: now,
    )
    await runtime.run_once()
    assert ledger.position.symbol == expected
    ranked = next(
        e["body"]["candidates"] for e in ledger.events if e["stage"] == "RANKING"
    )
    assert ranked[0]["symbol"] == "ETH/EUR" and all(r["eligible"] for r in ranked)
    assert len([o for o in ledger.orders if o["purpose"] == "ENTRY"]) == 1
    assert (await replay_cycle(next(iter(ledger.packets.values()))))[
        "status"
    ] == "IDENTICAL"


@pytest.mark.asyncio
async def test_entry_suspension_during_inference_prevents_order_submission():
    data = historical_data()
    now = datetime.fromtimestamp(
        (data["BTC/EUR"]["candles"]["4h"][259][0] + 14400000) / 1000, timezone.utc
    )
    ledger = FeatureLedger(now=now)

    class PausingSignal(ExperimentSignal):
        async def propose(self, *a, **kw):
            ledger.control = {"enabled": False, "revision": 1}
            return await super().propose(*a, **kw)

    runtime = Orchestrator(
        config(),
        ledger,
        HistoricalExchange(data, now),
        PausingSignal({"kind": "mechanical"}),
        PaperBroker(D(".008"), 5),
        now=lambda: now,
    )
    await runtime.run_once()
    assert not ledger.orders and ledger.position is None
    assert any(e["stage"] == "ENTRY_DISABLED" for e in ledger.events)


@pytest.mark.asyncio
async def test_selected_candidate_is_revalidated_at_the_final_price():
    data = historical_data()
    now = datetime.fromtimestamp(
        (data["BTC/EUR"]["candles"]["4h"][259][0] + 14400000) / 1000, timezone.utc
    )

    class MovingExchange(HistoricalExchange):
        def __init__(self, *a):
            super().__init__(*a)
            self.ticks = 0

        def fetch_ticker(self, symbol):
            self.ticks += 1
            return (
                {"last": "1", "timestamp": int(now.timestamp() * 1000)}
                if self.ticks >= 2
                else super().fetch_ticker(symbol)
            )

    ledger = FeatureLedger(now=now)
    await Orchestrator(
        config(),
        ledger,
        MovingExchange(data, now),
        ExperimentSignal({"kind": "mechanical"}),
        PaperBroker(D(".008"), 5),
        now=lambda: now,
    ).run_once()
    assert not ledger.orders
    assert any(e["stage"] == "APPROVED" for e in ledger.events)
    assert any(e["stage"] == "REVALIDATION_REJECTED" for e in ledger.events)


@pytest.mark.asyncio
async def test_observation_failure_blocks_new_entries_but_keeps_existing_stop_management():
    from tests.test_orchestrator import symbol_state, FakeLLM, llm_signal
    from app.runtime import orchestrator as module
    from unittest.mock import patch

    class FullStorage(FeatureLedger):
        async def begin_cycle(self, *a):
            raise RuntimeError("quota")

    ledger = FullStorage()
    cfg = config()
    runtime = Orchestrator(
        cfg,
        ledger,
        object(),
        FakeLLM(llm_signal("HOLD")),
        PaperBroker(D(".008"), 5),
        now=lambda: NOW,
    )
    flat = await runtime.run_once()
    assert not ledger.orders and flat.errors
    pos = PositionRow(
        uuid.uuid4(),
        "BTC/EUR",
        D(100),
        D(1),
        D(95),
        D(110),
        NOW - timedelta(hours=4),
        D(5),
        NOW - timedelta(hours=4),
    )
    ledger.position = pos
    ledger.current = "IN_POSITION"
    await ledger.attach_policy("PAPER", pos.id, position_policy(cfg))
    candle = [int((NOW - timedelta(hours=4)).timestamp() * 1000), 100, 105, 90, 92, 1]
    with (
        patch.object(
            module,
            "build_snapshot",
            lambda *a, **kw: replace(symbol_state(), last_close_at=NOW),
        ),
        patch.object(module, "closed_candles", lambda *a: [candle]),
    ):
        managed = await runtime.run_once()
    assert ledger.position is None and len(ledger.closed) == 1 and managed.errors


def test_recording_limit_cannot_interrupt_an_already_executed_side_effect():
    from app.insights.replay import Recorder, validate_packet

    recorder = Recorder(config())
    recorder.calls = [{}] * 5000

    class Source:
        def operation(self):
            return "already executed"

    assert recorder.proxy("source", Source()).operation() == "already executed"
    packet = recorder.packet()
    assert packet["status"] == "INCOMPLETE" and packet["trace"] == []
    with pytest.raises(Unreplayable):
        validate_packet(packet)


@pytest.mark.asyncio
async def test_observer_notification_uses_read_only_payload_and_does_not_follow_redirects():
    import httpx
    from app.insights.watch import notify_incident

    sent = []

    def receive(request):
        sent.append(request)
        return httpx.Response(200, json={"ok": True})

    assert await notify_incident(
        "https://alerts.example/incident",
        {"mode": "PAPER", "amount": D("1.23")},
        transport=httpx.MockTransport(receive),
    )
    assert len(sent) == 1 and b"1.23" in sent[0].content
    with pytest.raises(ValueError, match="HTTPS"):
        await notify_incident("http://alerts.example", {})
    with pytest.raises(httpx.HTTPStatusError):
        await notify_incident(
            "https://alerts.example",
            {},
            transport=httpx.MockTransport(
                lambda r: httpx.Response(
                    302, headers={"location": "https://other.example"}
                )
            ),
        )


@pytest.mark.asyncio
async def test_failed_alert_is_retried_even_if_incident_resolves_before_delivery(tmp_path):
    from app.insights.notifications import NotificationOutbox

    queue = NotificationOutbox(tmp_path / "outbox.sqlite3", source="test", mode="PAPER")
    issue = {"key": "STOP:1", "reason": "protection insuffisante"}
    first = {"observation": {"observed_at": NOW}, "issues": [issue]}
    queue.observe(first)
    calls = []

    async def fail(url, payload):
        calls.append(payload)
        raise TimeoutError("webhook unavailable")

    with pytest.raises(TimeoutError):
        await queue.deliver("https://alerts.example", first, fail)

    resolved = {"observation": {"observed_at": NOW + timedelta(seconds=15)}, "issues": []}
    queue.observe(resolved)

    async def succeed(url, payload):
        calls.append(payload)

    await queue.deliver("https://alerts.example", resolved, succeed)
    assert len(calls) == 2
    assert calls[1]["new_incidents"] == ["STOP:1"]
    pending = calls[1]["pending_incidents"]
    assert pending[0]["issue"] == issue
    assert pending[0]["observed_at"] == NOW.isoformat()
    assert pending[0]["event_id"] == calls[0]["pending_incidents"][0]["event_id"]
    assert calls[1]["issues"] == []
    assert not queue.pending
    queue.close()


@pytest.mark.asyncio
async def test_delivered_alert_is_deduplicated_until_incident_reopens(tmp_path):
    from app.insights.notifications import NotificationOutbox

    queue = NotificationOutbox(tmp_path / "outbox.sqlite3", source="test", mode="PAPER")
    result = {"observation": {"observed_at": NOW}, "issues": [{"key": "STOP:1"}]}
    sent = []

    async def send(url, payload):
        sent.append(payload)

    queue.observe(result)
    await queue.deliver("https://alerts.example", result, send)
    queue.observe(result)
    await queue.deliver("https://alerts.example", result, send)
    assert len(sent) == 1
    queue.observe({"observation": {"observed_at": NOW}, "issues": []})
    queue.observe(result)
    await queue.deliver("https://alerts.example", result, send)
    assert len(sent) == 2
    queue.close()


def test_confidence_does_not_mix_exit_policies_or_certify_unknown_versions():
    base = {
        "action": "BUY",
        "confidence": ".8",
        "model_name": "m",
        "raw_response": {
            "model_digest": "weights",
            "prompt_template_digest": "template",
            "lessons_digest": "lessons",
        },
        "closed_at": NOW - timedelta(hours=1),
        "net_pnl_quote": D(1),
        "market_regime": "RANGING",
    }
    records = [{**base, "policy_id": "old"} for _ in range(20)] + [
        {**base, "policy_id": "new"},
        base,
    ]
    report = confidence_report(records, until=NOW)
    assert len(report["cohorts"]) == 3
    assert (
        next(c for c in report["cohorts"] if c["policy_id"] == "old")["evidence"]
        == "DESCRIPTIVE_ONLY"
    )
    assert (
        next(c for c in report["cohorts"] if c["policy_id"] is None)["evidence"]
        == "INSUFFICIENT"
    )


@pytest.mark.asyncio
async def test_replay_preserves_model_identity_and_already_applied_settings_on_later_cycles():
    data = historical_data()
    now = datetime.fromtimestamp(
        (data["BTC/EUR"]["candles"]["4h"][259][0] + 14400000) / 1000, timezone.utc
    )

    class VersionedLedger(FeatureLedger):
        async def runtime_snapshot(self, *a):
            return {
                "payload": {"MIN_CONFIDENCE": ".7"},
                "updated_at": NOW,
                "revision": 1,
            }

    class IdentifiedSignal(ExperimentSignal):
        async def model_identity(self):
            return "actual-model-weights"

    ledger = VersionedLedger(now=now)
    exchange = HistoricalExchange(data, now)
    runtime = Orchestrator(
        config(),
        ledger,
        exchange,
        IdentifiedSignal({"kind": "mechanical"}),
        PaperBroker(D(".008"), 5),
        now=lambda: ledger.now,
        settings_loader=lambda overrides=None: config(**(overrides or {})),
    )
    await runtime.run_once()
    ledger.now = exchange.now = now + timedelta(hours=4)
    await runtime.run_once()
    assert len(ledger.packets) == 2
    for packet in ledger.packets.values():
        assert packet["manifest"]["model_digest"] == "actual-model-weights"
        assert (await replay_cycle(packet))["status"] == "IDENTICAL"


@pytest.mark.asyncio
async def test_uncertain_live_entry_blocks_retry_without_sending_a_compensating_sell():
    data = historical_data()
    now = datetime.fromtimestamp(
        (data["BTC/EUR"]["candles"]["4h"][259][0] + 14400000) / 1000, timezone.utc
    )
    ledger = FeatureLedger(now=now)

    class UncertainBroker(PaperBroker):
        def entry(self, *a, **kw):
            raise RuntimeError("unknown external result")

        def emergency_exit(self, *a, **kw):
            raise AssertionError("no blind compensation")

    runtime = Orchestrator(
        config(),
        ledger,
        HistoricalExchange(data, now),
        ExperimentSignal({"kind": "mechanical"}),
        UncertainBroker(D(".008"), 5),
        now=lambda: now,
    )
    runtime._mode = (
        "LIVE"  # Simulate the owner state only; no exchange transport or live settings.
    )
    with pytest.raises(RuntimeError, match="unknown external"):
        await runtime.run_once()
    assert ledger.current == "EMERGENCY_HALT" and not ledger.orders
    assert any(e["stage"] == "EXECUTION_UNCERTAIN" for e in ledger.events)
