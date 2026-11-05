"""Historique OHLCVT : analyse pure du CSV Kraken, trous, agrégation, cohérence.

Aucun réseau, aucune base : ces tests couvrent les décisions qui déterminent si
une bougie entre en base et si un backtest lira un prix vrai.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.market.history import (
    CandleGap,
    HistoryError,
    aggregate,
    detect_gaps,
    merge,
    parse_ohlcvt,
    reconcile_aggregation,
    storage_verdict,
)

HOUR = 3_600_000
FOUR_HOURS = 14_400_000


def at(hour: int, minute: int = 0) -> int:
    """Timestamp ms d'un instant fixe (2024-01-01) pour des tests lisibles."""
    base = datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp()
    return int((base + hour * 3600 + minute * 60) * 1000)


def candle(open_ms: int, o: str = "100", h: str = "110", low: str = "90",
           c: str = "105", v: str = "1") -> list:
    return [open_ms, Decimal(o), Decimal(h), Decimal(low), Decimal(c), Decimal(v)]


# ---------------------------------------------------------------------------
# Lecture du CSV
# ---------------------------------------------------------------------------

def test_parse_ohlcvt_lit_le_format_kraken_et_trie():
    text = (
        "1704067200,100.5,101.0,99.5,100.8,12.5,7\n"
        "1704063600,99.0,100.0,98.0,100.5,10.0,5\n"
    )
    candles = parse_ohlcvt(text, "1h")

    assert [c[0] for c in candles] == [1704063600000, 1704067200000]
    assert candles[0][1] == Decimal("99.0")
    assert candles[0][5] == Decimal("10.0")
    assert all(isinstance(c[1], Decimal) for c in candles)


def test_parse_ohlcvt_ignore_entete_et_lignes_vides():
    text = "\n timestamp,open,high,low,close,volume,trades\n\n1704067200,1,2,0.5,1.5,3,4\n"
    candles = parse_ohlcvt(text, "1h")
    assert len(candles) == 1
    assert candles[0][4] == Decimal("1.5")


def test_parse_ohlcvt_refuse_une_ligne_incomplete():
    with pytest.raises(HistoryError, match="incomplète"):
        parse_ohlcvt("1704067200,1,2,0.5\n", "1h")


def test_parse_ohlcvt_refuse_un_timestamp_illisible():
    with pytest.raises(HistoryError, match="timestamp illisible"):
        parse_ohlcvt("pas-une-date,1,2,0.5,1.5,3,4\n", "1h")


def test_parse_ohlcvt_garde_la_derniere_occurrence_d_un_timestamp():
    text = (
        "1704067200,1,2,0.5,1.5,3,4\n"
        "1704067200,9,9,9,9,9,9\n"
    )
    candles = parse_ohlcvt(text, "1h")
    assert len(candles) == 1
    assert candles[0][1] == Decimal("9")


def test_parse_ohlcvt_refuse_un_timeframe_non_gere():
    with pytest.raises(HistoryError, match="timeframe"):
        parse_ohlcvt("1704067200,1,2,0.5,1.5,3,4\n", "5m")


# ---------------------------------------------------------------------------
# Trous
# ---------------------------------------------------------------------------

def test_detect_gaps_ne_signale_rien_sur_une_serie_continue():
    candles = [candle(at(h)) for h in range(5)]
    assert detect_gaps(candles, "1h") == []


def test_detect_gaps_mesure_un_trou_en_bougies_et_en_heures():
    candles = [candle(at(0)), candle(at(1)), candle(at(5))]
    gaps = detect_gaps(candles, "1h")

    assert gaps == [
        CandleGap(
            after=datetime(2024, 1, 1, 2, 0, tzinfo=timezone.utc),
            before=datetime(2024, 1, 1, 5, 0, tzinfo=timezone.utc),
            missing=3,
            hours=4.0,
        )
    ]


def test_detect_gaps_ignore_un_duplicata_ou_un_desordre():
    candles = [candle(at(0)), candle(at(0)), candle(at(1))]
    assert detect_gaps(candles, "1h") == []


def test_detect_gaps_travaille_aussi_en_h4():
    candles = [candle(at(0)), candle(at(12))]
    gaps = detect_gaps(candles, "4h")
    assert gaps[0].missing == 2
    assert gaps[0].hours == 12.0


# ---------------------------------------------------------------------------
# Agrégation H1 → H4
# ---------------------------------------------------------------------------

def test_aggregate_fusionne_quatre_heures_en_une_bougie():
    candles = [
        candle(at(0), "100", "101", "99", "100.5", "1"),
        candle(at(1), "100.5", "104", "100", "103", "2"),
        candle(at(2), "103", "103", "95", "96", "3"),
        candle(at(3), "96", "98", "95.5", "97", "4"),
    ]
    result = aggregate(candles, "1h", "4h")

    assert len(result) == 1
    assert result[0][0] == at(0)
    assert result[0][1] == Decimal("100")
    assert result[0][2] == Decimal("104")   # plus haut
    assert result[0][3] == Decimal("95")    # plus bas
    assert result[0][4] == Decimal("97")    # dernière clôture
    assert result[0][5] == Decimal("10")    # volume cumulé


def test_aggregate_ignore_une_fenetre_incomplete():
    """Trois heures sur quatre ne font pas une bougie : elle serait fausse."""
    candles = [candle(at(h), v="1") for h in range(3)]
    assert aggregate(candles, "1h", "4h") == []


def test_aggregate_refuse_une_conversion_impossible():
    with pytest.raises(HistoryError, match="divise"):
        aggregate([candle(at(0))], "4h", "1h")


# ---------------------------------------------------------------------------
# Fusion de sources et cohérence
# ---------------------------------------------------------------------------

def test_merge_dedoublonne_par_timestamp_et_garde_la_derniere():
    archive = [candle(at(0), c="100"), candle(at(1), c="101")]
    rest = [candle(at(1), c="999"), candle(at(2), c="102")]

    merged = merge(archive, rest)
    assert [c[0] for c in merged] == [at(0), at(1), at(2)]
    assert merged[1][4] == Decimal("999")


def test_reconcile_aggregation_valide_une_archive_coherente():
    h1 = [
        candle(at(h), "100", "102", "99", "101", "1")
        for h in range(4)
    ]
    h4 = aggregate(h1, "1h", "4h")

    compared, divergent, examples = reconcile_aggregation(h1, h4)
    assert compared == 1
    assert divergent == 0
    assert examples == []


def test_reconcile_aggregation_signale_un_prix_divergent():
    h1 = [candle(at(h), "100", "102", "99", "101", "1") for h in range(4)]
    h4 = aggregate(h1, "1h", "4h")
    h4[0][4] = Decimal("666")  # clôture publiée incohérente

    compared, divergent, examples = reconcile_aggregation(h1, h4)
    assert compared == 1
    assert divergent == 1
    assert "close" in examples[0]
    assert "666" in examples[0]


def test_reconcile_aggregation_ne_compare_que_les_fenetres_communes():
    h1 = [candle(at(h), "100", "102", "99", "101", "1") for h in range(4)]
    h4 = aggregate(h1, "1h", "4h") + [candle(at(8), "1", "1", "1", "1", "1")]

    compared, divergent, _ = reconcile_aggregation(h1, h4)
    assert compared == 1
    assert divergent == 0


# ---------------------------------------------------------------------------
# Quota de stockage (plancher de 100 Mo libres sur 500 Mo)
# ---------------------------------------------------------------------------

QUOTA = 500_000_000
PLANCHER = 100_000_000


def test_storage_verdict_autorise_une_ecriture_qui_laisse_le_plancher():
    verdict = storage_verdict(
        db_bytes=78_000_000, row_bytes=200, rows_to_add=1_000,
        quota_bytes=QUOTA, min_free_bytes=PLANCHER,
    )
    assert verdict.allowed
    assert verdict.free_after_bytes == QUOTA - 78_200_000
    assert "OK" in verdict.reason


def test_storage_verdict_refuse_une_ecriture_qui_passe_sous_le_plancher():
    verdict = storage_verdict(
        db_bytes=390_000_000, row_bytes=200, rows_to_add=100_000,
        quota_bytes=QUOTA, min_free_bytes=PLANCHER,
    )
    assert not verdict.allowed
    assert "refusée" in verdict.reason
    assert verdict.free_after_bytes < PLANCHER


def test_storage_verdict_refuse_une_base_deja_sous_le_plancher():
    """Même une écriture vide est refusée : le plancher est une règle, pas un budget."""
    verdict = storage_verdict(
        db_bytes=410_000_000, row_bytes=200, rows_to_add=0,
        quota_bytes=QUOTA, min_free_bytes=PLANCHER,
    )
    assert not verdict.allowed


def test_storage_verdict_accepte_l_egalite_au_plancher():
    verdict = storage_verdict(
        db_bytes=QUOTA - PLANCHER, row_bytes=200, rows_to_add=0,
        quota_bytes=QUOTA, min_free_bytes=PLANCHER,
    )
    assert verdict.allowed
    assert verdict.free_after_bytes == PLANCHER


def test_storage_verdict_mesure_la_marge_actuelle():
    verdict = storage_verdict(
        db_bytes=78_000_000, row_bytes=200, rows_to_add=0,
        quota_bytes=QUOTA, min_free_bytes=PLANCHER,
    )
    assert verdict.free_now_bytes == QUOTA - 78_000_000
