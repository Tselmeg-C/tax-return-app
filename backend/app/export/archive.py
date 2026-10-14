"""The ZIP export: the originals of the counted receipts plus `uebersicht.csv` and `LIESMICH.txt`.

Entry names are `<anlage>_<zeile>_<date>_<vendor>.<ext>`: ASCII only, the vendor sanitised, the
extension from the sniffed mime type. `original_filename` is never used. Originals are read
through `Storage.open` only; a missing one is skipped and listed (item ids, no names) in
`FEHLT.txt`. The archive is built in a `SpooledTemporaryFile` so the response knows its size
and the number of missing files before the first byte is sent.
"""

from __future__ import annotations

import re
import tempfile
import unicodedata
import zipfile
from dataclasses import dataclass
from typing import IO

import structlog

from app.documents.sniff import EXT_BY_MIME
from app.export.csv_file import csv_bytes
from app.export.data import ExportData, Line
from app.storage import Storage
from app.storage.base import StorageError

log = structlog.stdlib.get_logger("app.export")

SPOOL_BYTES = 16 * 1024 * 1024
VENDOR_MAX = 40
_UNSAFE = re.compile(r"[^A-Za-z0-9]+")
README = (
    "belegbot: Belege fuer die Steuererklaerung\r\n"
    "Diese Datei ist eine unverbindliche Belegsammlung, keine Steuerberatung.\r\n"
    "uebersicht.csv listet jeden Beleg mit Betrag, Zeile und Dateiname in diesem Archiv.\r\n"
    "Zeilen mit 'ohne Beleg' im Hinweis haben keine Datei (Eigenbeleg ohne Dokument).\r\n"
    "Dateinamen: <Anlage>_<Zeile>_<Datum>_<Lieferant>.<Endung>.\r\n"
    "Alle Angaben vor der Abgabe pruefen; massgeblich ist der Bescheid des Finanzamts.\r\n"
)


def vendor_slug(vendor: str | None) -> str:
    ascii_text = unicodedata.normalize("NFKD", vendor or "").encode("ascii", "ignore").decode()
    return _UNSAFE.sub("-", ascii_text).strip("-")[:VENDOR_MAX].strip("-") or "beleg"


def entry_name(line: Line) -> str:
    ext = EXT_BY_MIME.get(line.mime_type or "", "bin")
    day = line.day.isoformat() if line.day else "ohne-datum"
    return f"{line.anlage}_{line.zeile or 'ohne-zeile'}_{day}_{vendor_slug(line.vendor)}.{ext}"


def entry_names(lines: list[Line]) -> tuple[dict[str, str], list[Line]]:
    """(item id -> entry name, the lines that own a file). One file per document: later lines
    of the same document point to the first line's name. Collisions get `_2`, `_3`."""
    names: dict[str, str] = {}
    by_doc: dict[str, str] = {}
    taken: set[str] = set()
    owners: list[Line] = []
    for line in lines:
        if line.document_id is None:
            continue
        if line.document_id in by_doc:
            names[line.item_id] = by_doc[line.document_id]
            continue
        base = entry_name(line)
        stem, _, ext = base.rpartition(".")
        name, n = base, 1
        while name.lower() in taken:
            n += 1
            name = f"{stem}_{n}.{ext}"
        taken.add(name.lower())
        by_doc[line.document_id] = names[line.item_id] = name
        owners.append(line)
    return names, owners


@dataclass
class Archive:
    file: IO[bytes]
    size: int
    missing: int

    def close(self) -> None:
        self.file.close()


async def build_archive(data: ExportData, storage: Storage) -> Archive:
    names, owners = entry_names(data.lines)
    spool = tempfile.SpooledTemporaryFile(max_size=SPOOL_BYTES)  # noqa: SIM115 (closed by caller)
    missing: list[str] = []
    try:
        with zipfile.ZipFile(spool, "w", compression=zipfile.ZIP_STORED) as zf:
            for line in owners:
                assert line.storage_key is not None
                try:
                    reader = await storage.open(line.storage_key)
                except StorageError as exc:  # not found, bad key: skip, never abort
                    log.warning("export.original_unreadable", error_kind=type(exc).__name__)
                    missing.append(line.item_id)
                    continue
                try:
                    with zf.open(names[line.item_id], "w", force_zip64=True) as out:
                        async for chunk in reader:
                            out.write(chunk)
                finally:
                    await reader.aclose()
            gone = {names[i] for i in missing}
            files = {item: name for item, name in names.items() if name not in gone}
            zf.writestr("uebersicht.csv", csv_bytes(data, files))
            zf.writestr("LIESMICH.txt", README.encode("utf-8"))
            if missing:
                body = "".join(f"{i[:8]} file_missing\r\n" for i in missing)
                zf.writestr("FEHLT.txt", body.encode("ascii"))
        size = spool.tell()
        spool.seek(0)
    except BaseException:
        spool.close()
        raise
    return Archive(spool, size, len(missing))
