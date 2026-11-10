from decimal import Decimal as D

import pytest

from app.market.trade_history import TradeCandles


def trade(price, volume, seconds, identity):
    return [price, volume, seconds, "b", "m", "", identity]


def test_exact_decimal_volume_duplicate_page_and_numeric_tie_break():
    history = TradeCandles(0, 3_600_000)
    first = trade("100", "0.1", "0.1", 2)
    history.observe([trade("105", "0.2", "0.1", 10), first])
    history.observe([first, trade("999", "10", "3600", 11)])
    assert history.candles() == [[0, D(100), D(105), D(100), D(105), D("0.3"), 2]]


def test_empty_hour_is_not_fabricated_and_end_trade_is_excluded():
    history = TradeCandles(0, 7_200_000)
    history.observe([trade("100", "1", "1", 1), trade("200", "1", "7200", 2)])
    assert len(history.candles()) == 1
    assert history.candles()[0][0] == 0


def test_unfinished_pagination_cannot_produce_accepted_candles():
    history = TradeCandles(0, 3_600_000)
    history.observe([trade("100", "1", "3599.9999999", 1)])
    with pytest.raises(ValueError, match="fin non couverte"):
        history.candles()


def test_same_id_with_different_trade_is_rejected():
    history = TradeCandles(0, 3_600_000)
    history.observe([trade("100", "1", "1", 1)])
    with pytest.raises(ValueError, match="divergente"):
        history.observe([trade("101", "1", "1", 1)])


@pytest.mark.parametrize("row", [trade("NaN", "1", "1", 1), trade("100", "-1", "1", 1),
                                   trade("100", "1", "Infinity", 1), trade("100", "1", "1", -1)])
def test_invalid_public_trade_is_not_turned_into_a_candle(row):
    with pytest.raises(ValueError):
        TradeCandles(0, 3_600_000).observe([row])
