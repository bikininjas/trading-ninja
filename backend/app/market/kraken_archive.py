"""Lecture partielle d'une archive ZIP Kraken par requêtes HTTP Range.

Les archives OHLCVT trimestrielles de Kraken pèsent ~575 Mo chacune parce
qu'elles contiennent toutes les paires dans sept granularités. Télécharger trois
ans de ces archives (≈ 7 Go) pour n'utiliser que six fichiers CSV de quelques
dizaines de kilo-octets serait absurde.

Or le serveur annonce `accept-ranges: bytes` : ce module lit le répertoire
central du ZIP (à la fin du fichier), puis ne récupère **que** les octets des
membres demandés. Six fichiers par trimestre ≈ 250 Ko au lieu de 575 Mo.

Contraintes assumées :

- Lecture seule, aucune écriture.
- Méthodes de compression `stored` (0) et `deflate` (8) uniquement — ce sont
  celles de l'archive Kraken ; toute autre méthode lève.
- ZIP classique et ZIP64 (l'archive complète dépasse 4 Go, les trimestrielles
  non, mais le répertoire peut être en ZIP64).

Le module est sans base de données et sans horloge : `HttpRangeSource` fait le
réseau, `BytesRangeSource` sert aux tests.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from typing import Protocol

__all__ = [
    "ArchiveMember",
    "BytesRangeSource",
    "HttpRangeSource",
    "KrakenArchive",
    "KrakenArchiveError",
    "MultiPartRangeSource",
    "parse_central_directory",
]

EOCD_SIGNATURE = b"PK\x05\x06"
CENTRAL_SIGNATURE = b"PK\x01\x02"
ZIP64_LOCATOR = b"PK\x06\x07"
LOCAL_SIGNATURE = b"PK\x03\x04"

TAIL_BYTES = 130_000          # de quoi contenir l'EOCD et ses commentaires
STORED, DEFLATED = 0, 8


class KrakenArchiveError(RuntimeError):
    """Archive illisible, membre absent ou compression non gérée."""


class RangeSource(Protocol):
    """Une source d'octets adressable, sans téléchargement complet."""

    def size(self) -> int: ...

    def read(self, start: int, end: int) -> bytes:
        """Octets de `start` à `end` **inclus**."""
        ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ArchiveMember:
    name: str
    method: int
    compressed_size: int
    uncompressed_size: int
    offset: int


class HttpRangeSource:
    """Source HTTP ; toute réponse autre que 206 est une erreur explicite."""

    def __init__(self, url: str, *, timeout: float = 60.0, retries: int = 3) -> None:
        import httpx  # import local : le module reste importable sans réseau

        self._client = httpx.Client(timeout=timeout, follow_redirects=True)
        self._url = url
        self._retries = retries
        self._size: int | None = None

    def size(self) -> int:
        if self._size is None:
            response = self._client.head(self._url)
            response.raise_for_status()
            length = response.headers.get("content-length")
            if not length:
                raise KrakenArchiveError(f"taille inconnue pour {self._url}")
            self._size = int(length)
        return self._size

    def read(self, start: int, end: int) -> bytes:
        if start < 0 or end < start:
            raise KrakenArchiveError(f"plage invalide : {start}-{end}")
        last: Exception | None = None
        for _ in range(self._retries):
            try:
                response = self._client.get(
                    self._url, headers={"Range": f"bytes={start}-{end}"}
                )
                if response.status_code != 206:
                    raise KrakenArchiveError(
                        f"le serveur ignore les requêtes Range "
                        f"(HTTP {response.status_code} sur {self._url})"
                    )
                return response.content
            except Exception as exc:  # noqa: BLE001 - on retente, puis on relaie
                last = exc
        raise KrakenArchiveError(f"lecture {start}-{end} impossible : {last}")

    def close(self) -> None:
        self._client.close()


class BytesRangeSource:
    """Source en mémoire, pour les tests : même contrat que HTTP."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    def size(self) -> int:
        return len(self._data)

    def read(self, start: int, end: int) -> bytes:
        if start < 0 or end >= len(self._data) or end < start:
            raise KrakenArchiveError(f"plage hors bornes : {start}-{end}")
        return self._data[start:end + 1]

    def close(self) -> None:
        self._data = b""


class MultiPartRangeSource:
    """Plusieurs fichiers concaténables vus comme un seul espace d'octets.

    L'archive complète de Kraken est découpée en cinq parties (`cat part* > zip`).
    Ce compositeur applique la même concaténation sans télécharger : une lecture
    à l'offset X est traduite vers la partie qui contient X. Le répertoire central
    vit dans la dernière partie (~1,16 Go), donc seuls ses derniers kilo-octets
    sont lus.
    """

    def __init__(self, parts: list[str | RangeSource], *, timeout: float = 90.0,
                 retries: int = 3) -> None:
        if not parts:
            raise KrakenArchiveError("aucune partie fournie")
        self._parts: list[RangeSource] = [
            HttpRangeSource(part, timeout=timeout, retries=retries)
            if isinstance(part, str) else part
            for part in parts
        ]
        self._starts: list[int] | None = None

    def _layout(self) -> list[tuple[int, RangeSource]]:
        if self._starts is None:
            starts: list[int] = []
            cursor = 0
            for part in self._parts:
                starts.append(cursor)
                cursor += part.size()
            self._starts = starts
        return list(zip(self._starts, self._parts))

    def size(self) -> int:
        return sum(part.size() for part in self._parts)

    def read(self, start: int, end: int) -> bytes:
        if start < 0 or end < start or end >= self.size():
            raise KrakenArchiveError(f"plage hors bornes : {start}-{end}")
        chunks = bytearray()
        for part_start, part in self._layout():
            part_end = part_start + part.size() - 1
            if end < part_start or start > part_end:
                continue
            low = max(start, part_start)
            high = min(end, part_end)
            chunks += part.read(low - part_start, high - part_start)
        if len(chunks) != end - start + 1:
            raise KrakenArchiveError(
                f"lecture incomplète {start}-{end} : {len(chunks)} octets"
            )
        return bytes(chunks)

    def close(self) -> None:
        for part in self._parts:
            part.close()



ZIP64_EXTRA_ID = 0x0001


def _apply_zip64_extra(extra: bytes, uncompressed: int, compressed: int,
                       offset: int) -> tuple[int, int, int]:
    """Applique le champ supplémentaire ZIP64 (id 0x0001) aux valeurs 32 bits.

    L'ordre des champs 64 bits est imposé par la spécification : taille
    décompressée, taille compressée, offset de l'en-tête local, numéro de disque.
    Un champ n'est présent que si sa valeur 32 bits vaut 0xFFFFFFFF.
    """
    position = 0
    while position + 4 <= len(extra):
        field_id, field_size = struct.unpack("<HH", extra[position:position + 4])
        body = extra[position + 4:position + 4 + field_size]
        if field_id == ZIP64_EXTRA_ID:
            cursor = 0
            if uncompressed == 0xFFFFFFFF and cursor + 8 <= len(body):
                uncompressed = struct.unpack("<Q", body[cursor:cursor + 8])[0]
                cursor += 8
            if compressed == 0xFFFFFFFF and cursor + 8 <= len(body):
                compressed = struct.unpack("<Q", body[cursor:cursor + 8])[0]
                cursor += 8
            if offset == 0xFFFFFFFF and cursor + 8 <= len(body):
                offset = struct.unpack("<Q", body[cursor:cursor + 8])[0]
            break
        position += 4 + field_size
    return uncompressed, compressed, offset


def parse_central_directory(directory: bytes) -> dict[str, ArchiveMember]:
    """Répertoire central d'un ZIP → {nom: membre}. Pur, testable hors réseau."""
    members: dict[str, ArchiveMember] = {}
    position = 0
    while position + 46 <= len(directory) and directory[position:position + 4] == CENTRAL_SIGNATURE:
        fields = struct.unpack("<IHHHHHHIIIHHHHHII", directory[position:position + 46])
        method = fields[4]
        compressed, uncompressed = fields[8], fields[9]
        name_len, extra_len, comment_len = fields[10], fields[11], fields[12]
        offset = fields[16]
        name = directory[position + 46:position + 46 + name_len].decode("utf-8", "replace")
        extra = directory[
            position + 46 + name_len:position + 46 + name_len + extra_len
        ]
        if 0xFFFFFFFF in (compressed, uncompressed, offset):
            uncompressed, compressed, offset = _apply_zip64_extra(
                extra, uncompressed, compressed, offset
            )
        members[name] = ArchiveMember(name, method, compressed, uncompressed, offset)
        position += 46 + name_len + extra_len + comment_len
    return members


class KrakenArchive:
    """Archive ZIP distante, lue membre par membre."""

    def __init__(self, source: RangeSource) -> None:
        self._source = source
        self._members: dict[str, ArchiveMember] | None = None

    # -- structure ---------------------------------------------------------

    def _read_end_of_central_directory(self) -> tuple[int, int, int]:
        """Rend (entries, cd_size, cd_offset), en gérant le ZIP64."""
        size = self._source.size()
        tail_start = max(0, size - TAIL_BYTES)
        tail = self._source.read(tail_start, size - 1)
        index = tail.rfind(EOCD_SIGNATURE)
        if index < 0:
            raise KrakenArchiveError("répertoire central introuvable")
        _, _, _, _, entries, cd_size, cd_offset, _ = struct.unpack(
            "<IHHHHIIH", tail[index:index + 22]
        )
        if cd_offset == 0xFFFFFFFF or entries == 0xFFFF:
            locator = tail.rfind(ZIP64_LOCATOR)
            if locator < 0:
                raise KrakenArchiveError("localisateur ZIP64 introuvable")
            zip64_offset = struct.unpack("<Q", tail[locator + 8:locator + 16])[0]
            record = self._source.read(zip64_offset, zip64_offset + 55)
            entries, cd_size, cd_offset = struct.unpack("<QQQ", record[32:56])
        return entries, cd_size, cd_offset

    def members(self) -> dict[str, ArchiveMember]:
        if self._members is None:
            _, cd_size, cd_offset = self._read_end_of_central_directory()
            directory = self._source.read(cd_offset, cd_offset + cd_size - 1)
            self._members = parse_central_directory(directory)
            if not self._members:
                raise KrakenArchiveError("répertoire central vide")
        return self._members

    # -- lecture -----------------------------------------------------------

    def read(self, name: str) -> bytes:
        member = self.members().get(name)
        if member is None:
            raise KrakenArchiveError(f"membre absent de l'archive : {name}")
        if member.method not in (STORED, DEFLATED):
            raise KrakenArchiveError(
                f"compression non gérée ({member.method}) pour {name}"
            )
        header = self._source.read(member.offset, member.offset + 29)
        if header[:4] != LOCAL_SIGNATURE:
            raise KrakenArchiveError(f"en-tête local corrompu pour {name}")
        name_len, extra_len = struct.unpack("<HH", header[26:30])
        start = member.offset + 30 + name_len + extra_len
        payload = self._source.read(start, start + member.compressed_size - 1)
        if member.method == STORED:
            data = payload
        else:
            data = zlib.decompress(payload, -15)
        if len(data) != member.uncompressed_size:
            raise KrakenArchiveError(
                f"{name} : {len(data)} octets décompressés, "
                f"{member.uncompressed_size} annoncés"
            )
        return data

    def read_text(self, name: str, encoding: str = "utf-8") -> str:
        return self.read(name).decode(encoding)
