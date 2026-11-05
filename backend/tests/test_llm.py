"""Tests du client LLM : parsing strict, repli, garde-fous déterministes."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest

from app.core.settings import LLMSettings
from app.engine.risk_gate import MarketSnapshot
from app.llm.client import (
    D1_SYSTEM_PROMPT, LLMError, SYSTEM_PROMPT, SignalClient, parse_signal,
)
from app.market.feed import D1Context, MarketContext

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)

SNAPSHOT = MarketSnapshot(
    symbol="BTC/EUR", price=Decimal("100"), ema_200_h4=Decimal("90"),
    atr_14_h4=Decimal("1"), last_closed_h1_return_atr=Decimal("0"),
    tick_at=NOW, now=NOW,
)
CONTEXT = MarketContext(
    ema_50_h4=Decimal("95"), rsi_14_h4=Decimal("55"), macd_histogram=Decimal("0.5"),
    order_book_imbalance=Decimal("0.1"), candles_closed=320,
)
VALID = (
    '{"action":"BUY","confidence":0.82,"market_regime":"TRENDING_UP",'
    '"directional_bias":"BULLISH","thesis":"prix au-dessus de l EMA200, MACD positif"}'
)


def settings(**kwargs) -> LLMSettings:
    base = dict(model="test-model", timeout_seconds=1.0, max_retries=0)
    base.update(kwargs)
    return LLMSettings(**base)


def client(content: str = VALID, status: int = 200) -> SignalClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={
            "message": {"content": content},
            "eval_count": 42,
            "total_duration": 123_456_789,
        })

    return SignalClient(settings(), transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_valid_response_is_parsed():
    parsed = parse_signal(VALID)
    assert parsed.action == "BUY"
    assert parsed.confidence == Decimal("0.82")
    assert parsed.market_regime == "TRENDING_UP"
    assert parsed.directional_bias == "BULLISH"


def test_halt_response_is_parsed():
    parsed = parse_signal(
        '{"action":"HALT","confidence":0.91,"market_regime":"TRENDING_DOWN",'
        '"directional_bias":"BEARISH","thesis":"risque de krach"}'
    )
    assert parsed.action == "HALT"
    assert parsed.confidence == Decimal("0.91")


def test_schema_allows_the_halt_action():
    from app.llm.client import SIGNAL_SCHEMA

    assert "HALT" in SIGNAL_SCHEMA["properties"]["action"]["enum"]


def test_json_wrapped_in_prose_is_recovered():
    parsed = parse_signal("Voici ma réponse :\n```json\n" + VALID + "\n```")
    assert parsed.action == "BUY"


@pytest.mark.parametrize("payload", [
    '{"action":"YOLO","confidence":0.5,"market_regime":"RANGING","directional_bias":"NEUTRAL"}',
    '{"action":"BUY","confidence":1.5,"market_regime":"RANGING","directional_bias":"NEUTRAL"}',
    '{"action":"BUY","confidence":0.5,"market_regime":"MOON","directional_bias":"NEUTRAL"}',
    '{"action":"BUY","confidence":0.5,"market_regime":"RANGING","directional_bias":"UP"}',
    '{"action":"BUY","market_regime":"RANGING","directional_bias":"NEUTRAL"}',
])
def test_invalid_responses_are_refused(payload):
    with pytest.raises(LLMError):
        parse_signal(payload)


def test_numeric_fields_from_the_model_are_ignored():
    payload = ('{"action":"BUY","confidence":0.8,"market_regime":"TRENDING_UP",'
               '"directional_bias":"BULLISH","thesis":"ok","amount":999,"stop":1,'
               '"leverage":50}')
    parsed = parse_signal(payload)
    assert parsed.ignored_fields == ("amount", "leverage", "stop")


def test_thesis_is_truncated_to_200_characters():
    payload = ('{"action":"HOLD","confidence":0.1,"market_regime":"RANGING",'
               '"directional_bias":"NEUTRAL","thesis":"' + "x" * 500 + '"}')
    assert len(parse_signal(payload).thesis) == 200


# ---------------------------------------------------------------------------
# Appel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_propose_returns_a_signal_and_hides_account_data():
    client_ = client()
    signal = await client_.propose("BTC/EUR", SNAPSHOT, CONTEXT)
    assert signal.action == "BUY"
    assert signal.confidence == Decimal("0.82")
    assert signal.degraded is False
    payload = signal.prompt_payload
    assert "balance" not in payload
    assert "api_key" not in str(payload).lower()
    assert payload["rsi_14"] == "55.00"


@pytest.mark.asyncio
async def test_timeout_falls_back_to_hold_with_zero_confidence():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("trop lent")

    client_ = SignalClient(settings(), transport=httpx.MockTransport(handler))
    signal = await client_.propose("BTC/EUR", SNAPSHOT, CONTEXT)
    assert signal.action == "HOLD"
    assert signal.confidence == 0
    assert signal.degraded is True
    assert "REPLI" in signal.thesis


@pytest.mark.asyncio
async def test_http_error_falls_back_to_hold():
    signal = await client(status=500).propose("BTC/EUR", SNAPSHOT, CONTEXT)
    assert signal.action == "HOLD"
    assert signal.degraded is True


@pytest.mark.asyncio
async def test_garbage_response_falls_back_to_hold():
    signal = await client("je pense qu'il faut acheter").propose("BTC/EUR", SNAPSHOT, CONTEXT)
    assert signal.action == "HOLD"
    assert signal.degraded is True


# ---------------------------------------------------------------------------
# Garde-fous déterministes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_buy_below_ema200_is_downgraded_even_if_the_model_insists():
    low = MarketSnapshot(
        symbol="BTC/EUR", price=Decimal("80"), ema_200_h4=Decimal("90"),
        atr_14_h4=Decimal("1"), last_closed_h1_return_atr=Decimal("0"),
        tick_at=NOW, now=NOW,
    )
    signal = await client().propose("BTC/EUR", low, CONTEXT)
    assert signal.action == "HOLD"
    assert signal.confidence == 0
    assert "EMA200" in signal.thesis


@pytest.mark.asyncio
async def test_buy_with_rsi_above_70_is_downgraded():
    hot = MarketContext(
        ema_50_h4=Decimal("95"), rsi_14_h4=Decimal("74"), macd_histogram=Decimal("0.5"),
        order_book_imbalance=Decimal("0.1"), candles_closed=320,
    )
    signal = await client().propose("BTC/EUR", SNAPSHOT, hot)
    assert signal.action == "HOLD"
    assert "RSI" in signal.thesis


@pytest.mark.asyncio
async def test_sell_and_hold_are_not_downgraded():
    hold = VALID.replace('"BUY"', '"HOLD"')
    signal = await client(hold).propose("BTC/EUR", SNAPSHOT, CONTEXT)
    assert signal.action == "HOLD"
    assert signal.degraded is False


# ---------------------------------------------------------------------------
# Prompt Daily (D-061)
# ---------------------------------------------------------------------------

D1 = D1Context(
    ema_20=Decimal("105"), ema_50=Decimal("103"), ema_200=Decimal("98"),
    ema_20_prev=Decimal("104"), ema_50_slope_5d_pct=Decimal("0.01"),
    rsi_14=Decimal("58"), atr_14=Decimal("2"),
    donchian_high_20=Decimal("110"), bollinger_upper=Decimal("112"),
    bollinger_mid=Decimal("105"), swing_low_10=Decimal("99"),
    last_open=Decimal("104"), last_close=Decimal("106"),
    last_low=Decimal("103"), last_high=Decimal("107"),
    prev_close=Decimal("104"), prev_low=Decimal("102"),
    bearish_divergence=False, highest_high_chandelier=Decimal("112"),
)
D1_CONTEXT = MarketContext(
    ema_50_h4=Decimal("95"), rsi_14_h4=Decimal("55"),
    macd_histogram=Decimal("0.5"), order_book_imbalance=Decimal("0.1"),
    candles_closed=320, d1=D1,
)


def capturing_client(content: str = VALID) -> tuple[SignalClient, dict]:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": content}})

    return SignalClient(settings(), transport=httpx.MockTransport(handler)), captured


@pytest.mark.asyncio
async def test_daily_uses_the_d1_prompt_and_indicator_payload():
    client_, captured = capturing_client()
    signal = await client_.propose(
        "BTC/EUR", SNAPSHOT, D1_CONTEXT, decision_timeframe="1d"
    )

    assert captured["messages"][0]["content"] == D1_SYSTEM_PROMPT
    payload = signal.prompt_payload
    assert payload["decision_timeframe"] == "1d"
    assert payload["prompt_version"] == "v3-d1"
    assert payload["ema_200"] == "98"
    assert payload["donchian_high_20"] == "110"
    assert payload["breakout_donchian"] is False
    assert payload["highest_high_chandelier"] == "112"
    assert payload["value_rebound"] is False
    # Aucun indicateur H4 ne doit fuiter dans le prompt Daily.
    assert "macd_histogram" not in payload
    assert "ema_50_h4" not in payload


@pytest.mark.asyncio
async def test_h4_keeps_its_historical_prompt_and_version():
    client_, captured = capturing_client()
    signal = await client_.propose("BTC/EUR", SNAPSHOT, CONTEXT)

    assert captured["messages"][0]["content"] == SYSTEM_PROMPT
    assert signal.prompt_payload["decision_timeframe"] == "4h"
    assert signal.prompt_payload["prompt_version"] == "v3"


@pytest.mark.asyncio
async def test_an_unknown_decision_timeframe_is_refused():
    with pytest.raises(ValueError, match="decision_timeframe"):
        await client().propose(
            "BTC/EUR", SNAPSHOT, CONTEXT, decision_timeframe="1w"
        )


@pytest.mark.asyncio
async def test_daily_without_context_falls_back_to_hold():
    signal = await client().propose(
        "BTC/EUR", SNAPSHOT, CONTEXT, decision_timeframe="1d"
    )
    assert signal.action == "HOLD"
    assert signal.confidence == 0
    assert signal.degraded is True
    assert "contexte D1 absent" in signal.thesis


@pytest.mark.asyncio
async def test_daily_buy_below_ema200_is_downgraded():
    low = MarketSnapshot(
        symbol="BTC/EUR", price=Decimal("80"), ema_200_h4=Decimal("90"),
        atr_14_h4=Decimal("1"), last_closed_h1_return_atr=Decimal("0"),
        tick_at=NOW, now=NOW,
    )
    signal = await client().propose(
        "BTC/EUR", low, D1_CONTEXT, decision_timeframe="1d"
    )
    assert signal.action == "HOLD"
    assert signal.confidence == 0
    assert "EMA200" in signal.thesis
