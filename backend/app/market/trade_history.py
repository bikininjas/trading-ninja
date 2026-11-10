"""Reconstruit des bougies seulement à partir de transactions publiques prouvées."""

from decimal import Decimal, ROUND_FLOOR


class TradeCandles:
    def __init__(self, start_ms, end_ms):
        if start_ms % 3_600_000 or end_ms % 3_600_000 or start_ms >= end_ms:
            raise ValueError("fenêtre H1 UTC alignée requise")
        self.start_ms, self.end_ms = start_ms, end_ms
        self.seen = {}
        self.hours = {}
        self.covered_until_ms = None

    def observe(self, rows):
        for row in rows:
            if len(row) < 7:
                raise ValueError("identifiant de transaction requis")
            price, volume, seconds = (Decimal(str(v)) for v in row[:3])
            if any(not v.is_finite() for v in (price, volume, seconds)) or min(price, volume) <= 0:
                raise ValueError("transaction invalide")
            stamp = int((seconds * 1000).to_integral_value(rounding=ROUND_FLOOR))
            self.covered_until_ms = max(self.covered_until_ms or stamp, stamp)
            identity = int(str(row[6]))
            if identity < 0:
                raise ValueError("identifiant de transaction invalide")
            value = (price, volume, seconds)
            if identity in self.seen:
                if self.seen[identity] != value:
                    raise ValueError("transaction dupliquée divergente")
                continue
            self.seen[identity] = value
            if not self.start_ms <= stamp < self.end_ms:
                continue
            hour = stamp // 3_600_000 * 3_600_000
            bar = self.hours.setdefault(hour, {"open": (seconds, identity, price),
                                              "close": (seconds, identity, price),
                                              "high": price, "low": price,
                                              "volume": Decimal(0), "trades": 0})
            point = (seconds, identity, price)
            if point[:2] < bar["open"][:2]:
                bar["open"] = point
            if point[:2] > bar["close"][:2]:
                bar["close"] = point
            bar["high"], bar["low"] = max(bar["high"], price), min(bar["low"], price)
            bar["volume"] += volume
            bar["trades"] += 1

    def candles(self):
        if self.covered_until_ms is None or self.covered_until_ms < self.end_ms:
            raise ValueError("borne de fin non couverte par la pagination")
        # Une heure sans transaction reste absente : aucun prix n'est inventé.
        return [[hour, bar["open"][2], bar["high"], bar["low"], bar["close"][2],
                 bar["volume"], bar["trades"]] for hour, bar in sorted(self.hours.items())]
