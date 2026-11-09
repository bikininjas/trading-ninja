"""Paquets typés et bornés ; aucune importation décidée par un paquet de rejeu."""

from dataclasses import fields, is_dataclass
from datetime import datetime, date
from decimal import Decimal
from enum import Enum
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
import uuid

SECRET = re.compile(
    r"password|secret|token|api.?key|authorization|database.?url|dsn|_raw_env", re.I
)
MAX_PACKET = 8 * 1024 * 1024


def encode(value):
    if is_dataclass(value):
        return {
            "@": "dataclass",
            "type": type(value).__module__ + "." + type(value).__name__,
            "fields": {
                f.name: ("REDACTED_PRESENT" if getattr(value, f.name) else "")
                if SECRET.search(f.name)
                else encode(getattr(value, f.name))
                for f in fields(value)
            },
        }
    if isinstance(value, Enum):
        return {
            "@": "enum",
            "type": type(value).__module__ + "." + type(value).__name__,
            "value": value.value,
        }
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("valeur non finie dans le paquet")
        return {"@": "decimal", "value": str(value)}
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("date sans fuseau")
        return {"@": "datetime", "value": value.isoformat()}
    if isinstance(value, date):
        return {"@": "date", "value": value.isoformat()}
    if isinstance(value, uuid.UUID):
        return {"@": "uuid", "value": str(value)}
    if isinstance(value, Path):
        return {"@": "path", "value": str(value)}
    if isinstance(value, tuple):
        return {"@": "tuple", "items": [encode(v) for v in value]}
    if isinstance(value, (set, frozenset)):
        return {"@": "set", "items": sorted((encode(v) for v in value), key=canonical)}
    if isinstance(value, dict):
        if "@" in value:
            return {
                "@": "mapping",
                "items": {str(k): encode(v) for k, v in value.items()},
            }
        return {
            str(k): ("REDACTED_PRESENT" if v else "")
            if SECRET.search(str(k))
            else encode(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [encode(v) for v in value]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        import math

        if not math.isfinite(value):
            raise ValueError("flottant non fini")
        return value
    raise ValueError("type non exportable : " + type(value).__name__)


@lru_cache(maxsize=1)
def registry():
    from app.core import settings
    from app.db import repository
    from app.engine import risk_gate, capital
    from app.exchange import kraken
    from app.execution import paper, live
    from app.llm import client
    from app.market import feed
    from app.runtime import orchestrator

    out = {}
    for module in (
        settings,
        repository,
        risk_gate,
        capital,
        kraken,
        paper,
        live,
        client,
        feed,
        orchestrator,
    ):
        for name, item in vars(module).items():
            if isinstance(item, type) and (
                is_dataclass(item) or issubclass(item, Enum)
            ):
                out[item.__module__ + "." + item.__name__] = item
    return out


def decode(value):
    if isinstance(value, list):
        return [decode(v) for v in value]
    if not isinstance(value, dict):
        return value
    kind = value.get("@")
    if kind == "mapping":
        return {k: decode(v) for k, v in value["items"].items()}
    if kind == "decimal":
        number = Decimal(value["value"])
        if not number.is_finite():
            raise ValueError("montant non fini")
        return number
    if kind == "datetime":
        at = datetime.fromisoformat(value["value"])
        if at.tzinfo is None:
            raise ValueError("date sans fuseau")
        return at
    if kind == "date":
        return date.fromisoformat(value["value"])
    if kind == "uuid":
        return uuid.UUID(value["value"])
    if kind == "path":
        return Path(value["value"])
    if kind in ("tuple", "set"):
        items = [decode(v) for v in value["items"]]
        return tuple(items) if kind == "tuple" else frozenset(items)
    if kind in ("dataclass", "enum"):
        cls = registry().get(value["type"])
        if cls is None:
            raise ValueError("type de paquet non autorisé")
        return (
            cls(value["value"])
            if kind == "enum"
            else cls(**{k: decode(v) for k, v in value["fields"].items()})
        )
    if kind is not None:
        raise ValueError("contrat de paquet inconnu")
    return {k: decode(v) for k, v in value.items()}


def canonical(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def bounded(value, max_bytes=MAX_PACKET):
    raw = canonical(value)
    if len(raw.encode()) > max_bytes:
        raise ValueError("paquet trop volumineux")
    return raw


@lru_cache(maxsize=1)
def code_fingerprint():
    root = Path(__file__).resolve().parents[1]
    return digest(
        {
            str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*.py"))
        }
    )


def public(value):
    """Projection JSON lisible, sans métadonnées de types ni secrets."""
    if is_dataclass(value):
        return public({f.name: getattr(value, f.name) for f in fields(value)})
    if isinstance(value, dict):
        return {
            str(k): ("***" if v else "") if SECRET.search(str(k)) else public(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [public(v) for v in value]
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (Decimal, uuid.UUID, Path)):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value
