#!/usr/bin/env python3
"""Collecte publique paginée, sans clé ni écriture SQL, pour réparer un trou H1."""

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import gzip
import hashlib
import json
from pathlib import Path
import sys
import time

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.market.trade_history import TradeCandles


def collect(symbol, start, end, directory, *, max_pages=1000):
    pair = {"BTC/EUR": "XBTEUR", "ETH/EUR": "ETHEUR", "SOL/EUR": "SOLEUR"}.get(symbol)
    if pair is None or not start.tzinfo or not end.tzinfo:
        raise ValueError("paire et bornes UTC explicites requises")
    begin_ms, end_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
    accumulator = TradeCandles(begin_ms, end_ms)
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    cursor = begin_ms * 1_000_000
    pages = []
    last_call = 0
    with httpx.Client(timeout=20) as client:
        for index in range(max_pages):
            wait = 1.05 - (time.monotonic() - last_call)
            if wait > 0:
                time.sleep(wait)
            last_call = time.monotonic()
            response = client.get("https://api.kraken.com/0/public/Trades", params={"pair": pair, "since": str(cursor), "count": 1000})
            response.raise_for_status()
            body = json.loads(response.text, parse_float=Decimal)
            if body.get("error"):
                raise ValueError("refus de la source publique")
            result = body["result"]
            next_cursor = int(result["last"])
            rows = next(v for k, v in result.items() if k != "last")
            if not rows or next_cursor <= cursor:
                raise ValueError("pagination sans progression")
            accumulator.observe(rows)
            payload = gzip.compress(response.content, mtime=0)
            path = directory / f"page-{index:04d}.json.gz"
            path.write_bytes(payload)
            pages.append({"file": path.name, "sha256": hashlib.sha256(payload).hexdigest(),
                          "since": str(cursor), "next": str(next_cursor), "trades": len(rows)})
            cursor = next_cursor
            if index % 10 == 0:
                print(json.dumps({"symbol": symbol, "pages": index + 1,
                                  "covered_until": datetime.fromtimestamp(accumulator.covered_until_ms / 1000, timezone.utc).isoformat()}), flush=True)
            if accumulator.covered_until_ms >= end_ms:
                break
        else:
            raise ValueError("plafond de pages atteint : collecte incomplète")
    candles = accumulator.candles()
    data = {"symbol": symbol, "timeframe": "1h", "candles": candles}
    candle_file = directory / "candles.json"
    candle_file.write_text(json.dumps(data, default=str, separators=(',', ':')))
    manifest = {"schema_version": 1, "source": "Kraken public Trades", "symbol": symbol,
                "start_at": start.isoformat(), "end_at": end.isoformat(), "pages": pages,
                "candles_sha256": hashlib.sha256(candle_file.read_bytes()).hexdigest(),
                "observed_hours": len(candles), "expected_hours": (end_ms - begin_ms) // 3_600_000,
                "database_applied": False}
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2) + '\n')
    return {k: v for k, v in manifest.items() if k != "pages"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = collect(args.symbol, datetime.fromisoformat(args.start), datetime.fromisoformat(args.end), args.out)
        print(json.dumps(result))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "INCOMPLETE", "error_type": type(exc).__name__}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
