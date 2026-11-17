"""Tests du flux de marché et des indicateurs, sur bougies synthétiques."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.market.feed import (
    ATR_PERIOD, EMA_PERIOD, FeedError, atr, build_d1_context, build_snapshot,
    closed_candles, ema, macd_histogram, rsi,
)

H4 = 14_400_000


def candles(n: int, start: float = 100.0, step: float = 0.1, spread: float = 0.5,
            t0: int = 1_700_000_000_000) -> list[list]:
    out = []
    for i in range(n):
        close = start + i * step
        out.append([t0 + i * H4, close - 0.05, close + spread, close - spread, close, 10.0])
    return out


class FakeExchange:
    def __init__(self, h4: list[list], h1: list[list], now_ms: int,
                 book: dict | None = None, d1: list[list] | None = None):
        self._h4, self._h1, self._now = h4, h1, now_ms
        self._d1 = d1 or []
        self._book = book or {"bids": [[100.0, 2.0]], "asks": [[100.1, 1.0]]}

    def fetch_ohlcv(self, symbol, timeframe, limit=None):
        if timeframe == "1d":
            return list(self._d1)
        return list(self._h4 if timeframe == "4h" else self._h1)

    def milliseconds(self):
        return self._now

    def fetch_order_book(self, symbol, limit=20):
        return self._book


def test_ema_rises_in_an_uptrend():
    values = [Decimal(str(100 + i)) for i in range(300)]
    value = ema(values, EMA_PERIOD)
    assert Decimal(100) < value < Decimal(399)


def test_ema_refuses_short_history():
    with pytest.raises(FeedError):
        ema([Decimal(1)] * 10, 200)


def test_atr_matches_a_constant_range():
    # Chaque bougie a high-low = 1, les clôtures montent de 0,1 : TR = 1.
    value = atr(candles(40), ATR_PERIOD)
    assert value == Decimal("1")


def test_rsi_is_maximal_on_a_pure_uptrend():
    assert rsi(candles(60)) == Decimal("100")


def test_rsi_is_minimal_on_a_pure_downtrend():
    falling = candles(60, step=-0.1)
    assert rsi(falling) == Decimal("0")


def test_macd_is_positive_in_an_uptrend():
    assert macd_histogram(candles(120)) > 0


def test_closed_candles_drop_the_unfinished_one():
    series = candles(10)
    last_ts = series[-1][0]
    exchange = FakeExchange(series, series, now_ms=last_ts + H4 + 1)
    assert len(closed_candles(exchange, "BTC/EUR", "4h", 10)) == 10

    exchange = FakeExchange(series, series, now_ms=last_ts + 1)
    assert len(closed_candles(exchange, "BTC/EUR", "4h", 10)) == 9


def test_network_failure_is_reported_as_unavailable_market_data():
    import ccxt

    class Unavailable:
        def fetch_ohlcv(self, *args, **kwargs):
            raise ccxt.NetworkError("connection reset")

    with pytest.raises(FeedError, match="BTC/EUR.*4h.*NetworkError"):
        closed_candles(Unavailable(), "BTC/EUR", "4h", 10)


def test_recorded_network_error_remains_a_network_error_during_replay():
    import ccxt
    from app.insights.replay import ReplayCursor, encode

    packet = {"trace": [{"target": "exchange", "method": "fetch_ohlcv",
                         "args": {"value": encode(("BTC/EUR", "4h"))},
                         "kwargs": {"value": encode({"limit": 10})},
                         "error": {"type": "NetworkError", "message": "connection reset"}}]}
    class Recorded:
        def fetch_ohlcv(self, *args, **kwargs):
            return ReplayCursor(packet).call("exchange", "fetch_ohlcv", args, kwargs)

    with pytest.raises(FeedError, match="NetworkError") as error:
        closed_candles(Recorded(), "BTC/EUR", "4h", 10)
    assert isinstance(error.value.__cause__, ccxt.NetworkError)


def test_snapshot_with_d1_exposes_the_structural_swing_low():
    t0 = 1_700_000_000_000
    d1 = []
    for i in range(320):
        close = 100.0 + i * 0.1
        d1.append([t0 + i * 86_400_000, close - 0.05, close + 0.5,
                   close - 0.5, close, 10.0])
    h4 = candles(120, t0=t0)
    exchange = FakeExchange(
        h4, h4, now_ms=d1[-1][0] + 86_400_000 + 1, d1=d1
    )

    class Info:
        constraints = None
        quote = "EUR"

    state = build_snapshot(
        exchange, "BTC/EUR", decision_timeframe="1d", entry_timeframe="4h",
        info=Info(), with_order_book=False, with_d1=True, macro_timeframe="1d",
    )
    assert state.context.d1 is not None
    assert state.snapshot.swing_low_10d == state.context.d1.swing_low_10
    assert state.snapshot.swing_low_10d > 0
    assert state.snapshot.price == state.context.d1.last_close


def test_d1_context_exposes_the_chandelier_highest_high():
    t0 = 1_700_000_000_000
    d1 = []
    for i in range(320):
        close = 100.0 + i * 0.1
        d1.append([t0 + i * 86_400_000, close - 0.05, close + 0.5,
                   close - 0.5, close, 10.0])
    # Un sommet isolé dans les 20 dernières bougies doit être retenu même si la
    # clôture courante est plus basse : c'est tout l'intérêt du Chandelier.
    d1[-3][2] = Decimal("150")

    context = build_d1_context(d1)
    assert context.highest_high_chandelier == Decimal("150")

    narrow = build_d1_context(d1, chandelier_period=2)
    assert narrow.highest_high_chandelier == Decimal(str(max(d1[-2][2], d1[-1][2])))


def test_d1_context_refuses_an_absurd_chandelier_period():
    d1 = [
        [1_700_000_000_000 + i * 86_400_000, 100, 101, 99, 100, 10]
        for i in range(320)
    ]
    with pytest.raises(FeedError, match="Chandelier"):
        build_d1_context(d1, chandelier_period=1)


def test_build_snapshot_uses_only_closed_candles():
    h4 = candles(320)
    h1 = candles(120, t0=1_700_000_000_000, step=0.01, spread=0.2)
    now = h4[-1][0] + H4 + 1
    exchange = FakeExchange(h4, h1, now_ms=now)

    class Info:
        constraints = None
        quote = "EUR"

    state = build_snapshot(exchange, "BTC/EUR", info=Info(), now=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert state.snapshot.price == Decimal(str(h4[-1][4]))
    assert state.snapshot.ema_200_h4 > 0
    assert state.snapshot.atr_14_h4 > 0
    assert state.snapshot.ema_200_slope_atr > 0
    assert state.context.rsi_14_h4 == Decimal("100")
    assert state.context.order_book_imbalance.quantize(Decimal("0.0001")) == Decimal("0.3333")


def test_build_snapshot_refuses_insufficient_history():
    h4 = candles(50)
    h1 = candles(120)
    exchange = FakeExchange(h4, h1, now_ms=h4[-1][0] + H4 + 1)

    class Info:
        pass

    with pytest.raises(FeedError, match="EMA200"):
        build_snapshot(exchange, "BTC/EUR", info=Info())


def test_snapshot_macro_indicators_use_only_closed_candles():
    h4 = candles(321)
    now_ms = h4[-1][0]  # dernière bougie encore ouverte
    exchange = FakeExchange(h4, h4, now_ms)
    class Info:
        constraints = None
        quote = "EUR"
    state = build_snapshot(exchange, "BTC/EUR", info=Info(), with_order_book=False)
    closes = [c[4] for c in h4[:-1]]
    for period in (50, 100, 200):
        value, slope = state.snapshot.macro_ema(period)
        assert value == ema(closes, period)
        assert slope == (value - ema(closes[:-60], period)) / state.snapshot.atr_14_h4

@pytest.mark.parametrize('macro_tf,eval_tf,period', [('4h','4h',200),('1h','4h',200),('4h','4h',50),('1d','1d',200),('1h','1h',75)])
def test_independent_macro_and_evaluation_series_are_deduplicated(macro_tf, eval_tf, period):
    from app.market.feed import TIMEFRAME_MS
    from types import SimpleNamespace
    now = 1_800_057_600_000  # frontière D1 UTC
    now -= now % TIMEFRAME_MS['1d']
    calls=[]
    class Exchange:
        def milliseconds(self): return now
        def fetch_ohlcv(self, symbol, tf, limit=None):
            calls.append((tf,limit))
            step=TIMEFRAME_MS[tf]
            offset={'1h':10,'4h':20,'1d':30}[tf]
            return [[now-(limit-i)*step, offset+i, offset+i+2, offset+i-1, offset+i+1, 1] for i in range(limit)]
    state=build_snapshot(Exchange(),'BTC/EUR',info=SimpleNamespace(), with_order_book=False,
                         decision_timeframe=eval_tf,macro_timeframe=macro_tf,macro_ema_period=period,
                         trend_buffer_pct=Decimal('.01'))
    assert len(calls)==len({tf for tf,_ in calls})
    assert {tf for tf,_ in calls}=={macro_tf,eval_tf,'1h'}
    assert state.snapshot.macro_timeframe==macro_tf and state.snapshot.eval_timeframe==eval_tf
    assert state.snapshot.macro_ema_period==period
    assert state.snapshot.macro_ema_value==state.snapshot.macro_ema(period)[0]
    assert state.snapshot.macro_ema_threshold_with_margin==state.snapshot.macro_ema_value*Decimal('1.01')
    assert int(state.last_close_at.timestamp()*1000)==now


def test_ohlcv_retries_two_network_failures_then_succeeds():
    import ccxt
    from types import SimpleNamespace
    calls=[]; sleeps=[]
    def fetch(*args, **kwargs):
        calls.append(1)
        if len(calls)<3: raise ccxt.NetworkError('reset')
        return [[0,1,2,1,2,1]]
    rows=closed_candles(SimpleNamespace(fetch_ohlcv=fetch),'BTC/EUR','1h',1,
                        now_ms=3_600_000,sleep=sleeps.append)
    assert len(rows)==1 and len(calls)==3 and sleeps==[1.0,2.0]


@pytest.mark.parametrize('bad', ['stale','future','gap','nan','negative'])
def test_sql_fallback_never_makes_bad_or_old_candles_fresh(bad):
    import ccxt
    from types import SimpleNamespace
    now=36_000_000; step=3_600_000
    rows=[[now-(3-i)*step,1,2,1,2,1] for i in range(3)]
    if bad=='stale': rows=rows[:-1]
    if bad=='future': rows[-1][0]+=step
    if bad=='gap': rows.pop(1)
    if bad=='nan': rows[-1][4]='NaN'
    if bad=='negative': rows[-1][3]=-1
    def fail(*a,**kw): raise ccxt.RequestTimeout('timeout')
    with pytest.raises(FeedError,match='cache SQL refusé'):
        closed_candles(SimpleNamespace(fetch_ohlcv=fail),'BTC/EUR','1h',3,now_ms=now,
                        cache_loader=lambda *args:rows,sleep=lambda _:None)


def test_fresh_sql_fallback_preserves_source_close_and_warns(caplog):
    import httpx
    from types import SimpleNamespace
    step=3_600_000; now=10*step+5000
    rows=[[7*step,1,2,1,2,1],[8*step,1,2,1,2,1],[9*step,1,2,1,2,1]]
    def fail(*a,**kw): raise httpx.ConnectError('offline')
    result=closed_candles(SimpleNamespace(fetch_ohlcv=fail),'BTC/EUR','1h',3,now_ms=now,
                          cache_loader=lambda *args: rows,sleep=lambda _:None)
    assert result==rows and result[-1][0]+step==10*step
    record=next(r for r in caplog.records if r.message=='market_candles_sql_fallback')
    assert record.symbol=='BTC/EUR' and record.timeframe=='1h' and record.age_ms==5000


def test_recorded_httpx_transport_error_replays_with_the_same_retry_contract():
    import httpx
    from app.insights.replay import ReplayCursor, encode
    packet={'trace':[{'target':'exchange','method':'fetch_ohlcv',
        'args':{'value':encode(('BTC/EUR','4h'))},'kwargs':{'value':encode({'limit':10})},
        'error':{'type':'ConnectError','message':'offline'}}]}
    class Exchange:
        def fetch_ohlcv(self,*args,**kwargs): return ReplayCursor(packet).call('exchange','fetch_ohlcv',args,kwargs)
    pauses=[]
    with pytest.raises(FeedError) as error:
        closed_candles(Exchange(),'BTC/EUR','4h',10,sleep=pauses.append)
    assert isinstance(error.value.__cause__,httpx.ConnectError) and pauses==[1,2]
