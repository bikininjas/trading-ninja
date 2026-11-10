"""Jeu de marché figé : empreintes, Decimal, continuité explicite et aucun réseau."""

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path

STEPS = {"1h": 3_600_000, "4h": 14_400_000}


def file_digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_rows(rows, timeframe):
    previous = None
    for row in rows:
        if len(row) != 6 or type(row[0]) is not int or row[0] < 0 or row[0] % STEPS[timeframe]:
            raise ValueError("bougie de dataset invalide ou non alignée")
        if previous is not None and row[0] <= previous:
            raise ValueError("bougies désordonnées ou dupliquées")
        previous = row[0]
        o, h, l, c, v = (Decimal(str(x)) for x in row[1:])
        if any(not x.is_finite() for x in (o, h, l, c, v)) or min(o, h, l, c) <= 0 or v < 0 or l > min(o, c) or h < max(o, c):
            raise ValueError("OHLCV de dataset incohérent")


def coverage(rows, timeframe):
    validate_rows(rows, timeframe)
    gaps = [{"after_ms": a[0], "before_ms": b[0], "missing": (b[0] - a[0]) // STEPS[timeframe] - 1}
            for a, b in zip(rows, rows[1:]) if b[0] - a[0] != STEPS[timeframe]]
    return {"rows": len(rows), "first_ms": rows[0][0] if rows else None,
            "last_ms": rows[-1][0] if rows else None, "gaps": gaps,
            "missing": sum(g["missing"] for g in gaps)}


def build_manifest(directory, markets, *, source, consistency):
    directory = Path(directory)
    combined = defaultdict(list)
    files = []
    for path in sorted(directory.glob("part-*.json")):
        item = json.loads(path.read_text())
        key = (item["symbol"], item["timeframe"])
        if key[0] not in markets or key[1] not in STEPS:
            raise ValueError("série de dataset non autorisée")
        combined[key].extend(item["candles"])
        files.append({"file": path.name, "sha256": file_digest(path), "symbol": key[0],
                      "timeframe": key[1], "rows": len(item["candles"])})
    expected = {(symbol, tf) for symbol in markets for tf in STEPS}
    if set(combined) != expected:
        raise ValueError("dataset incomplet")
    measured = [{"symbol": s, "timeframe": tf, **coverage(rows, tf)} for (s, tf), rows in sorted(combined.items())]
    identity = {"files": files, "markets": markets, "coverage": measured}
    manifest = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
                "source": source, "consistency": consistency, **identity,
                "dataset_digest": hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()}
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


def load_dataset(directory, *, start_ms=None, end_ms=None):
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text())
    if manifest.get("schema_version") != 1:
        raise ValueError("version de dataset inconnue")
    identity = {key: manifest[key] for key in ("files", "markets", "coverage")}
    if hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest() != manifest["dataset_digest"]:
        raise ValueError("manifest de dataset modifié")
    data = {symbol: {"market": item, "candles": {tf: [] for tf in STEPS}}
            for symbol, item in manifest["markets"].items()}
    for part in manifest["files"]:
        name = part["file"]
        if Path(name).name != name:
            raise ValueError("chemin de dataset non local")
        path = directory / name
        if path.stat().st_size > 8 * 1024 * 1024 or file_digest(path) != part["sha256"]:
            raise ValueError("fichier de dataset modifié ou trop grand")
        item = json.loads(path.read_text())
        if item["symbol"] != part["symbol"] or item["timeframe"] != part["timeframe"] or len(item["candles"]) != part["rows"]:
            raise ValueError("contenu de partition divergent")
        rows = [r for r in item["candles"] if (start_ms is None or r[0] >= start_ms) and (end_ms is None or r[0] < end_ms)]
        data[item["symbol"]]["candles"][item["timeframe"]].extend([[r[0], *(Decimal(str(v)) for v in r[1:])] for r in rows])
    for item in data.values():
        for tf, rows in item["candles"].items():
            validate_rows(rows, tf)
    return data, manifest
