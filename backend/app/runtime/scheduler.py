"""Cadence d'évaluation : une décision par bougie clôturée, pas par minute.

Le moteur papier peut être lancé par un cron toutes les cinq minutes ou rester en
veille. Dans les deux cas, la seule cadence qui a un sens est la clôture de la
bougie de décision : à l'intérieur d'une bougie H4, les indicateurs ne changent
pas, donc redemander une décision produit des doublons et fausse les
statistiques.

Les bougies H4 sont alignées sur l'époque Unix (00:00, 04:00, 08:00… UTC), comme
chez l'exchange. `next_candle_close` calcule la **prochaine** clôture strictement
postérieure à `now` : appelée à 12:00:00, elle rend 16:00, jamais 12:00, pour
qu'une boucle ne réévalue pas la bougie qui vient d'être traitée.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.market.feed import TIMEFRAME_MS

__all__ = ["EPOCH", "next_candle_close", "seconds_until_next_close"]

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def next_candle_close(now: datetime, timeframe: str) -> datetime:
    """Prochaine clôture de bougie strictement après `now`, en UTC."""
    if timeframe not in TIMEFRAME_MS:
        raise ValueError(f"timeframe non géré : {timeframe}")
    if now.tzinfo is None:
        raise ValueError("now doit porter un fuseau horaire")
    duration = timedelta(milliseconds=TIMEFRAME_MS[timeframe])
    elapsed = now.astimezone(timezone.utc) - EPOCH
    steps = elapsed // duration + 1
    return EPOCH + steps * duration


def seconds_until_next_close(now: datetime, timeframe: str) -> float:
    """Secondes à attendre avant la prochaine clôture (toujours > 0)."""
    return (next_candle_close(now, timeframe) - now).total_seconds()
