"""Contrats des commandes d'évaluation et d'aperçu, sans réseau ni écriture SQL."""

from decimal import Decimal
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import asyncpg
import pytest

from app.core.settings import load_settings
from scripts import eval_llm_swing, kill_switch


@pytest.mark.asyncio
@pytest.mark.parametrize("fee_bps", [25, 80])
async def test_llm_evaluation_uses_requested_fee(monkeypatch, fee_bps):
    settings = load_settings(overrides={"TRADING_MODE": "paper"}, use_environment=False)
    monkeypatch.setattr(eval_llm_swing, "load_settings", lambda: settings)
    monkeypatch.setattr(type(settings), "database", lambda self: NS(asyncpg_kwargs=lambda: {}))
    conn = NS(close=AsyncMock())
    monkeypatch.setattr(asyncpg, "connect", AsyncMock(return_value=conn))
    monkeypatch.setattr(eval_llm_swing, "build_exchange", lambda **kw: NS(load_markets=lambda: None))
    monkeypatch.setattr(eval_llm_swing, "load_candles", AsyncMock(return_value=[[0]]))
    monkeypatch.setattr(eval_llm_swing, "aggregate", lambda *args: [[0]] * (eval_llm_swing.D1_WARMUP + 30))
    monkeypatch.setattr(eval_llm_swing, "market_info", lambda *args, **kw: NS(taker_fee=kw["taker_fee"]))
    fees = []
    monkeypatch.setattr(eval_llm_swing, "run_swing_backtest", lambda **kw: fees.append(kw["info"].taker_fee) or NS(trades=[]))
    args = NS(symbols="BTC/EUR", triggers="both", exit_mode="ema20", fee_bps=fee_bps,
              months=0, limit=0, out=None, json=False)
    await eval_llm_swing.evaluate(args)
    assert fees == [Decimal(fee_bps) / Decimal(10000)]
    conn.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_kill_switch_dry_run_lists_all_positions(monkeypatch, capsys):
    settings = NS(mode="paper", execution_mode="PAPER", database=lambda: object())
    positions = [NS(symbol=symbol, amount=Decimal("1"), entry_price=Decimal("100"),
                    stop_loss_price=Decimal("90")) for symbol in ("BTC/EUR", "ETH/EUR")]
    repo = NS(state=AsyncMock(return_value=("IN_POSITION", None)),
              open_positions=AsyncMock(return_value=positions), open_orders=AsyncMock(return_value=[]),
              close=AsyncMock())
    monkeypatch.setattr(kill_switch, "load_settings", lambda: settings)
    monkeypatch.setattr(kill_switch.PostgresRepository, "connect", AsyncMock(return_value=repo))
    assert await kill_switch._run(True, False) == 0
    output = capsys.readouterr().out
    assert "positions ouvertes : 2" in output
    assert "BTC/EUR" in output and "ETH/EUR" in output
    repo.close.assert_awaited_once()
