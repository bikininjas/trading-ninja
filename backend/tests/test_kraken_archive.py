"""Lecture ZIP par plages HTTP : le mécanisme qui évite 9,5 Go de téléchargement.

Les tests construisent une vraie archive ZIP en mémoire (`zipfile`), puis la
servent par tranches avec `BytesRangeSource` — exactement le contrat de la source
HTTP. Un ZIP64 est fabriqué à la main pour vérifier l'analyse du champ
supplémentaire, que `zipfile` ne produit pas sur un petit fichier.
"""

from __future__ import annotations

import io
import struct
import zipfile

import pytest

from app.market.kraken_archive import (
    BytesRangeSource,
    KrakenArchive,
    KrakenArchiveError,
    MultiPartRangeSource,
    parse_central_directory,
)


def build_zip(members: dict[str, bytes], compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=compression) as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


CSV = b"1704067200,100,110,90,105,1,2\n1704070800,105,108,100,107,3,4\n"


# ---------------------------------------------------------------------------
# Lecture d'un membre
# ---------------------------------------------------------------------------

def test_lit_un_membre_deflate_sans_telecharger_l_archive():
    data = build_zip({"XBTEUR_60.csv": CSV, "autre.csv": b"x,y\n"})
    archive = KrakenArchive(BytesRangeSource(data))

    assert archive.read_text("XBTEUR_60.csv") == CSV.decode()


def test_lit_un_membre_stored():
    data = build_zip({"ETHEUR_240.csv": CSV}, compression=zipfile.ZIP_STORED)
    archive = KrakenArchive(BytesRangeSource(data))

    assert archive.read("ETHEUR_240.csv") == CSV


def test_liste_tous_les_membres_avec_leurs_tailles():
    data = build_zip({"a.csv": CSV, "b.csv": b"z"})
    members = KrakenArchive(BytesRangeSource(data)).members()

    assert set(members) == {"a.csv", "b.csv"}
    assert members["a.csv"].uncompressed_size == len(CSV)
    assert members["a.csv"].method in (0, 8)


def test_un_membre_absent_est_une_erreur_explicite():
    data = build_zip({"a.csv": CSV})
    archive = KrakenArchive(BytesRangeSource(data))

    with pytest.raises(KrakenArchiveError, match="membre absent"):
        archive.read("SOLEUR_60.csv")


def test_un_entete_local_corrompu_est_refuse():
    data = bytearray(build_zip({"a.csv": CSV}))
    archive = KrakenArchive(BytesRangeSource(bytes(data)))
    member = archive.members()["a.csv"]
    data[member.offset:member.offset + 4] = b"XXXX"

    with pytest.raises(KrakenArchiveError, match="en-tête local"):
        KrakenArchive(BytesRangeSource(bytes(data))).read("a.csv")


def test_une_source_hors_bornes_est_refusee():
    source = BytesRangeSource(b"abc")

    with pytest.raises(KrakenArchiveError, match="hors bornes"):
        source.read(0, 10)


# ---------------------------------------------------------------------------
# Archive multi-parties
# ---------------------------------------------------------------------------

def test_multi_parties_reconstitue_l_archive_concatenee():
    data = build_zip({"a.csv": CSV, "b.csv": CSV * 50})
    cut1, cut2 = len(data) // 3, 2 * len(data) // 3
    source = MultiPartRangeSource([
        BytesRangeSource(data[:cut1]),
        BytesRangeSource(data[cut1:cut2]),
        BytesRangeSource(data[cut2:]),
    ])
    archive = KrakenArchive(source)

    assert source.size() == len(data)
    assert archive.read_text("b.csv") == (CSV * 50).decode()


def test_multi_parties_refuse_une_plage_hors_bornes():
    source = MultiPartRangeSource([BytesRangeSource(b"abc")])

    with pytest.raises(KrakenArchiveError, match="hors bornes"):
        source.read(1, 5)


def test_multi_parties_exige_au_moins_une_partie():
    with pytest.raises(KrakenArchiveError, match="aucune partie"):
        MultiPartRangeSource([])


# ---------------------------------------------------------------------------
# ZIP64
# ---------------------------------------------------------------------------

def test_parse_central_directory_lit_l_offset_zip64():
    """Le répertoire central d'une grosse archive met l'offset dans l'extra."""
    name = b"XBTEUR_60.csv"
    real_offset = 0x1_2345_6789
    extra = struct.pack("<HHQ", 0x0001, 8, real_offset)
    record = struct.pack(
        "<IHHHHHHIIIHHHHHII",
        0x02014B50, 45, 45, 0, 8, 0, 0, 0,
        100, 200,                # tailles compressée/décompressée
        len(name), len(extra), 0,
        0, 0, 0,
        0xFFFFFFFF,              # offset 32 bits = sentinelle ZIP64
    ) + name + extra

    members = parse_central_directory(record)

    assert members["XBTEUR_60.csv"].offset == real_offset
    assert members["XBTEUR_60.csv"].compressed_size == 100
    assert members["XBTEUR_60.csv"].uncompressed_size == 200


def test_parse_central_directory_ignore_les_octets_non_reconnus():
    assert parse_central_directory(b"bruit") == {}
