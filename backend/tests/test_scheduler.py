"""Tests de la cadence : prochaine clôture de bougie, alignée sur l'époque Unix."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.runtime.scheduler import next_candle_close, seconds_until_next_close

UTC = timezone.utc


def at(hour: int, minute: int = 0, second: int = 0, day: int = 4) -> datetime:
    return datetime(2026, 11, day, hour, minute, second, tzinfo=UTC)


def test_next_h4_close_from_mid_candle():
    assert next_candle_close(at(11, 30), "4h") == at(12)


def test_exactly_on_the_close_returns_the_following_one():
    # Appelée à 12:00:00 pile, la fonction ne doit pas rendre 12:00 : la bougie
    # qui vient de fermer a déjà été (ou va être) traitée par le cycle en cours.
    assert next_candle_close(at(12), "4h") == at(16)


def test_next_h4_close_crosses_midnight():
    assert next_candle_close(at(23, 59), "4h") == at(0, day=5)


def test_next_h1_close():
    assert next_candle_close(at(11, 30), "1h") == at(12)
    assert next_candle_close(at(11, 0, 1), "1h") == at(12)


def test_next_daily_close():
    assert next_candle_close(at(11, 30), "1d") == at(0, day=5)


def test_a_paris_timestamp_is_converted_to_utc():
    paris = timezone(timedelta(hours=1))
    moment = datetime(2026, 11, 4, 13, 30, tzinfo=paris)  # 12:30 UTC
    assert next_candle_close(moment, "4h") == at(16)


def test_seconds_until_next_close_is_positive():
    assert seconds_until_next_close(at(11, 30), "4h") == 1800.0


def test_naive_datetime_is_refused():
    with pytest.raises(ValueError, match="fuseau"):
        next_candle_close(datetime(2026, 11, 4, 11, 30), "4h")


def test_unknown_timeframe_is_refused():
    with pytest.raises(ValueError, match="timeframe"):
        next_candle_close(at(11, 30), "2h")
