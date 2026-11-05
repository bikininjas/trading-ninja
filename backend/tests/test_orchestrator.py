"""Tests de bout en bout de l'orchestrateur, avec des doubles.

Ce qui est vérifié ici n'est pas le calcul du pare-feu (129 tests le couvrent) mais
le **câblage** : une intention approuvée produit une position, une intention HOLD
n'entre pas dans le pare-feu, un repli LLM ne produit rien, et une bougie qui touche
le stop ferme la position puis pose un cooldown.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.core.settings import load_settings
from app.db.repository import AccountSnapshot, PositionRow
from app.engine.machine import COOLDOWN, HALT, IDLE, IN_POSITION
from app.engine.risk_gate import ExchangeConstraints
from app.exchange.kraken import KrakenMarketInfo
from app.execution.live import RemotePositionState
from app.execution.paper import Fill, PaperBroker
from app.llm.client import LLMSignal
from app.market.feed import MarketContext, SymbolState
from app.runtime import orchestrator as orch_module
from app.runtime.orchestrator import Orchestrator

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)

CONSTRAINTS = ExchangeConstraints(
    amount_step=Decimal("0.001"), min_amount=Decimal("0.001"),
    min_notional=Decimal("1"), price_tick=Decimal("0.01"),
)
INFO = KrakenMarketInfo(
    symbol="BTC/EUR", exchange_symbol="XXBTZEUR", base="BTC", quote="EUR", active=True,
    taker_fee=Decimal("0.0026"), maker_fee=Decimal("0.0016"), constraints=CONSTRAINTS,
)
CONTEXT = MarketContext(
    ema_50_h4=Decimal("99.5"), rsi_14_h4=Decimal("55"), macd_histogram=Decimal("0.5"),
    order_book_imbalance=Decimal("0.1"), candles_closed=320,
)


def symbol_state(price: str = "100", ema: str = "90", ema50: str | None = None) -> SymbolState:
    from app.engine.risk_gate import MarketSnapshot

    price_d = Decimal(price)
    ema50_d = Decimal(ema50) if ema50 is not None else price_d * Decimal("0.995")
    snapshot = MarketSnapshot(
        symbol="BTC/EUR", price=price_d, ema_200_h4=Decimal(ema),
        atr_14_h4=Decimal("1"), last_closed_h1_return_atr=Decimal("0"),
        tick_at=NOW, now=NOW, ema_200_slope_atr=Decimal("10"),
    )
    context = MarketContext(
        ema_50_h4=ema50_d, rsi_14_h4=Decimal("55"), macd_histogram=Decimal("0.5"),
        order_book_imbalance=Decimal("0.1"), candles_closed=320,
    )
    return SymbolState(
        symbol="BTC/EUR", info=INFO, snapshot=snapshot, context=context, last_close_at=NOW
    )


class FakeRepo:
    def __init__(self, state: str = IDLE, position: PositionRow | None = None,
                 free_quote: str = "9.37"):
        self.current_state = state
        self.position = position
        self.cooldown: datetime | None = None
        self.transitions: list[tuple[str, str]] = []
        self.signals: list[dict] = []
        self.last_signals: dict[str, datetime] = {}
        self.evaluations: list[dict] = []
        self.orders: list[dict] = []
        self.snapshots: list[dict] = []
        self.closed: dict | None = None
        self.after_close: dict | None = None
        self.sweeps: list[dict] = []
        self.deferred = Decimal(0)
        self.forced_halt: str | None = None
        self.forced_halt_auto_resume_at: datetime | None = None
        self.halt_detail: str | None = None
        self.resumes: list[str] = []
        self.shadows: list[dict] = []
        self.lessons: list[str] = []
        self.account_snapshot = AccountSnapshot(
            equity=Decimal(free_quote), free_quote=Decimal(free_quote), hwm=Decimal("9.37"),
            daily_drawdown_pct=Decimal(0), weekly_drawdown_pct=Decimal(0),
            from_hwm_drawdown_pct=Decimal(0), consecutive_losses=0, orders_last_hour=0,
            has_open_position=position is not None,
        )

    async def ensure_mode(self, mode): pass

    async def state(self, mode): return self.current_state, self.cooldown

    async def transition(self, mode, expected, target, reason, code=None, context=None):
        assert self.current_state == expected, (
            f"état {self.current_state}, attendu {expected}")
        self.transitions.append((expected, target))
        self.current_state = target

    async def account(self, mode, starting): return self.account_snapshot

    async def open_position(self, mode): return self.position

    async def snapshot_equity(self, mode, **kwargs): self.snapshots.append(kwargs)

    async def insert_signal(self, mode, symbol, model_name, **kwargs):
        self.signals.append({"symbol": symbol, "model": model_name, **kwargs})
        self.last_signals[symbol] = NOW
        return uuid.uuid4(), NOW

    async def last_signal_at(self, mode, symbol):
        return self.last_signals.get(symbol)

    async def insert_evaluation(self, mode, symbol, **kwargs):
        self.evaluations.append({"symbol": symbol, **kwargs})
        return uuid.uuid4(), NOW

    async def insert_shadow_trade(self, mode, symbol, **kwargs):
        self.shadows.append({"symbol": symbol, **kwargs})
        return uuid.uuid4()

    async def create_position(self, mode, **kwargs):
        self.position = PositionRow(
            id=uuid.uuid4(), symbol=kwargs["symbol"], entry_price=kwargs["entry_price"],
            amount=kwargs["amount"], stop_loss_price=kwargs["stop_loss_price"],
            take_profit_price=kwargs["take_profit_price"], opened_at=NOW,
            risk_amount_quote=kwargs["risk_amount_quote"],
        )
        return self.position.id

    async def insert_order(self, mode, **kwargs):
        self.orders.append(kwargs)
        return uuid.uuid4()

    async def close_position(self, position_id, **kwargs):
        self.closed = kwargs
        self.position = None

    async def update_after_close(self, mode, **kwargs): self.after_close = kwargs

    async def insert_sweep(self, mode, **kwargs):
        self.sweeps.append(kwargs)
        return uuid.uuid4()

    async def deferred_sweep_quote(self, mode):
        return self.deferred

    async def set_cooldown(self, mode, until): self.cooldown = until

    async def force_halt(self, mode, reason, code=None, auto_resume_at=None):
        self.forced_halt = reason
        self.forced_halt_auto_resume_at = auto_resume_at
        self.current_state = "EMERGENCY_HALT"
        self.cooldown = auto_resume_at

    async def halt_details(self, mode):
        return self.halt_detail

    async def force_resume(self, mode, reason, who="admin"):
        self.resumes.append(reason)
        self.current_state = "IDLE"
        self.cooldown = None

    async def runtime_settings(self, mode):
        return {}, None

    async def active_lessons(self, mode):
        return self.lessons


class FakeLLM:
    def __init__(self, signal: LLMSignal):
        self.signal = signal
        self.calls = 0
        self.lessons_seen: list = []

    async def propose(self, symbol, snapshot, context, **kwargs):
        self.calls += 1
        self.lessons_seen.append(kwargs.get("lessons"))
        return self.signal


def llm_signal(action: str = "BUY", confidence: str = "0.9", *, degraded: bool = False,
               reason: str | None = None) -> LLMSignal:
    return LLMSignal(
        action=action, confidence=Decimal(confidence),
        market_regime="TRENDING_UP", directional_bias="BULLISH", thesis="test",
        raw={}, prompt_payload={"rsi_14": "55.00"}, prompt_sha256="x" * 64,
        inference_ms=12, degraded=degraded, degradation_reason=reason,
    )


def settings(tmp_path: Path, **extra):
    overrides = {
        "TRADING_MODE": "paper", "EXCHANGE_SANDBOX_MODE": "true",
        "EXCHANGE_ID": "kraken", "TRADING_SYMBOL": "BTC/EUR",
        "TRADING_SYMBOLS": "BTC/EUR", "RISK_PROFILE": "micro",
        "PAPER_START_EQUITY": "9.37", "LLM_MODEL_NAME": "test-model",
    }
    overrides.update(extra)
    return load_settings(env_file=tmp_path / "absent.env", overrides=overrides)


def orchestrator(tmp_path: Path, repo: FakeRepo, llm: FakeLLM,
                 settings_=None) -> Orchestrator:
    return Orchestrator(
        settings_ or settings(tmp_path), repo, exchange=object(), signal_client=llm,
        broker=PaperBroker(taker_fee=Decimal("0.0026"), slippage_bps=5),
        now=lambda: NOW,
        settings_loader=lambda overrides=None: settings(tmp_path, **(overrides or {})),
    )


@pytest.fixture(autouse=True)
def _patch_feed(monkeypatch):
    monkeypatch.setattr(orch_module, "build_snapshot",
                        lambda exchange, symbol, **kwargs: symbol_state())
    monkeypatch.setattr(orch_module, "last_price",
                        lambda exchange, symbol: Decimal("100"))


@pytest.mark.asyncio
async def test_approved_intent_opens_a_paper_position(tmp_path):
    repo, llm = FakeRepo(), FakeLLM(llm_signal("BUY"))
    report = await orchestrator(tmp_path, repo, llm).run_once()

    assert repo.current_state == IN_POSITION
    assert repo.position is not None
    assert len(repo.orders) == 2  # entrée + stop
    assert repo.orders[1]["purpose"] == "STOP_LOSS"
    assert repo.orders[1]["stop_price"] == Decimal("97.50")
    assert repo.evaluations[0]["decision"].approved is True
    assert report.position_opened is not None


@pytest.mark.asyncio
async def test_hold_never_reaches_the_risk_gate(tmp_path):
    repo, llm = FakeRepo(), FakeLLM(llm_signal("HOLD", "0.1"))
    report = await orchestrator(tmp_path, repo, llm).run_once()

    assert repo.current_state == IDLE
    assert repo.position is None
    assert repo.signals and not repo.evaluations
    assert report.evaluations == 0
    assert repo.shadows and repo.shadows[0]["kind"] == "MODEL_HOLD"


@pytest.mark.asyncio
async def test_active_lessons_are_passed_to_the_model(tmp_path):
    repo, llm = FakeRepo(), FakeLLM(llm_signal("HOLD", "0.1"))
    repo.lessons = ["Les achats dans un EMA200 descendant perdent."]
    await orchestrator(tmp_path, repo, llm).run_once()

    assert llm.lessons_seen[0] == repo.lessons


@pytest.mark.asyncio
async def test_rejected_buy_is_recorded_as_a_shadow_trade(tmp_path):
    repo, llm = FakeRepo(), FakeLLM(llm_signal("BUY", "0.50"))
    report = await orchestrator(tmp_path, repo, llm).run_once()

    assert repo.position is None
    assert report.approved == 0
    assert len(repo.shadows) == 1
    shadow = repo.shadows[0]
    assert shadow["kind"] == "GATE_REJECTED_BUY"
    assert shadow["rejection_code"] == "CONFIDENCE_TOO_LOW"
    assert shadow["plan_status"] == "OPEN"
    assert shadow["amount"] is not None
    assert shadow["stop_loss_price"] is not None


@pytest.mark.asyncio
async def test_degraded_llm_produces_no_evaluation(tmp_path):
    repo = FakeRepo()
    llm = FakeLLM(llm_signal("HOLD", "0", degraded=True, reason="timeout"))
    report = await orchestrator(tmp_path, repo, llm).run_once()

    assert repo.current_state == IDLE
    assert report.degraded_calls == 1
    assert not repo.evaluations


@pytest.mark.asyncio
async def test_counter_trend_buy_is_refused_by_the_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(orch_module, "build_snapshot",
                        lambda exchange, symbol, **kwargs: symbol_state(price="80", ema="90"))
    monkeypatch.setattr(orch_module, "last_price",
                        lambda exchange, symbol: Decimal("80"))
    repo = FakeRepo()
    orch = orchestrator(
        tmp_path, repo, FakeLLM(llm_signal("BUY")),
        settings(tmp_path, ENTRY_RULE_ENABLED="false"),
    )
    report = await orch.run_once()

    assert repo.current_state == IDLE
    assert repo.position is None
    decision = repo.evaluations[0]["decision"]
    assert decision.approved is False
    assert decision.rejection_code.value == "REJECTED_COUNTER_TREND_EMA200"
    assert report.approved == 0


@pytest.mark.asyncio
async def test_cooldown_is_resumed_after_a_restart(tmp_path):
    # Critère 1.2 : un redémarrage pendant le cooldown ne repart pas de zéro et ne trade pas.
    repo = FakeRepo(state=COOLDOWN)
    repo.cooldown = NOW + timedelta(minutes=10)
    report = await orchestrator(tmp_path, repo, FakeLLM(llm_signal("BUY"))).run_once()

    assert repo.current_state == COOLDOWN
    assert repo.transitions == []
    assert repo.signals == []
    assert any("cooldown" in action for action in report.actions)


@pytest.mark.asyncio
async def test_expired_cooldown_returns_to_idle(tmp_path):
    repo = FakeRepo(state=COOLDOWN)
    repo.cooldown = NOW - timedelta(minutes=1)
    await orchestrator(tmp_path, repo, FakeLLM(llm_signal("HOLD", "0.1"))).run_once()

    assert (COOLDOWN, IDLE) in repo.transitions
    assert repo.current_state == IDLE


@pytest.mark.asyncio
async def test_a_closed_candle_is_evaluated_only_once(tmp_path):
    # Phase 2.1 : une bougie clôturée = une évaluation, y compris après un
    # redémarrage. Le second passage ne doit ni rappeler le modèle ni écrire un
    # nouveau signal.
    repo = FakeRepo()
    llm = FakeLLM(llm_signal("HOLD", "0.1"))
    orch = orchestrator(tmp_path, repo, llm)

    first = await orch.run_once()
    second = await orch.run_once()

    assert first.skipped == 0
    assert len(repo.signals) == 1
    assert llm.calls == 1
    assert second.skipped == 1
    assert any("déjà évaluée" in action for action in second.actions)


@pytest.mark.asyncio
async def test_open_position_is_recovered_after_a_restart(tmp_path, monkeypatch):
    # Critère 1.7 (partie base) : une position ouverte est retrouvée et surveillée.
    position = PositionRow(
        id=uuid.uuid4(), symbol="BTC/EUR", entry_price=Decimal("100"),
        amount=Decimal("0.046"), stop_loss_price=Decimal("96"),
        take_profit_price=Decimal("105"), opened_at=NOW - timedelta(hours=1),
        risk_amount_quote=Decimal("0.115"),
    )
    repo = FakeRepo(state=IN_POSITION, position=position)
    candle = [(NOW - timedelta(hours=4)).timestamp() * 1000, "100", "102", "98", "101", "1"]
    monkeypatch.setattr(orch_module, "closed_candles",
                        lambda exchange, symbol, timeframe, limit: [candle])

    report = await orchestrator(tmp_path, repo, FakeLLM(llm_signal("HOLD"))).run_once()

    assert repo.current_state == IN_POSITION
    assert repo.position is not None
    assert any("aucune sortie" in action for action in report.actions)


def test_drawdown_maths_are_exact():
    from app.db.repository import _drawdown

    assert _drawdown(Decimal("10"), Decimal("10")) == 0
    assert _drawdown(Decimal("10"), Decimal("9")) == Decimal("0.1")
    assert _drawdown(Decimal("0"), Decimal("9")) == 0
    assert _drawdown(Decimal("10"), Decimal("12")) == 0  # un nouveau plus haut n'est pas un drawdown


@pytest.mark.asyncio
async def test_candle_hitting_the_stop_closes_the_position_and_cools_down(tmp_path, monkeypatch):
    opened = NOW - timedelta(hours=5)
    position = PositionRow(
        id=uuid.uuid4(), symbol="BTC/EUR", entry_price=Decimal("100"),
        amount=Decimal("0.046"), stop_loss_price=Decimal("96"),
        take_profit_price=Decimal("105"), opened_at=opened,
        risk_amount_quote=Decimal("0.115"),
    )
    repo = FakeRepo(state=IN_POSITION, position=position, free_quote="5.32")
    candle = [(NOW - timedelta(hours=4)).timestamp() * 1000, "100", "101", "95", "99", "1"]
    monkeypatch.setattr(orch_module, "closed_candles",
                        lambda exchange, symbol, timeframe, limit: [candle])

    report = await orchestrator(tmp_path, repo, FakeLLM(llm_signal("HOLD"))).run_once()

    assert repo.current_state == COOLDOWN
    assert repo.position is None
    assert repo.closed["exit_reason"] == "STOP_LOSS"
    assert repo.closed["net_pnl_quote"] < 0
    assert repo.after_close["consecutive_losses"] == 1
    assert repo.cooldown == NOW + timedelta(minutes=15)
    assert report.position_closed is not None
    assert repo.sweeps == []  # une perte ne produit aucune ligne d'écrémage


class LiveFakeBroker:
    """Broker live minimal : l'exchange dit si la position existe encore."""

    def __init__(self, *, base_free: Decimal, stop_ok: bool, fill: Fill):
        self.base_free = base_free
        self.stop_ok = stop_ok
        self.fill = fill
        self.exit_calls = 0
        self.emergency_calls = 0

    def fee(self, notional):
        return notional * Decimal("0.008")

    @staticmethod
    def exit_trigger(stop_loss_price, take_profit_price, candle):
        return PaperBroker.exit_trigger(stop_loss_price, take_profit_price, candle)

    async def position_state(self, symbol, amount, stop_price):
        return RemotePositionState(
            symbol=symbol, base_total=self.base_free,
            stop_ok=self.stop_ok, stop_order_id=None,
        )

    async def last_sell_fill(self, symbol, *, since=None, limit=20):
        return self.fill

    async def exit(self, amount, market_price, *, symbol, stop_price=None):
        self.exit_calls += 1
        return self.fill

    async def emergency_exit(self, amount, *, symbol, stop_price=None,
                             market_price=None):
        self.emergency_calls += 1
        return self.fill

    async def free_quote(self):
        return Decimal("5.32")


def live_position() -> PositionRow:
    return PositionRow(
        id=uuid.uuid4(), symbol="BTC/EUR", entry_price=Decimal("100"),
        amount=Decimal("0.046"), stop_loss_price=Decimal("96"),
        take_profit_price=Decimal("105"), opened_at=NOW - timedelta(hours=1),
        risk_amount_quote=Decimal("0.115"),
    )


def stop_fill() -> Fill:
    return Fill(
        price=Decimal("95"), amount=Decimal("0.046"),
        notional_quote=Decimal("4.37"), fee_quote=Decimal("0.035"),
        slippage_quote=Decimal("0"),
    )


@pytest.mark.asyncio
async def test_a_live_stop_already_triggered_is_recorded_without_another_sell(tmp_path):
    repo = FakeRepo(state=IN_POSITION, position=live_position(), free_quote="5.32")
    broker = LiveFakeBroker(base_free=Decimal("0"), stop_ok=False, fill=stop_fill())
    orch = Orchestrator(
        settings(tmp_path), repo, exchange=object(),
        signal_client=FakeLLM(llm_signal("HOLD")), broker=broker,
        now=lambda: NOW,
        settings_loader=lambda overrides=None: settings(tmp_path, **(overrides or {})),
    )

    report = await orch.run_once()

    assert broker.exit_calls == 0
    assert broker.emergency_calls == 0
    assert repo.closed["exit_reason"] == "STOP_LOSS"
    assert repo.current_state == COOLDOWN
    assert report.position_closed is not None


@pytest.mark.asyncio
async def test_a_live_position_without_a_stop_is_sold_then_halted(tmp_path):
    repo = FakeRepo(state=IN_POSITION, position=live_position(), free_quote="5.32")
    broker = LiveFakeBroker(
        base_free=Decimal("0.046"), stop_ok=False, fill=stop_fill()
    )
    orch = Orchestrator(
        settings(tmp_path), repo, exchange=object(),
        signal_client=FakeLLM(llm_signal("HOLD")), broker=broker,
        now=lambda: NOW,
        settings_loader=lambda overrides=None: settings(tmp_path, **(overrides or {})),
    )

    await orch.run_once()

    assert broker.exit_calls == 0
    assert broker.emergency_calls == 1
    assert repo.closed["exit_reason"] == "STOP_MISSING"
    assert repo.current_state == HALT


@pytest.mark.asyncio
async def test_winning_trade_sweeps_thirty_percent(tmp_path, monkeypatch):
    from dataclasses import replace

    opened = NOW - timedelta(hours=5)
    position = PositionRow(
        id=uuid.uuid4(), symbol="BTC/EUR", entry_price=Decimal("100"),
        amount=Decimal("0.046"), stop_loss_price=Decimal("96"),
        take_profit_price=Decimal("105"), opened_at=opened,
        risk_amount_quote=Decimal("0.115"),
    )
    repo = FakeRepo(state=IN_POSITION, position=position, free_quote="5.32")
    candle = [(NOW - timedelta(hours=4)).timestamp() * 1000, "100", "110", "99", "108", "1"]
    monkeypatch.setattr(orch_module, "closed_candles",
                        lambda exchange, symbol, timeframe, limit: [candle])

    # Seuil abaissé pour que l'écrémage soit exécuté sur un gain de test.
    settings_ = replace(settings(tmp_path), sweep_min_transfer_quote=Decimal("0.0001"))
    orch = Orchestrator(
        settings_, repo, exchange=object(), signal_client=FakeLLM(llm_signal("HOLD")),
        broker=PaperBroker(taker_fee=Decimal("0.0026"), slippage_bps=5),
        now=lambda: NOW,
    )
    report = await orch.run_once()

    assert repo.closed["exit_reason"] == "TAKE_PROFIT"
    assert repo.closed["net_pnl_quote"] > 0
    assert len(repo.sweeps) == 1
    sweep = repo.sweeps[0]
    assert sweep["status"] == "SIMULATED"
    assert sweep["swept_amount"] > 0
    # Le ledger reste équilibré : équité = cash + réserve.
    last = repo.snapshots[-1]
    assert last["equity"] == last["free_quote"] + sweep["swept_amount"]
    assert "écrémé" in report.position_closed


@pytest.mark.asyncio
async def test_small_win_is_recorded_as_skipped(tmp_path, monkeypatch):
    opened = NOW - timedelta(hours=5)
    position = PositionRow(
        id=uuid.uuid4(), symbol="BTC/EUR", entry_price=Decimal("100"),
        amount=Decimal("0.046"), stop_loss_price=Decimal("96"),
        take_profit_price=Decimal("105"), opened_at=opened,
        risk_amount_quote=Decimal("0.115"),
    )
    repo = FakeRepo(state=IN_POSITION, position=position, free_quote="5.32")
    candle = [(NOW - timedelta(hours=4)).timestamp() * 1000, "100", "110", "99", "108", "1"]
    monkeypatch.setattr(orch_module, "closed_candles",
                        lambda exchange, symbol, timeframe, limit: [candle])

    # Seuil par défaut (1,00 EUR) : 30 % d'un gain de ~0,20 EUR reste en dessous.
    await orchestrator(tmp_path, repo, FakeLLM(llm_signal("HOLD"))).run_once()

    assert len(repo.sweeps) == 1
    assert repo.sweeps[0]["status"] == "SKIPPED_BELOW_MINIMUM"
    assert repo.sweeps[0]["swept_amount"] == 0
    assert repo.sweeps[0]["next_retry_at"] is not None


@pytest.mark.asyncio
async def test_deferred_sweeps_accumulate_until_the_threshold(tmp_path, monkeypatch):
    from dataclasses import replace

    position = PositionRow(
        id=uuid.uuid4(), symbol="BTC/EUR", entry_price=Decimal("100"),
        amount=Decimal("0.046"), stop_loss_price=Decimal("96"),
        take_profit_price=Decimal("105"), opened_at=NOW - timedelta(hours=5),
        risk_amount_quote=Decimal("0.115"),
    )
    repo = FakeRepo(state=IN_POSITION, position=position, free_quote="5.32")
    repo.deferred = Decimal("0.06")  # report d'un trade précédent
    candle = [(NOW - timedelta(hours=4)).timestamp() * 1000, "100", "110", "99", "108", "1"]
    monkeypatch.setattr(orch_module, "closed_candles",
                        lambda exchange, symbol, timeframe, limit: [candle])

    settings_ = replace(settings(tmp_path), sweep_min_transfer_quote=Decimal("0.10"))
    orch = Orchestrator(
        settings_, repo, exchange=object(), signal_client=FakeLLM(llm_signal("HOLD")),
        broker=PaperBroker(taker_fee=Decimal("0.0026"), slippage_bps=5),
        now=lambda: NOW,
    )
    await orch.run_once()

    # 0,06 reporté + ~0,061 du trade = ~0,121 >= 0,10 : l'écrémage se déclenche.
    sweep = repo.sweeps[0]
    assert sweep["status"] == "SIMULATED"
    assert sweep["swept_amount"] > Decimal("0.11")


# ---------------------------------------------------------------------------
# Autonomie du LLM : SELL et HALT
# ---------------------------------------------------------------------------


def open_position(opened_hours_ago: int = 5) -> PositionRow:
    return PositionRow(
        id=uuid.uuid4(), symbol="BTC/EUR", entry_price=Decimal("100"),
        amount=Decimal("0.046"), stop_loss_price=Decimal("96"),
        take_profit_price=Decimal("105"),
        opened_at=NOW - timedelta(hours=opened_hours_ago),
        risk_amount_quote=Decimal("0.115"),
    )


def flat_candle() -> list:
    return [(NOW - timedelta(hours=4)).timestamp() * 1000, "100", "102", "98", "101", "1"]


@pytest.mark.asyncio
async def test_llm_sell_closes_the_position(tmp_path, monkeypatch):
    repo = FakeRepo(state=IN_POSITION, position=open_position(), free_quote="5.32")
    monkeypatch.setattr(orch_module, "closed_candles",
                        lambda exchange, symbol, timeframe, limit: [flat_candle()])

    report = await orchestrator(tmp_path, repo, FakeLLM(llm_signal("SELL", "0.90"))).run_once()

    assert repo.current_state == COOLDOWN
    assert repo.position is None
    assert repo.closed["exit_reason"] == "LLM_SELL"
    assert repo.orders[-1]["purpose"] == "LLM_EXIT"
    assert report.position_closed is not None


@pytest.mark.asyncio
async def test_llm_sell_without_a_position_is_ignored(tmp_path):
    repo = FakeRepo()
    report = await orchestrator(
        tmp_path, repo, FakeLLM(llm_signal("SELL", "0.90"))
    ).run_once()

    assert repo.current_state == IDLE
    assert repo.position is None
    assert not repo.evaluations
    assert any("SELL sans position" in action for action in report.actions)


@pytest.mark.asyncio
async def test_llm_halt_liquidates_and_halts(tmp_path, monkeypatch):
    repo = FakeRepo(state=IN_POSITION, position=open_position(), free_quote="5.32")
    monkeypatch.setattr(orch_module, "closed_candles",
                        lambda exchange, symbol, timeframe, limit: [flat_candle()])

    report = await orchestrator(tmp_path, repo, FakeLLM(llm_signal("HALT", "0.90"))).run_once()

    assert repo.position is None
    assert repo.closed["exit_reason"] == "LLM_HALT"
    assert repo.forced_halt is not None
    assert report.state_after == "EMERGENCY_HALT"


@pytest.mark.asyncio
async def test_llm_halt_when_flat_halts_without_trade(tmp_path):
    repo = FakeRepo()
    report = await orchestrator(
        tmp_path, repo, FakeLLM(llm_signal("HALT", "0.90"))
    ).run_once()

    assert repo.position is None
    assert repo.forced_halt is not None
    assert report.state_after == "EMERGENCY_HALT"


@pytest.mark.asyncio
async def test_low_confidence_halt_is_ignored(tmp_path):
    repo = FakeRepo()
    report = await orchestrator(
        tmp_path, repo, FakeLLM(llm_signal("HALT", "0.60"))
    ).run_once()

    assert repo.forced_halt is None
    assert repo.current_state == IDLE
    assert any("HALT ignoré" in action for action in report.actions)


@pytest.mark.asyncio
async def test_entry_rule_blocks_a_far_pullback(tmp_path, monkeypatch):
    monkeypatch.setattr(
        orch_module, "build_snapshot",
        lambda exchange, symbol, **kwargs: symbol_state(ema50="95"),
    )
    repo, llm = FakeRepo(), FakeLLM(llm_signal("BUY"))

    report = await orchestrator(tmp_path, repo, llm).run_once()

    assert repo.current_state == IDLE
    assert llm.calls == 0
    assert repo.signals == []
    assert any("pas de setup" in action for action in report.actions)


# ---------------------------------------------------------------------------
# Reprise automatique après un arrêt du modèle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_halt_sets_a_24h_auto_resume(tmp_path):
    repo = FakeRepo()
    await orchestrator(tmp_path, repo, FakeLLM(llm_signal("HALT", "0.90"))).run_once()

    assert repo.forced_halt is not None
    assert repo.forced_halt_auto_resume_at == NOW + timedelta(hours=24)


@pytest.mark.asyncio
async def test_llm_halt_resumes_automatically_after_the_deadline(tmp_path):
    repo = FakeRepo(state="EMERGENCY_HALT")
    repo.halt_detail = "LLM HALT : BTC/EUR soldé"
    repo.cooldown = NOW - timedelta(minutes=1)

    report = await orchestrator(
        tmp_path, repo, FakeLLM(llm_signal("HOLD", "0.1"))
    ).run_once()

    assert repo.current_state == IDLE
    assert repo.resumes
    assert any("reprise automatique" in action for action in report.actions)


@pytest.mark.asyncio
async def test_human_halt_is_not_resumed_automatically(tmp_path):
    repo = FakeRepo(state="EMERGENCY_HALT")
    repo.halt_detail = "panic administrateur"
    repo.cooldown = NOW - timedelta(minutes=1)

    report = await orchestrator(
        tmp_path, repo, FakeLLM(llm_signal("HOLD", "0.1"))
    ).run_once()

    assert repo.current_state == "EMERGENCY_HALT"
    assert repo.resumes == []
    assert report.state_after == "EMERGENCY_HALT"
