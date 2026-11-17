"""RSS publics bornés et Fear & Greed. Aucun texte externe n'est une consigne."""
import asyncio
import hashlib
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree
from urllib.parse import urlsplit
import httpx

RSS_SOURCES = ("https://www.coindesk.com/arc/outboundfeeds/rss", "https://cointelegraph.com/rss")
FNG_URL = "https://api.alternative.me/fng/?limit=1"
MAX_BYTES = 1_000_000


def article_url(value):
    """Liens éditoriaux HTTPS uniquement ; aucun lien fourni n'est exécuté."""
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 32 for c in value):
        return None
    try:
        parsed = urlsplit(value)
        if (parsed.scheme == "https" and parsed.hostname in
                {"coindesk.com", "www.coindesk.com", "cointelegraph.com", "www.cointelegraph.com"}
                and not parsed.username and not parsed.password and parsed.port in (None, 443)):
            return value
    except ValueError:
        pass
    return None


def parse_rss(body: bytes, *, now: datetime, source: str = "") -> list[dict]:
    decoded = body.decode("utf-8-sig")
    if len(body) > MAX_BYTES or re.search(r"<!\s*(?:DOCTYPE|ENTITY)", decoded, re.I):
        raise ValueError("XML trop volumineux ou déclaration interdite")
    root = ElementTree.fromstring(decoded)
    items = []
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] not in {"item", "entry"}:
            continue
        fields = {child.tag.rsplit("}", 1)[-1]: "".join(child.itertext()).strip() for child in node}
        title = fields.get("title", "")[:500]
        raw_date = fields.get("pubDate") or fields.get("published") or fields.get("updated")
        try:
            try:
                at = parsedate_to_datetime(raw_date)
            except (ValueError, TypeError):
                at = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
            if at.tzinfo is None or not now - timedelta(hours=12) <= at <= now:
                continue
        except (AttributeError, ValueError, TypeError):
            continue
        if not title:
            continue
        title = re.sub(r"<[^>]*>", " ", title)
        description = re.sub(r"<[^>]*>", " ", fields.get("description", fields.get("summary", "")))[:1000]
        key = hashlib.sha256(" ".join(title.casefold().split()).encode()).hexdigest()
        link = fields.get("link")
        for child in node:
            if child.tag.rsplit("}", 1)[-1] == "link" and child.get("rel", "alternate") == "alternate":
                link = child.get("href") or link
        items.append({"title": title, "description": description, "published_at": at.astimezone(timezone.utc).isoformat(),
                      "sha256": key, "source": source, "url": article_url(link)})
    items.sort(key=lambda item: item["published_at"], reverse=True)
    return items[:80]


def parse_fng(data, *, now):
    row = data["data"][0]
    value = int(row["value"])
    at = datetime.fromtimestamp(int(row["timestamp"]), timezone.utc)
    if not 0 <= value <= 100 or not now - timedelta(hours=48) <= at <= now:
        raise ValueError("Fear & Greed invalide ou ancien")
    return {"value": value, "at": at.isoformat()}


async def _read(client, url):
    async with client.stream("GET", url) as response:
        response.raise_for_status()
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > MAX_BYTES:
                raise ValueError("source trop volumineuse")
        return bytes(body)


async def collect(*, now, transport=None, timeout=5.0):
    import json
    async with httpx.AsyncClient(timeout=timeout, transport=transport,
                                 headers={"User-Agent": "TradingNinja-MarketIntel/1.0"}) as client:
        async def source(url):
            try:
                async with asyncio.timeout(timeout):
                    body = await _read(client, url)
                return parse_fng(json.loads(body), now=now) if url == FNG_URL else parse_rss(body, now=now, source=url)
            except (httpx.HTTPError, TimeoutError, ValueError, OverflowError, OSError, KeyError, IndexError, TypeError, ElementTree.ParseError):
                return None
        results = await asyncio.gather(*(source(url) for url in (*RSS_SOURCES, FNG_URL)))
    unique = {}
    for batch in results[:2]:
        for item in batch or []:
            previous = unique.get(item["sha256"])
            if previous is None or item["published_at"] > previous["published_at"]:
                unique[item["sha256"]] = item
    articles = sorted(unique.values(), key=lambda item: item["published_at"], reverse=True)[:80]
    return {"articles": articles, "fear_and_greed": results[2],
            "complete": all(item is not None for item in results)}
