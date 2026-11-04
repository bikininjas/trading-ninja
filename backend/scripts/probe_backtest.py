"""Sonde : combien de décisions LLM un backtest coûterait-il sur BTC/EUR ?

Aucun appel au modèle. On récupère l'historique réel Kraken, on rejoue les indicateurs
avec les fonctions du projet, et on compte les bougies où un achat est seulement
*possible* selon les garde-fous (prix au-dessus de l'EMA200 + buffer, RSI < 70,
volatilité dans les bornes).
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from decimal import Decimal

sys.path.insert(0, ".")

from app.exchange.kraken import build_exchange
from app.market.feed import TIMEFRAME_MS, atr, ema, rsi

SYMBOL = "BTC/EUR"
DAYS = 220
BUFFER = Decimal("0.005")
MIN_ATR = Decimal("0.0015")
MAX_ATR = Decimal("0.08")
RSI_MAX = Decimal("70")


def fetch_all(exchange, timeframe: str, days: int) -> list[list]:
    duration = TIMEFRAME_MS[timeframe]
    since = exchange.milliseconds() - days * 86_400_000
    seen: dict[int, list] = {}
    while True:
        batch = exchange.fetch_ohlcv(SYMBOL, timeframe, since=since, limit=720)
        if not batch:
            break
        for candle in batch:
            seen[candle[0]] = candle
        last = batch[-1][0]
        if last <= since:
            break
        since = last + duration
        if since >= exchange.milliseconds():
            break
    now = exchange.milliseconds()
    candles = [seen[k] for k in sorted(seen) if k + duration <= now]
    return candles


def main() -> None:
    exchange = build_exchange()
    exchange.load_markets()
    h4 = fetch_all(exchange, "4h", DAYS)
    print(f"{SYMBOL} : {len(h4)} bougies H4 closes "
          f"({datetime.fromtimestamp(h4[0][0] / 1000, timezone.utc):%Y-%m-%d} → "
          f"{datetime.fromtimestamp(h4[-1][0] / 1000, timezone.utc):%Y-%m-%d})")

    closes = [c[4] for c in h4]
    eligible = []
    above = 0
    for i in range(200, len(h4)):
        window = h4[:i + 1]
        price = Decimal(str(closes[i]))
        ema200 = ema(closes[:i + 1], 200)
        atr14 = atr(window)
        rsi14 = rsi(window)
        atr_pct = atr14 / price
        if price > ema200 * (1 + BUFFER):
            above += 1
        if (
            price > ema200 * (1 + BUFFER)
            and rsi14 < RSI_MAX
            and MIN_ATR <= atr_pct <= MAX_ATR
        ):
            eligible.append((i, price, ema200, rsi14, atr_pct))

    usable = len(h4) - 200
    print(f"bougies évaluables (après EMA200) : {usable}")
    print(f"au-dessus de l'EMA200+buffer       : {above} ({above / usable:.1%})")
    print(f"zone d'achat possible              : {len(eligible)} ({len(eligible) / usable:.1%})")
    if eligible:
        print("premières dates :",
              ", ".join(
                  datetime.fromtimestamp(h4[i][0] / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M")
                  for i, *_ in eligible[:5]
              ))
    print(f"\nappels LLM (zone seule) : {len(eligible)} × ~1,2 s ≈ "
          f"{len(eligible) * 1.2 / 60:.1f} min")
    print(f"appels LLM (toutes bougies) : {usable} × ~1,2 s ≈ {usable * 1.2 / 60:.1f} min")


if __name__ == "__main__":
    main()
