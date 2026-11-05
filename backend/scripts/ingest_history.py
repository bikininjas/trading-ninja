#!/usr/bin/env python3
"""Ingestion de l'historique OHLCVT multi-années de Kraken.

    cd backend
    .venv/bin/python scripts/ingest_history.py --months 36 --dry-run
    .venv/bin/python scripts/ingest_history.py --months 36
    .venv/bin/python scripts/ingest_history.py --rest-only      # complète la fin
    .venv/bin/python scripts/ingest_history.py --report          # lit la base

Pourquoi ce script existe : l'API REST publique plafonne à 720 bougies (30 jours
de H1, 120 jours de H4). Les archives OHLCVT trimestrielles de Kraken couvrent
chaque marché depuis sa première transaction. Une archive pèse ~575 Mo, mais ce
script n'en lit que les membres utiles par requêtes HTTP Range (voir
`app/market/kraken_archive.py`) : quelques centaines de kilo-octets par trimestre.

Ce que le script fait :

1. télécharge (par plage) les CSV `1h` et `4h` des paires demandées ;
2. contrôle la cohérence : une agrégation H1 → H4 doit reproduire le H4 publié ;
3. écrit dans `market_candles_history` de façon idempotente ;
4. mesure la couverture réelle (bougies, profondeur, trous) et l'affiche.

Aucun ordre n'est envoyé, aucun solde n'est lu, aucune clé privée n'est utilisée :
seules les archives publiques et la base sont touchées. Le mode `--dry-run`
n'écrit rien.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncpg  # noqa: E402

from app.core.env import load_env  # noqa: E402
from app.core.settings import load_settings  # noqa: E402
from app.market.history import (  # noqa: E402
    TIMEFRAME_SECONDS,
    Coverage,
    coverage,
    detect_gaps,
    parse_ohlcvt,
    reconcile_aggregation,
    storage_state,
    storage_verdict,
    store_candles,
)
from app.market.kraken_archive import (  # noqa: E402
    HttpRangeSource,
    KrakenArchive,
    KrakenArchiveError,
    MultiPartRangeSource,
)

ARCHIVE_URL = "https://assets.kraken.com/marketing/institutions/Kraken_OHLCVT_{year}Q{quarter}.zip"
FULL_ARCHIVE_PARTS = [
    f"https://assets.kraken.com/marketing/institutions/Kraken_OHLCVT_Full.zip.part{i:02d}"
    for i in range(5)
]
DEFAULT_PAIRS = ("BTC/EUR", "ETH/EUR", "SOL/EUR")
TIMEFRAME_MINUTES = {"1h": "60", "4h": "240"}
# Les archives Kraken utilisent les codes historiques : XBT pour Bitcoin.
LEGACY_BASE = {"BTC": "XBT"}

# Plancher de stockage : Seb garde toujours 100 Mo libres sur les 500 Mo du plan.
QUOTA_MB = int(load_env().get("SUPABASE_DB_QUOTA_MB", "500"))
MIN_FREE_MB = int(load_env().get("SUPABASE_DB_MIN_FREE_MB", "100"))


class BudgetExceeded(RuntimeError):
    """L'écriture ferait passer la base sous le plancher de 100 Mo libres."""


async def store_within_budget(conn, symbol: str, timeframe: str,
                              candles: list[list], source: str) -> int:
    """Écrit après avoir vérifié le quota ; refuse au lieu de remplir la base."""
    if conn is None:
        return 0
    db_bytes, row_bytes = await storage_state(conn)
    verdict = storage_verdict(
        db_bytes=db_bytes,
        row_bytes=row_bytes,
        rows_to_add=len(candles),
        quota_bytes=QUOTA_MB * 1_000_000,
        min_free_bytes=MIN_FREE_MB * 1_000_000,
    )
    if not verdict.allowed:
        raise BudgetExceeded(verdict.reason)
    return await store_candles(conn, symbol, timeframe, candles, source)


# ---------------------------------------------------------------------------
# Calendrier des archives
# ---------------------------------------------------------------------------

def archive_code(symbol: str) -> str:
    base, _, quote = symbol.partition("/")
    return f"{LEGACY_BASE.get(base, base)}{quote}"


def quarter_index(year: int, quarter: int) -> int:
    return year * 4 + (quarter - 1)


def quarters_since(months: int, today: date | None = None) -> list[tuple[int, int]]:
    """Les trimestres **complets** couvrant au moins `months` mois avant aujourd'hui.

    Le trimestre en cours n'est pas publié : la fin de série est complétée par
    `--rest-only`.
    """
    moment = today or datetime.now(timezone.utc).date()
    first_month = moment.year * 12 + (moment.month - 1) - months
    start_year, start_month = divmod(first_month, 12)
    first = quarter_index(start_year, start_month // 3 + 1)
    last = quarter_index(moment.year, (moment.month - 1) // 3 + 1) - 1
    return [(index // 4, index % 4 + 1) for index in range(first, last + 1)]


def member_name(symbol: str, timeframe: str) -> str:
    return f"{archive_code(symbol)}_{TIMEFRAME_MINUTES[timeframe]}.csv"


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------

async def ingest_archives(conn, pairs: list[str], timeframes: list[str], months: int,
                          dry_run: bool) -> tuple[int, int]:
    """Importe les ZIP trimestriels. Rend (échecs, trimestres indisponibles)."""
    periods = quarters_since(months)
    print(f"Archives : {len(periods)} trimestres ({periods[0][0]}Q{periods[0][1]} → "
          f"{periods[-1][0]}Q{periods[-1][1]}) · paires {', '.join(pairs)}")
    failures = 0
    unavailable = 0
    for year, quarter in periods:
        url = ARCHIVE_URL.format(year=year, quarter=quarter)
        label = f"{year}Q{quarter}"
        source = HttpRangeSource(url, timeout=90.0)
        try:
            archive = KrakenArchive(source)
            available = archive.members()
        except Exception as exc:  # noqa: BLE001 - archive absente ou réseau
            print(f"  {label}  INDISPONIBLE  {exc}")
            unavailable += 1
            source.close()
            continue

        h1_by_pair: dict[str, list[list]] = {}
        for symbol in pairs:
            for timeframe in timeframes:
                name = member_name(symbol, timeframe)
                if name not in available:
                    print(f"  {label}  {symbol:8s} {timeframe}  MEMBRE ABSENT ({name})")
                    failures += 1
                    continue
                try:
                    text = archive.read_text(name)
                except KrakenArchiveError as exc:
                    print(f"  {label}  {symbol:8s} {timeframe}  LECTURE ÉCHOUÉE  {exc}")
                    failures += 1
                    continue
                candles = parse_ohlcvt(text, timeframe)
                if timeframe == "1h":
                    h1_by_pair[symbol] = candles
                inserted = 0
                if not dry_run:
                    inserted = await store_within_budget(
                        conn, symbol, timeframe, candles, "kraken-archive"
                    )
                gaps = detect_gaps(candles, timeframe)
                gap_note = f" · {len(gaps)} trou(s)" if gaps else ""
                wrote = f"+{inserted}" if not dry_run else "dry-run"
                print(
                    f"  {label}  {symbol:8s} {timeframe}  {len(candles):6d} bougies  "
                    f"{wrote:>8s}{gap_note}"
                )

        # Contrôle H1 → H4 sur la même archive.
        if "1h" in timeframes and "4h" in timeframes:
            for symbol in pairs:
                if symbol not in h1_by_pair:
                    continue
                name = member_name(symbol, "4h")
                if name not in available:
                    continue
                published = parse_ohlcvt(archive.read_text(name), "4h")
                compared, divergent, examples = reconcile_aggregation(h1_by_pair[symbol], published)
                if divergent:
                    print(
                        f"  {label}  {symbol:8s} COHÉRENCE H1→H4 : {divergent}/{compared} "
                        f"bougies divergentes"
                    )
                    for example in examples:
                        print(f"      {example}")
                    failures += 1
        source.close()
    return failures, unavailable


async def ingest_full_archive(conn, pairs: list[str], timeframes: list[str],
                              dry_run: bool) -> int:
    """Importe l'archive complète (5 parties concaténées) par lecture Range.

    Les ZIP trimestriels ne remontent que sur les derniers trimestres publiés :
    Kraken ne conserve pas les incréments au-delà. L'archive complète couvre
    chaque paire depuis sa première transaction, et ne coûte ici que les octets
    des six CSV utiles (le répertoire central est lu dans la dernière partie).
    """
    print("Archive complète : 5 parties concaténées, lecture par plages")
    source = MultiPartRangeSource(FULL_ARCHIVE_PARTS)
    archive = KrakenArchive(source)
    failures = 0
    try:
        available = archive.members()
        h1_by_pair: dict[str, list[list]] = {}
        for symbol in pairs:
            for timeframe in timeframes:
                name = member_name(symbol, timeframe)
                if name not in available:
                    print(f"  COMPLET  {symbol:8s} {timeframe}  MEMBRE ABSENT ({name})")
                    failures += 1
                    continue
                candles = parse_ohlcvt(archive.read_text(name), timeframe)
                if timeframe == "1h":
                    h1_by_pair[symbol] = candles
                inserted = 0
                if not dry_run:
                    inserted = await store_within_budget(
                        conn, symbol, timeframe, candles, "kraken-archive"
                    )
                first = datetime.fromtimestamp(int(candles[0][0]) / 1000, tz=timezone.utc)
                last = datetime.fromtimestamp(int(candles[-1][0]) / 1000, tz=timezone.utc)
                gaps = detect_gaps(candles, timeframe)
                wrote = f"+{inserted}" if not dry_run else "dry-run"
                print(
                    f"  COMPLET  {symbol:8s} {timeframe}  {len(candles):6d} bougies  "
                    f"{first:%Y-%m-%d} → {last:%Y-%m-%d}  {wrote:>8s}  {len(gaps)} trou(s)"
                )
        if "1h" in timeframes and "4h" in timeframes:
            for symbol in pairs:
                if symbol not in h1_by_pair:
                    continue
                name = member_name(symbol, "4h")
                if name not in available:
                    continue
                published = parse_ohlcvt(archive.read_text(name), "4h")
                compared, divergent, examples = reconcile_aggregation(h1_by_pair[symbol], published)
                status = "OK" if divergent == 0 else f"{divergent}/{compared} DIVERGENTES"
                print(f"  COMPLET  {symbol:8s} cohérence H1→H4 : {status}")
                if divergent:
                    failures += 1
                    for example in examples:
                        print(f"      {example}")
    finally:
        source.close()
    return failures


async def ingest_rest(conn, pairs: list[str], timeframes: list[str], dry_run: bool) -> int:
    """Complète la fin de série avec les 720 dernières bougies de l'API REST."""
    from app.exchange.kraken import build_exchange
    from app.market.feed import closed_candles

    exchange = build_exchange(api_key=None, secret=None)
    exchange.load_markets()
    failures = 0
    try:
        for symbol in pairs:
            for timeframe in timeframes:
                try:
                    candles = closed_candles(exchange, symbol, timeframe, 720)
                except Exception as exc:  # noqa: BLE001 - réseau
                    print(f"  REST  {symbol:8s} {timeframe}  ÉCHEC  {exc}")
                    failures += 1
                    continue
                inserted = 0
                if not dry_run:
                    inserted = await store_within_budget(
                        conn, symbol, timeframe, candles, "kraken-rest"
                    )
                wrote = f"+{inserted}" if not dry_run else "dry-run"
                print(f"  REST  {symbol:8s} {timeframe}  {len(candles):6d} bougies  {wrote:>8s}")
    finally:
        close = getattr(exchange, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001 - fermeture best-effort
                pass
    return failures


# ---------------------------------------------------------------------------
# Rapport
# ---------------------------------------------------------------------------

def print_coverage(rows: list[Coverage]) -> None:
    if not rows:
        print("Aucune bougie en base.")
        return
    header = (
        f"{'paire':10s} {'tf':3s} {'bougies':>8s} {'première':>17s} {'dernière':>17s} "
        f"{'jours':>8s} {'manquantes':>11s} {'%':>7s} {'trous':>6s}"
    )
    print(header)
    print("-" * len(header))
    for row in rows:
        first = f"{row.first_open:%Y-%m-%d %H:%M}" if row.first_open else "—"
        last = f"{row.last_open:%Y-%m-%d %H:%M}" if row.last_open else "—"
        print(
            f"{row.symbol:10s} {row.timeframe:3s} {row.candles:8d} {first:>17s} "
            f"{last:>17s} {row.span_days:8.1f} {row.missing:11d} "
            f"{row.missing_pct:7.3f} {row.gap_count:6d}"
        )
    print()
    for row in rows:
        if not row.gaps:
            continue
        print(f"Trous — {row.symbol} {row.timeframe} (les 20 premiers) :")
        for gap in row.gaps:
            print(
                f"  {gap.after:%Y-%m-%d %H:%M} → {gap.before:%Y-%m-%d %H:%M}  "
                f"{gap.missing} bougie(s) · {gap.hours:.1f} h"
            )


async def run(args: argparse.Namespace) -> int:
    settings = load_settings()
    pairs = [p.strip().upper() for p in args.pairs.split(",") if p.strip()]
    timeframes = [t.strip() for t in args.timeframes.split(",") if t.strip()]
    for timeframe in timeframes:
        if timeframe not in TIMEFRAME_SECONDS:
            print(f"timeframe non géré : {timeframe}", file=sys.stderr)
            return 2

    # En dry-run, la base n'est jamais ouverte : le script ne fait que lire le réseau.
    conn = None if args.dry_run else await asyncpg.connect(
        **settings.database().asyncpg_kwargs()
    )

    failures = 0
    try:
        try:
            if not args.report:
                if args.source != "full":
                    quarterly_failures, unavailable = await ingest_archives(
                        conn, pairs, timeframes, args.months, args.dry_run
                    )
                    failures += quarterly_failures
                    if unavailable and args.source == "auto":
                        print(
                            f"\n{unavailable} trimestre(s) sans archive publiée : "
                            "bascule sur l'archive complète."
                        )
                        failures += await ingest_full_archive(
                            conn, pairs, timeframes, args.dry_run
                        )
                else:
                    failures += await ingest_full_archive(
                        conn, pairs, timeframes, args.dry_run
                    )
                if not args.no_rest:
                    failures += await ingest_rest(conn, pairs, timeframes, args.dry_run)
        except BudgetExceeded as exc:
            print(f"\nREFUS — {exc}", file=sys.stderr)
            print(
                f"Plancher : {MIN_FREE_MB} Mo libres sur {QUOTA_MB} Mo. "
                "Réduire --months, retirer une paire, ou purger d'anciennes bougies.",
                file=sys.stderr,
            )
            return 3

        if args.dry_run:
            print("\nMode dry-run : aucune écriture, aucun rapport de couverture.")
            return 0 if failures == 0 else 1

        rows: list[Coverage] = []
        for symbol in pairs:
            for timeframe in timeframes:
                rows.append(await coverage(conn, symbol, timeframe))
        print("\nCouverture en base :")
        print_coverage(rows)

        db_bytes, row_bytes = await storage_state(conn)
        free_mo = max(0, QUOTA_MB * 1_000_000 - db_bytes) / 1e6
        flag = "OK" if free_mo >= MIN_FREE_MB else "SOUS LE PLANCHER"
        print(
            f"\nStockage : base {db_bytes/1e6:.1f} Mo / {QUOTA_MB} Mo · "
            f"libre {free_mo:.1f} Mo (plancher {MIN_FREE_MB} Mo) · "
            f"~{row_bytes} o/ligne d'historique — {flag}"
        )

        depth_ok = True
        for row in rows:
            if row.span_days / 30.44 < args.months * 0.95:
                depth_ok = False
                print(
                    f"  ATTENTION {row.symbol} {row.timeframe} : "
                    f"{row.span_days / 30.44:.1f} mois seulement",
                    file=sys.stderr,
                )
        if args.strict and (failures or not depth_ok):
            return 1
        return 0
    finally:
        if conn is not None:
            await conn.close()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--months", type=int, default=36,
                        help="profondeur visée en mois (défaut 36)")
    parser.add_argument("--pairs", default=",".join(DEFAULT_PAIRS),
                        help=f"paires CCXT séparées par virgule (défaut {','.join(DEFAULT_PAIRS)})")
    parser.add_argument("--timeframes", default="1h,4h",
                        help="timeframes à importer (1h,4h)")
    parser.add_argument("--source", choices=("auto", "quarterly", "full"), default="auto",
                        help="auto = ZIP trimestriels puis archive complète si un trimestre manque")
    parser.add_argument("--dry-run", action="store_true",
                        help="télécharge et contrôle, n'écrit rien")
    parser.add_argument("--report", action="store_true",
                        help="lit la base et affiche la couverture, sans réseau")
    parser.add_argument("--no-rest", action="store_true",
                        help="ne complète pas la fin de série via l'API REST")
    parser.add_argument("--rest-only", action="store_true",
                        help="uniquement les 720 dernières bougies (API REST)")
    parser.add_argument("--strict", action="store_true",
                        help="code de sortie 1 si une archive échoue ou si la profondeur manque")
    args = parser.parse_args(argv)
    if args.rest_only:
        args.report = False
        args.no_rest = False
    if args.months <= 0:
        parser.error("--months doit être positif")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.rest_only:
        return asyncio.run(_rest_only(args))
    return asyncio.run(run(args))


async def _rest_only(args: argparse.Namespace) -> int:
    settings = load_settings()
    conn = await asyncpg.connect(**settings.database().asyncpg_kwargs())
    pairs = [p.strip().upper() for p in args.pairs.split(",") if p.strip()]
    timeframes = [t.strip() for t in args.timeframes.split(",") if t.strip()]
    try:
        try:
            failures = await ingest_rest(conn, pairs, timeframes, args.dry_run)
        except BudgetExceeded as exc:
            print(f"REFUS — {exc}", file=sys.stderr)
            return 3
        rows = [await coverage(conn, symbol, tf) for symbol in pairs for tf in timeframes]
        print()
        print_coverage(rows)
        return 0 if failures == 0 else 1
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
