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
from app.engine.machine import COOLDOWN, IDLE, IN_POSITION
from app.engine.risk_gate import ExchangeConstraints
from app.exchange.kraken import KrakenMarketInfo
from app.execution.paper import PaperBroker
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
    ema_50_h4=Decimal("95"), rsi_14_h4=Decimal("55"), macd_histogram=Decimal("0.5"),
    order_book_imbalance=Decimal("0.1"), candles_closed=320,
)


def symbol_state(price: str = "100", ema: str = "90") -> SymbolState:
    from app.engine.risk_gate import MarketSnapshot

    snapshot = MarketSnapshot(
        symbol="BTC/EUR", price=Decimal(price), ema_200_h4=Decimal(ema),
        atr_14_h4=Decimal("1"), last_closed_h1_return_atr=Decimal("0"),
        tick_at=NOW, now=NOW,
    )
    return SymbolState(
        symbol="BTC/EUR", info=INFO, snapshot=snapshot, context=CONTEXT, last_close_at=NOW
    )


class FakeRepo:
    def __init__(self, state: str = IDLE, position: PositionRow | None = None,
                 free_quote: str = "9.37"):
        self.current_state = state
        self.position = position
        self.cooldown: datetime | None = None
        self.transitions: list[tuple[str, str]] = []
        self.signals: list[dict] = []
        self.evaluations: list[dict] = []
        self.orders: list[dict] = []
        self.snapshots: list[dict] = []
        self.closed: dict | None = None
        self.after_close: dict | None = None
        self.sweeps: list[dict] = []
        self.deferred = Decimal(0)
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
        return uuid.uuid4(), NOW

    async def insert_evaluation(self, mode, symbol, **kwargs):
        self.evaluations.append({"symbol": symbol, **kwargs})
        return uuid.uuid4(), NOW

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


class FakeLLM:
    def __init__(self, signal: LLMSignal):
        self.signal = signal
        self.calls = 0

    async def propose(self, symbol, snapshot, context, **kwargs):
        self.calls += 1
        return self.signal


def llm_signal(action: str = "BUY", confidence: str = "0.9", *, degraded: bool = False,
               reason: str | None = None) -> LLMSignal:
    return LLMSignal(
        action=action, confidence=Decimal(confidence),
        market_regime="TRENDING_UP", directional_bias="BULLISH", thesis="test",
        raw={}, prompt_payload={"rsi_14": "55.00"}, prompt_sha256="x" * 64,
        inference_ms=12, degraded=degraded, degradation_reason=reason,
    )


def settings(tmp_path: Path):
    return load_settings(
        env_file=tmp_path / "absent.env",
        overrides={
            "TRADING_MODE": "paper", "EXCHANGE_SANDBOX_MODE": "true",
            "EXCHANGE_ID": "kraken", "TRADING_SYMBOL": "BTC/EUR",
            "TRADING_SYMBOLS": "BTC/EUR", "RISK_PROFILE": "micro",
            "PAPER_START_EQUITY": "9.37", "LLM_MODEL_NAME": "test-model",
        },
    )


def orchestrator(tmp_path: Path, repo: FakeRepo, llm: FakeLLM) -> Orchestrator:
    return Orchestrator(
        settings(tmp_path), repo, exchange=object(), signal_client=llm,
        broker=PaperBroker(taker_fee=Decimal("0.0026"), slippage_bps=5),
        now=lambda: NOW,
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
    report = await orchestrator(tmp_path, repo, FakeLLM(llm_signal("BUY"))).run_once()

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
