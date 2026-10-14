"""The CSV export: `;`, de-DE decimals, CRLF, UTF-8 with BOM (Excel de opens it correctly).

One row per counted receipt, in `ExportData.lines` order. Text cells that start with a formula
character get a leading `'` (formula injection); amounts are never guarded.
"""

from __future__ import annotations

import csv
import io

from app.domain.enums import LABELS_DE, Anlage, Category, PaymentMethod
from app.export.data import ExportData, de_date, de_plain

HEADER = (
    "Anlage",
    "Zeile",
    "Kategorie",
    "Person",
    "Beleg-Nr",
    "Datum",
    "Lieferant",
    "Zahlungsart",
    "Brutto",
    "Absetzbar",
    "Lohnanteil §35a",
    "Hinweis",
    "Datei im ZIP",
)
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def safe_text(value: str | None) -> str:
    text = value or ""
    return "'" + text if text.startswith(_FORMULA_START) else text


def csv_bytes(data: ExportData, files: dict[str, str] | None = None) -> bytes:
    """`files`: item id -> name in the ZIP (only the ZIP's `uebersicht.csv` fills it)."""
    out = io.StringIO(newline="")
    writer = csv.writer(out, delimiter=";", lineterminator="\r\n")
    writer.writerow(HEADER)
    for r in data.lines:
        hints = []
        if r.unreviewed:
            hints.append("ungeprüft")
        if r.not_computed:
            hints.append("nicht_berechnet")
        if r.document_id is None:
            hints.append("ohne Beleg")
        if r.zeile is None:
            hints.append("Zeile nicht zugeordnet")
        writer.writerow(
            [
                LABELS_DE[Anlage][Anlage(r.anlage)],
                r.zeile or "",
                LABELS_DE[Category][r.category],
                r.person,
                r.beleg_nr,
                de_date(r.day) if r.day else "",
                safe_text(r.vendor),
                LABELS_DE[PaymentMethod][r.payment],
                de_plain(r.gross),
                de_plain(r.deductible),
                "" if r.labour_35a is None else de_plain(r.labour_35a),
                ", ".join(hints),
                (files or {}).get(r.item_id, ""),
            ]
        )
    return out.getvalue().encode("utf-8-sig")
