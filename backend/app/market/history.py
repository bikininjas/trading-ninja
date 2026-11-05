"""Historique de marché persistant — le carburant du backtest multi-années.

L'API REST de Kraken plafonne à 720 bougies : impossible de valider un filtre de
tendance H4 sur 25 jours. Ce module lit et écrit la table
`market_candles_history` (migration 0008), alimentée par les archives publiques
OHLCVT de Kraken.

Deux parties distinctes :

- **Pur** (`parse_ohlcvt`, `detect_gaps`, `aggregate`, `merge`) : texte → bougies,
  contrôle de continuité, agrégation H1 → H4. Aucune I/O, testable en microsecondes.
- **Base** (`store_candles`, `load_candles`, `coverage`) : asyncpg, idempotent.

Convention de bougie : la forme CCXT `[open_ms, open, high, low, close, volume]`
partout dans le dépôt, donc `open_ms` est l'ouverture et la clôture vaut
`open_ms + durée`.

Aucune interpolation n'est faite ici. Inventer une bougie manquante fabriquerait
un prix qui n'a jamais existé, susceptible de déclencher un stop simulé : un trou
est **signalé**, jamais comblé.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Sequence

__all__ = [
    "CandleGap",
    "Coverage",
    "HistoryError",
    "StorageVerdict",
    "TIMEFRAME_SECONDS",
    "aggregate",
    "coverage",
    "detect_gaps",
    "load_candles",
    "merge",
    "parse_ohlcvt",
    "reconcile_aggregation",
    "storage_state",
    "storage_verdict",
    "store_candles",
]

TIMEFRAME_SECONDS: dict[str, int] = {"1h": 3_600, "4h": 14_400, "1d": 86_400}


class HistoryError(RuntimeError):
    """Historique inexploitable : format, timeframe ou cohérence."""


@dataclass(frozen=True, slots=True)
class CandleGap:
    """Un trou entre deux bougies consécutives."""

    after: datetime          # clôture de la dernière bougie avant le trou
    before: datetime         # ouverture de la première bougie après le trou
    missing: int             # bougies absentes
    hours: float             # durée réelle du trou


@dataclass(frozen=True, slots=True)
class Coverage:
    """Ce que la base contient réellement pour une paire et un timeframe."""

    symbol: str
    timeframe: str
    candles: int
    first_open: datetime | None
    last_open: datetime | None
    expected: int
    missing: int
    missing_pct: Decimal
    span_days: float
    gaps: tuple[CandleGap, ...] = ()

    @property
    def gap_count(self) -> int:
        return len(self.gaps)


@dataclass(frozen=True, slots=True)
class StorageVerdict:
    """Décision d'écriture face au quota de la base.

    Le quota Supabase (500 Mo) est une ressource partagée avec la télémétrie du
    bot. La règle de Seb est un plancher de 100 Mo libres, en permanence : une
    ingestion qui le franchirait est refusée **avant** d'écrire, pas signalée
    après coup quand la base refuse les insertions du bot.
    """

    allowed: bool
    db_bytes: int
    projected_bytes: int
    quota_bytes: int
    free_after_bytes: int
    min_free_bytes: int
    reason: str

    @property
    def free_now_bytes(self) -> int:
        return max(0, self.quota_bytes - self.db_bytes)


# ---------------------------------------------------------------------------
# Partie pure
# ---------------------------------------------------------------------------

def _decimal(text: str) -> Decimal:
    try:
        return Decimal(text.strip())
    except (InvalidOperation, AttributeError) as exc:  # pragma: no cover - défensif
        raise HistoryError(f"nombre illisible : {text!r}") from exc


def parse_ohlcvt(text: str, timeframe: str) -> list[list]:
    """CSV Kraken (`ts,open,high,low,close,volume,trades`) → bougies CCXT.

    Les lignes vides et l'en-tête éventuel sont ignorés. Les doublons de timestamp
    sont écrasés par la dernière occurrence (les archives ne devraient pas en
    contenir ; un fichier concaténé à la main pourrait).
    """
    if timeframe not in TIMEFRAME_SECONDS:
        raise HistoryError(f"timeframe non géré : {timeframe}")
    by_ts: dict[int, list] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        head = line.split(",", 1)[0].strip().lower()
        # Seul un en-tête nommé est ignoré : avaler toute ligne commençant par
        # une lettre masquerait un fichier corrompu, et un backtest silencieux
        # sur des données tronquées est pire qu'une erreur.
        if head in {"timestamp", "time", "date", "unix", "epoch"}:
            continue
        parts = line.split(",")
        if len(parts) < 6:
            raise HistoryError(f"ligne OHLCVT incomplète : {raw[:80]!r}")
        try:
            stamp = int(Decimal(parts[0]))
        except (InvalidOperation, ValueError) as exc:
            raise HistoryError(f"timestamp illisible : {parts[0]!r}") from exc
        open_ms = stamp * 1000
        by_ts[open_ms] = [
            open_ms,
            _decimal(parts[1]),
            _decimal(parts[2]),
            _decimal(parts[3]),
            _decimal(parts[4]),
            _decimal(parts[5]),
        ]
    return [by_ts[key] for key in sorted(by_ts)]


def merge(*series: Sequence[Sequence[Any]]) -> list[list]:
    """Fusionne plusieurs sources par timestamp ; la dernière gagne."""
    by_ts: dict[int, list] = {}
    for candles in series:
        for candle in candles:
            by_ts[int(candle[0])] = list(candle[:6])
    return [by_ts[key] for key in sorted(by_ts)]


def detect_gaps(candles: Sequence[Sequence[Any]], timeframe: str) -> list[CandleGap]:
    """Trous entre bougies consécutives, en unités du timeframe.

    Kraken ne publie que les intervalles où un trade a eu lieu : un trou au cœur
    d'une séance est donc presque toujours une anomalie, mais un trou de plusieurs
    jours sur une petite paire peut être un marché réellement inactif. Le rôle de
    cette fonction est de rendre l'écart visible, pas de décider.
    """
    if timeframe not in TIMEFRAME_SECONDS:
        raise HistoryError(f"timeframe non géré : {timeframe}")
    step_ms = TIMEFRAME_SECONDS[timeframe] * 1000
    gaps: list[CandleGap] = []
    for previous, current in zip(candles, candles[1:]):
        delta = int(current[0]) - int(previous[0])
        if delta <= step_ms:
            continue
        missing = delta // step_ms - 1
        gaps.append(
            CandleGap(
                after=datetime.fromtimestamp((int(previous[0]) + step_ms) / 1000, tz=timezone.utc),
                before=datetime.fromtimestamp(int(current[0]) / 1000, tz=timezone.utc),
                missing=int(missing),
                hours=round(delta / 3_600_000, 3),
            )
        )
    return gaps


def aggregate(candles: Sequence[Sequence[Any]], source: str, target: str) -> list[list]:
    """Agrège H1 → H4 en n'acceptant que les fenêtres complètes.

    Sert au contrôle de cohérence : les archives Kraken fournissent directement
    le H4, et une agrégation du H1 doit le reproduire à l'identique. Une fenêtre
    incomplète (trou interne ou bord de fichier) est **ignorée** : agréger trois
    heures en une bougie de quatre produirait un plus haut faux.
    """
    if source not in TIMEFRAME_SECONDS or target not in TIMEFRAME_SECONDS:
        raise HistoryError("timeframe non géré")
    src_ms = TIMEFRAME_SECONDS[source] * 1000
    dst_ms = TIMEFRAME_SECONDS[target] * 1000
    if dst_ms % src_ms != 0:
        raise HistoryError(f"{source} ne divise pas {target}")
    per_bucket = dst_ms // src_ms

    buckets: dict[int, list] = {}
    for candle in candles:
        open_ms = int(candle[0])
        bucket = open_ms - (open_ms % dst_ms)
        buckets.setdefault(bucket, []).append(candle)

    out: list[list] = []
    for bucket in sorted(buckets):
        members = sorted(buckets[bucket], key=lambda c: int(c[0]))
        expected = [bucket + i * src_ms for i in range(per_bucket)]
        if [int(c[0]) for c in members] != expected:
            continue
        out.append([
            bucket,
            members[0][1],
            max(c[2] for c in members),
            min(c[3] for c in members),
            members[-1][4],
            sum((c[5] for c in members), Decimal(0)),
        ])
    return out


def reconcile_aggregation(
    h1: Sequence[Sequence[Any]], h4: Sequence[Sequence[Any]], *, max_examples: int = 5
) -> tuple[int, int, list[str]]:
    """Compare le H4 publié à l'agrégation du H1.
    Rend (bougies comparées, divergentes, exemples). Les prix doivent être
    identiques — une divergence signale une archive incohérente, donc un backtest
    faux. Le volume n'est pas comparé : Kraken peut arrondir chaque agrégat.
    """
    rebuilt = {int(c[0]): c for c in aggregate(h1, "1h", "4h")}
    compared = 0
    divergences: list[str] = []
    for candle in h4:
        candidate = rebuilt.get(int(candle[0]))
        if candidate is None:
            continue
        compared += 1
        for label, published, computed in (
            ("open", candle[1], candidate[1]),
            ("high", candle[2], candidate[2]),
            ("low", candle[3], candidate[3]),
            ("close", candle[4], candidate[4]),
        ):
            if published != computed:
                if len(divergences) < max_examples:
                    at = datetime.fromtimestamp(int(candle[0]) / 1000, tz=timezone.utc)
                    divergences.append(
                        f"{at:%Y-%m-%d %H:%M} {label} publié={published} agrégé={computed}"
                    )
                break
    return compared, len(divergences), divergences


def storage_verdict(
    *,
    db_bytes: int,
    row_bytes: int,
    rows_to_add: int,
    quota_bytes: int,
    min_free_bytes: int,
) -> StorageVerdict:
    """Faut-il écrire `rows_to_add` lignes sans passer sous le plancher libre ?

    `row_bytes` vient de la mesure réelle (`pg_total_relation_size` / lignes) ;
    à défaut, 220 octets est la valeur observée sur `market_candles_history`
    (NUMERIC(28,10) × 5 + horodatage + index).
    """
    projected = db_bytes + max(0, rows_to_add) * max(1, row_bytes)
    free_after = quota_bytes - projected
    if free_after < min_free_bytes:
        manque_mo = (min_free_bytes - free_after) / 1e6
        return StorageVerdict(
            allowed=False,
            db_bytes=db_bytes,
            projected_bytes=projected,
            quota_bytes=quota_bytes,
            free_after_bytes=free_after,
            min_free_bytes=min_free_bytes,
            reason=(
                f"écriture refusée : {projected/1e6:.0f} Mo projetés sur "
                f"{quota_bytes/1e6:.0f} Mo, il manquerait {manque_mo:.0f} Mo "
                f"sous le plancher de {min_free_bytes/1e6:.0f} Mo libres"
            ),
        )
    return StorageVerdict(
        allowed=True,
        db_bytes=db_bytes,
        projected_bytes=projected,
        quota_bytes=quota_bytes,
        free_after_bytes=free_after,
        min_free_bytes=min_free_bytes,
        reason=(
            f"OK : {projected/1e6:.0f} Mo projetés, "
            f"{free_after/1e6:.0f} Mo libres après écriture "
            f"(plancher {min_free_bytes/1e6:.0f} Mo)"
        ),
    )


# ---------------------------------------------------------------------------
# Partie base
# ---------------------------------------------------------------------------

INSERT_SQL = """
WITH rows AS (
    SELECT *
    FROM unnest(
        $2::bigint[],
        $3::numeric[],
        $4::numeric[],
        $5::numeric[],
        $6::numeric[],
        $7::numeric[],
        $8::bigint[]
    ) AS x(open_ms, o, h, l, c, v, n)
), inserted AS (
    INSERT INTO market_candles_history
        (symbol, timeframe, open_time, open_price, high_price, low_price,
         close_price, volume, trades, source)
    SELECT
        $1::text,
        $9::text,
        to_timestamp(open_ms / 1000.0),
        o, h, l, c, v, n,
        $10::text
    FROM rows
    ON CONFLICT (symbol, timeframe, open_time) DO NOTHING
    RETURNING 1
)
SELECT count(*) FROM inserted
"""


async def store_candles(
    conn: Any,
    symbol: str,
    timeframe: str,
    candles: Sequence[Sequence[Any]],
    source: str,
    *,
    chunk_size: int = 5_000,
) -> int:
    """Écrit les bougies ; rend le nombre réellement inséré (les doublons sont ignorés).

    L'historique est immuable : une bougie déjà en base n'est jamais réécrite.
    `ON CONFLICT DO NOTHING` rend l'ingestion rejouable sans doublon ni écrasement.
    L'écriture est découpée en lots : une seule requête de 113 000 lignes tiendrait
    la connexion (et le pooler Supabase) trop longtemps pour un bénéfice nul.
    """
    if not candles:
        return 0
    if timeframe not in TIMEFRAME_SECONDS:
        raise HistoryError(f"timeframe non géré : {timeframe}")
    inserted = 0
    for start in range(0, len(candles), chunk_size):
        batch = candles[start:start + chunk_size]
        stamps = [int(c[0]) for c in batch]
        opens = [Decimal(str(c[1])) for c in batch]
        highs = [Decimal(str(c[2])) for c in batch]
        lows = [Decimal(str(c[3])) for c in batch]
        closes = [Decimal(str(c[4])) for c in batch]
        volumes = [Decimal(str(c[5])) for c in batch]
        counts = [None] * len(batch)
        inserted += int(await conn.fetchval(
            INSERT_SQL, symbol, stamps, opens, highs, lows, closes, volumes, counts,
            timeframe, source,
        ) or 0)
    return inserted


async def load_candles(
    conn: Any,
    symbol: str,
    timeframe: str,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int | None = None,
) -> list[list]:
    """Bougies au format CCXT, de la plus ancienne à la plus récente."""
    if timeframe not in TIMEFRAME_SECONDS:
        raise HistoryError(f"timeframe non géré : {timeframe}")
    rows = await conn.fetch(
        """
        SELECT open_time, open_price, high_price, low_price, close_price, volume
        FROM market_candles_history
        WHERE symbol = $1 AND timeframe = $2
          AND ($3::timestamptz IS NULL OR open_time >= $3)
          AND ($4::timestamptz IS NULL OR open_time < $4)
        ORDER BY open_time
        """,
        symbol, timeframe, start, end,
    )
    candles = [
        [
            int(row["open_time"].timestamp() * 1000),
            row["open_price"],
            row["high_price"],
            row["low_price"],
            row["close_price"],
            row["volume"],
        ]
        for row in rows
    ]
    return candles[-limit:] if limit else candles


async def coverage(
    conn: Any,
    symbol: str,
    timeframe: str,
    *,
    max_gaps: int = 20,
) -> Coverage:
    """Couverture mesurée en base, trous détaillés inclus."""
    if timeframe not in TIMEFRAME_SECONDS:
        raise HistoryError(f"timeframe non géré : {timeframe}")
    candles = await load_candles(conn, symbol, timeframe)
    if not candles:
        return Coverage(
            symbol=symbol, timeframe=timeframe, candles=0,
            first_open=None, last_open=None, expected=0, missing=0,
            missing_pct=Decimal(0), span_days=0.0, gaps=(),
        )
    step_ms = TIMEFRAME_SECONDS[timeframe] * 1000
    first_ms, last_ms = int(candles[0][0]), int(candles[-1][0])
    expected = (last_ms - first_ms) // step_ms + 1
    missing = max(0, expected - len(candles))
    gaps = detect_gaps(candles, timeframe)
    return Coverage(
        symbol=symbol,
        timeframe=timeframe,
        candles=len(candles),
        first_open=datetime.fromtimestamp(first_ms / 1000, tz=timezone.utc),
        last_open=datetime.fromtimestamp(last_ms / 1000, tz=timezone.utc),
        expected=int(expected),
        missing=int(missing),
        missing_pct=(
            (Decimal(missing) / Decimal(expected) * 100).quantize(Decimal("0.001"))
            if expected else Decimal(0)
        ),
        span_days=round((last_ms - first_ms) / 86_400_000, 3),
        gaps=tuple(gaps[:max_gaps]),
    )


def iter_rows(candles: Iterable[Sequence[Any]]) -> Iterable[tuple]:
    """Itère (open_ms, o, h, l, c, v) — utile aux scripts, sans ouvrir la base."""
    for candle in candles:
        yield (int(candle[0]), *candle[1:6])


async def storage_state(conn: Any) -> tuple[int, int]:
    """(taille de la base, octets par ligne de `market_candles_history`).

    La mesure réelle remplace toute estimation théorique : NUMERIC(28,10) et
    l'index pèsent bien plus qu'un flottant, et c'est ce chiffre qui décide si
    une ingestion tient dans le quota.
    """
    db_bytes = int(await conn.fetchval("SELECT pg_database_size(current_database())"))
    rows = int(await conn.fetchval("SELECT count(*) FROM market_candles_history"))
    if rows == 0:
        return db_bytes, 220
    table_bytes = int(await conn.fetchval(
        "SELECT pg_total_relation_size('market_candles_history')"
    ))
    return db_bytes, max(1, table_bytes // rows)
