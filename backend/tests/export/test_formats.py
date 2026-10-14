"""CSV writer and ZIP name builder (#21), no database."""

from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal

from app.domain.enums import Category, FilingStatus, PaymentMethod
from app.export.archive import entry_name, entry_names, vendor_slug
from app.export.csv_file import HEADER, csv_bytes, safe_text
from app.export.data import ExportData, Line, de_money, de_plain, zeile_key


def line(n: int, **kw: object) -> Line:
    values: dict[str, object] = {
        "item_id": f"{n:08d}-0000-0000-0000-000000000000",
        "beleg_nr": f"{n:08d}",
        "anlage": "n",
        "zeile": "54",
        "category": Category.WK_ARBEITSMITTEL,
        "person": "Steuerpflichtige/r (Person 1)",
        "document_id": f"doc{n}",
        "day": date(2025, 3, 4),
        "vendor": "Muster",
        "payment": PaymentMethod.CARD,
        "gross": Decimal("1234.5"),
        "deductible": Decimal("-10"),
        "labour_35a": None,
        "unreviewed": False,
        "not_computed": False,
        "mime_type": "application/pdf",
        "storage_key": "k",
        "size_bytes": 1,
    }
    values.update(kw)
    return Line(**values)  # type: ignore[arg-type]


def data_of(*lines: Line) -> ExportData:
    from app.export.data import Group

    groups = tuple(
        Group(l.anlage, l.zeile, l.category, "0.00", "src", (l,))
        for l in lines  # noqa: E741
    )
    return ExportData({"year": 2025}, FilingStatus.SINGLE, groups, {}, date(2026, 1, 1))


def test_money_formats() -> None:
    assert de_money("1234567.5") == "1.234.567,50"
    assert de_money("-0.05") == "-0,05"
    assert de_plain("1234.5") == "1234,50"


def test_csv_bom_semicolon_crlf_decimals_and_quoting() -> None:
    raw = csv_bytes(data_of(line(1, vendor='Müller; "Büro"\nZwei')))
    assert raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    assert text.count("\r\n") == 2  # header and row; the quoted newline stays as is
    rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=";"))
    assert tuple(rows[0]) == HEADER
    row = rows[1]
    assert row[0] == "Anlage N" and row[1] == "54" and row[2]
    assert row[5] == "04.03.2025" and row[6] == 'Müller; "Büro"\nZwei'
    assert row[8] == "1234,50" and row[9] == "-10,00"  # amounts are never guarded


def test_csv_formula_injection_guard() -> None:
    for bad in ("=1+1", "+49", "-cmd", "@SUM(A1)", "\tx", "\rx"):
        assert safe_text(bad) == "'" + bad
    assert safe_text("Muster") == "Muster" and safe_text(None) == ""
    rows = list(
        csv.reader(
            io.StringIO(csv_bytes(data_of(line(1, vendor="=1+1"))).decode("utf-8-sig")),
            delimiter=";",
        )
    )
    assert rows[1][6] == "'=1+1"


def test_csv_rows_sorted_by_anlage_zeile_date_id() -> None:
    a = line(3, zeile="9")
    b = line(2, zeile="10")
    c = line(1, anlage="sonderausgaben", zeile="5")
    d = line(4, zeile=None)
    order = [r.item_id[:8] for r in data_of(c, d, b, a).lines]
    assert order == ["00000003", "00000002", "00000004", "00000001"]
    assert zeile_key("9") < zeile_key("10") < zeile_key("a") < zeile_key(None)


def test_vendor_slug() -> None:
    assert vendor_slug("Müller & Söhne GmbH") == "Muller-Sohne-GmbH"
    assert vendor_slug(None) == "beleg" and vendor_slug("???") == "beleg"
    assert len(vendor_slug("x" * 300)) == 40
    slug = vendor_slug("../../etc/passwd\x00")
    assert "/" not in slug and "." not in slug and "\x00" not in slug
    assert vendor_slug("ä" * 10) == "a" * 10


def test_entry_names_pattern_collisions_and_shared_documents() -> None:
    a = line(1)
    b = line(2, document_id="doc2")
    shared = line(3, document_id="doc1", zeile="60")
    nozeile = line(4, zeile=None, day=None, document_id="doc4", mime_type="image/jpeg")
    manual = line(5, document_id=None, mime_type=None)
    names, owners = entry_names([a, b, shared, nozeile, manual])
    assert names[a.item_id] == "n_54_2025-03-04_Muster.pdf"
    assert names[b.item_id] == "n_54_2025-03-04_Muster_2.pdf"
    assert names[shared.item_id] == names[a.item_id]  # one file per document
    assert names[nozeile.item_id] == "n_ohne-zeile_ohne-datum_Muster.jpg"
    assert manual.item_id not in names
    assert [o.item_id for o in owners] == [a.item_id, b.item_id, nozeile.item_id]
    assert entry_name(line(6, mime_type="text/evil")).endswith(".bin")


def test_csv_marks_document_less_lines_but_not_missing_originals() -> None:
    """#128: no document -> Hinweis 'ohne Beleg'; a line whose file is missing (no entry in
    `files`) keeps an empty column and no such hint (FEHLT.txt covers it)."""
    rows = list(
        csv.reader(
            io.StringIO(csv_bytes(data_of(line(1, document_id=None), line(2))).decode("utf-8-sig")),
            delimiter=";",
        )
    )
    hint, name = HEADER.index("Hinweis"), HEADER.index("Datei im ZIP")
    assert "ohne Beleg" in rows[1][hint] and rows[1][name] == ""
    assert rows[2][hint] == "" and rows[2][name] == ""
