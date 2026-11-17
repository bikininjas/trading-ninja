"""La CLI croise toute la grille et transmet la période jusqu'au moteur."""
import json
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from scripts import backtest
from app.core.settings import load_settings
from app.engine.risk_gate import ExchangeConstraints


@pytest.mark.asyncio
@pytest.mark.parametrize('timeframes,expected', [('4h',72),('1h,4h,1d',216)])
async def test_macro_cli_crosses_72_scenarios_and_exports_sources(tmp_path, monkeypatch,timeframes,expected):
    settings = load_settings(overrides={"TRADING_MODE": "paper"}, use_environment=False)
    monkeypatch.setattr(backtest, "load_settings", lambda: settings)
    monkeypatch.setattr(type(settings), "database", lambda self: NS(asyncpg_kwargs=lambda: {}))
    conn = NS(close=AsyncMock())
    import asyncpg
    monkeypatch.setattr(asyncpg, "connect", AsyncMock(return_value=conn))
    monkeypatch.setattr(backtest, "build_exchange", lambda **kw: NS(load_markets=lambda: None))
    info = NS(taker_fee=Decimal("0.008"), constraints=ExchangeConstraints(
        Decimal("0.001"), Decimal("0.001"), Decimal("0.45"), Decimal("0.01")))
    monkeypatch.setattr(backtest, "market_info", lambda *a, **kw: info)
    monkeypatch.setattr(backtest, "resolve_taker_fee", lambda *a: Decimal("0.008"))
    start = int(datetime(2024, 9, 11, tzinfo=timezone.utc).timestamp()*1000)
    async def candles(conn, symbol, timeframe, **kw):
        step = 14_400_000 if timeframe == "4h" else 3_600_000
        return [[start+i*step, 100, 101, 99, 100, 1] for i in range(800 if timeframe == "4h" else 3200)]
    monkeypatch.setattr(backtest, "load_candles", candles)
    seen = []
    async def run(**kw):
        risk = kw["settings"].risk
        seen.append((kw["symbol"], risk.macro_timeframe, risk.macro_ema_period, risk.entry_mode,
                     risk.entry_ema200_slope_min, kw["broker"].execution_fee_pct))
        assert kw["settings"].mode == "paper"
        assert risk.max_consecutive_losses == 999
        assert risk.max_drawdown_from_hwm_pct == 1
        return NS(trades=[], payoff_ratio=None, win_rate=Decimal(0), profit_factor=None,
                  expectancy_quote=Decimal(0), net_pnl=Decimal(0), return_pct=Decimal(0),
                  max_drawdown_pct=Decimal(0), rejections={}, to_dict=lambda: {"trades": []})
    monkeypatch.setattr(backtest, "run_backtest", run)
    path = tmp_path / "report.json"
    args = NS(equity="1000", symbols="BTC/EUR,ETH/EUR,SOL/EUR", slopes="0.0,1.0",
              macro_ema_periods="50,100,200", macro_timeframes=timeframes, fee_bps="80,25", maker_fees=None,
              entry_modes="EMA50_H4,PULLBACK_H1", max_consecutive_losses=999,
              max_hwm_drawdown="1.0", months=24, as_of="2026-11-10T12:00:00+00:00",
              json=False, output_json=str(path))
    assert await backtest.sweep(args) == 0
    assert len(seen) == len(set(seen)) == expected
    assert conn.close.await_count == 1
    report = json.loads(path.read_text())
    assert len(report["rows"]) == expected
    assert len(report["sources"]) == 3
    assert all(row["bull_trap_rate"] is None for row in report["rows"])
