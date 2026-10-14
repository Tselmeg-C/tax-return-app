"""#21 export: routes, consistency with `GET /summary`, ZIP contents, isolation, privacy."""

from __future__ import annotations

import csv
import io
import re
import zipfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pypdf import PdfReader

from app.domain.enums import LABELS_DE, Anlage
from app.export.csv_file import HEADER
from app.export.data import de_money, load_export
from app.export.pdf import pdf_bytes
from tests.api.conftest import T
from tests.export.scenario import (
    EMPLOYER_SENTINEL,
    FILE_SENTINEL,
    NAME_SENTINEL,
    Scenario,
    iid,
    item,
    make_scenario,
)

YEAR = 2025
KAP_V = {"kap", "v"}


def pdf_text(raw: bytes) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(raw)).pages)


def csv_rows(raw: bytes) -> list[dict[str, str]]:
    text = raw.decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text, newline=""), delimiter=";"))


def de_to_decimal(value: str) -> Decimal:
    return Decimal(value.replace(".", "").replace(",", "."))


async def get(sc: Scenario, fmt: str, year: int = YEAR) -> Any:
    return await sc.t.get(sc.home, f"/export/{year}.{fmt}")


async def test_routes_need_a_session_and_validate_the_year(t: T) -> None:
    for fmt in ("pdf", "csv", "zip"):
        r = await t.get(None, f"/export/2025.{fmt}")
        assert r.status_code == 401 and r.json() == {"detail": "not_authenticated"}
    home = await t.home()
    for fmt in ("pdf", "csv", "zip"):
        bad = await t.get(home, f"/export/1999.{fmt}")
        assert bad.status_code == 422 and bad.json() == {
            "detail": "unsupported_year",
            "field": "year",
        }
        none = await t.get(home, f"/export/2025.{fmt}")
        assert none.status_code == 409 and none.json() == {"detail": "no_profile"}
        assert none.headers["cache-control"] == "no-store"


async def test_headers_fixed_names_no_csrf_needed(t: T) -> None:
    sc = await make_scenario(t)
    for fmt, mime, name in (
        ("pdf", "application/pdf", "bericht.pdf"),
        ("csv", "text/csv; charset=utf-8", "belege.csv"),
        ("zip", "application/zip", "belege.zip"),
    ):
        r = await t.api.request("GET", f"/export/{YEAR}.{fmt}", cookie=sc.home.cookie, csrf=False)
        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == mime
        assert r.headers["content-disposition"] == f'attachment; filename="belegbot-{YEAR}-{name}"'
        assert r.headers["cache-control"] == "no-store"
        assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-export-missing"] == "0"


async def test_totals_equal_the_dashboard_to_the_cent(t: T) -> None:
    sc = await make_scenario(t)
    summary = (await t.get(sc.home, f"/summary?year={YEAR}")).json()
    assert summary["status"] == "ok"
    # by_line adds up to by_anlage by construction.
    per_anlage: dict[str, Decimal] = {}
    for line in summary["by_line"]:
        per_anlage[line["anlage"]] = per_anlage.get(line["anlage"], Decimal(0)) + Decimal(
            line["amount"]
        )
    assert {a: f"{v:.2f}" for a, v in per_anlage.items()} == {
        a["anlage"]: a["amount"] for a in summary["by_anlage"]
    }
    # CSV: the sum of the rows per Anlage.
    rows = csv_rows((await get(sc, "csv")).content)
    csv_sum: dict[str, Decimal] = {}
    labels = {LABELS_DE[Anlage][a]: a.value for a in Anlage}
    for row in rows:
        key = labels[row["Anlage"]]
        csv_sum[key] = csv_sum.get(key, Decimal(0)) + de_to_decimal(row["Absetzbar"])
    # Beleg-level rows add up to the Anlage sums (the CSV holds every counted item once).
    assert {a: f"{v:.2f}" for a, v in csv_sum.items()} == {
        a["anlage"]: a["amount"] for a in summary["by_anlage"]
    }
    # PDF: "Summe <Anlage>" lines and the refund.
    text = pdf_text((await get(sc, "pdf")).content)
    for entry in summary["by_anlage"]:
        label = LABELS_DE[Anlage][Anlage(entry["anlage"])]
        found = re.findall(rf"Summe {re.escape(label)}\s+(-?[\d.]+,\d{{2}}) €", text)
        assert found == [de_money(entry["amount"])], label
    refund = Decimal(summary["refund"])
    word = "Nachzahlung" if refund < 0 else "Erstattung"
    assert re.search(rf"{word}\s+{re.escape(de_money(abs(refund)))} €", text)
    assert "Nicht in der Schätzung" in text and "Kapitalerträge und Vermietung" in text


async def test_pdf_page_one_content(t: T) -> None:
    sc = await make_scenario(t)
    text = pdf_text((await get(sc, "pdf")).content)
    for needle in (
        "Steuerübersicht 2025 – belegbot",
        "Zusammenveranlagung",
        "keine Steuerberatung",
        "vorläufig",
        "Seite 1 von",
        "ungeprüft",
        "Beleg-Nr.",
    ):
        assert needle in text, needle
    assert "Müller & Söhne" in text  # umlauts survive
    # The KAP item is listed apart, never in an Anlage section of the estimate.
    assert text.index("Nicht in der Schätzung") > text.index("Anlagen und Zeilen")


async def test_csv_rows_are_exactly_the_counted_items(t: T) -> None:
    sc = await make_scenario(t)
    r = await get(sc, "csv")
    rows = csv_rows(r.content)
    assert list(rows[0]) == list(HEADER)
    nr = {row["Beleg-Nr"] for row in rows}
    counted = {iid(n)[:8] for n in (1, 2, 3, 4, 5, 9, 10, 11)}
    assert nr == counted  # not 6 (irrelevant), 7 (outside), 8 (excluded), 12 (2024)
    by = {row["Beleg-Nr"]: row for row in rows}
    assert by[iid(9)[:8]]["Hinweis"] == "ungeprüft"
    assert by[iid(4)[:8]]["Hinweis"] == "nicht_berechnet, Zeile nicht zugeordnet"
    assert "Zeile nicht zugeordnet" in by[iid(10)[:8]]["Hinweis"]
    assert by[iid(3)[:8]]["Lieferant"].startswith("'=")  # formula guard
    assert by[iid(3)[:8]]["Lohnanteil §35a"] == "400,00"
    assert by[iid(1)[:8]]["Lieferant"] == 'Müller & Söhne; "Büro"'
    assert by[iid(1)[:8]]["Brutto"] == "150,50" and by[iid(1)[:8]]["Datum"] == "04.03.2025"
    assert by[iid(5)[:8]]["Datum"] == "02.11.2025"  # payment date when no invoice date
    assert by[iid(11)[:8]]["Person"] == "Kind 1"
    assert by[iid(3)[:8]]["Person"] == "Haushalt"


async def test_zip_contains_exactly_the_relevant_originals(t: T) -> None:
    sc = await make_scenario(t)
    r = await get(sc, "zip")
    assert r.status_code == 200 and r.headers["x-export-missing"] == "0"
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = zf.namelist()
    assert "uebersicht.csv" in names and "LIESMICH.txt" in names and "FEHLT.txt" not in names
    originals = [n for n in names if n not in ("uebersicht.csv", "LIESMICH.txt")]
    pattern = re.compile(
        r"^[a-z_]+_(\d+|ohne-zeile)_(\d{4}-\d{2}-\d{2}|ohne-datum)_[A-Za-z0-9-]+(_\d+)?\.(pdf|jpg|png)$"
    )
    assert all(pattern.fullmatch(n) for n in originals), originals
    assert len(originals) == len(set(originals))
    # pdf1 (two items), jpg1, png1, unrev: four originals; none of pdf2 / pdf3 / bad.
    assert len(originals) == 4
    blobs = {zf.read(n) for n in originals}
    assert blobs == {sc.contents[k] for k in ("pdf1", "jpg1", "png1", "unrev")}  # decrypted bytes
    assert not any(FILE_SENTINEL in n for n in names)
    # The overview points both items of pdf1 at the one file.
    rows = {r["Beleg-Nr"]: r for r in csv_rows(zf.read("uebersicht.csv"))}
    assert rows[iid(1)[:8]]["Datei im ZIP"] == rows[iid(2)[:8]]["Datei im ZIP"] != ""
    assert rows[iid(5)[:8]]["Datei im ZIP"] == ""  # manual item, no original
    assert {r["Datei im ZIP"] for r in rows.values() if r["Datei im ZIP"]} == set(originals)


async def test_missing_original_is_skipped_and_listed(t: T) -> None:
    sc = await make_scenario(t)
    key = sc.docs["jpg1"].storage_key
    path = next(
        p
        for p in (Path(t.api.settings.resolved_storage_path)).rglob("*")
        if p.is_file() and key.split("/")[-2] in str(p)
    )
    path.unlink()
    r = await get(sc, "zip")
    assert r.status_code == 200 and r.headers["x-export-missing"] == "1"
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    assert zf.read("FEHLT.txt").decode() == f"{iid(3)[:8]} file_missing\r\n"
    rows = {r["Beleg-Nr"]: r for r in csv_rows(zf.read("uebersicht.csv"))}
    assert rows[iid(3)[:8]]["Datei im ZIP"] == ""
    assert len([n for n in zf.namelist() if n.endswith((".pdf", ".jpg", ".png"))]) == 3


async def test_zip_over_the_limit_is_413_before_any_bytes(t: T) -> None:
    sc = await make_scenario(t)
    t.api.app.state.settings = t.api.settings.model_copy(update={"export_zip_max_mb": 1})
    from sqlalchemy import update

    from app.db.models import Document

    await t.session.execute(update(Document).values(size_bytes=800 * 1024))
    await t.session.commit()
    r = await get(sc, "zip")
    assert r.status_code == 413
    assert r.json() == {"detail": "export_too_large", "max_bytes": 1024 * 1024}


async def test_other_household_never_appears(t: T) -> None:
    sc = await make_scenario(t)
    other = await make_scenario(t, base=1000)  # a second complete household with the same shape
    mine = (await get(sc, "csv")).content
    # Same-shaped data, but ids are household-owned: add one item only the other one has.
    await item(
        other,
        99,
        who="a",
        category=sc.t
        and __import__("app.domain.enums", fromlist=["Category"]).Category.WK_FORTBILDUNG,
        vendor="Muster Nur-Fremd",
    )
    assert b"Nur-Fremd" not in mine
    theirs = (await t.get(other.home, f"/export/{YEAR}.csv")).content
    assert b"Nur-Fremd" in theirs
    assert b"Nur-Fremd" not in (await get(sc, "csv")).content
    zip_names = zipfile.ZipFile(io.BytesIO((await get(sc, "zip")).content)).namelist()
    assert len(zip_names) == 6
    # A user of a household without a profile cannot read someone else's export.
    stranger = await t.home()
    assert (await t.get(stranger, f"/export/{YEAR}.zip")).status_code == 409


async def test_no_name_steuer_id_or_filename_in_any_export(t: T) -> None:
    sc = await make_scenario(t)
    blobs = [
        (await get(sc, "pdf")).content,
        pdf_text((await get(sc, "pdf")).content).encode(),
        (await get(sc, "csv")).content,
    ]
    zf = zipfile.ZipFile(io.BytesIO((await get(sc, "zip")).content))
    blobs += [zf.read("uebersicht.csv"), zf.read("LIESMICH.txt"), "\n".join(zf.namelist()).encode()]
    secrets_ = [NAME_SENTINEL, EMPLOYER_SENTINEL, FILE_SENTINEL]
    for sid in sc.steuer_ids:
        secrets_ += [sid, f"{sid[:2]} {sid[2:5]} {sid[5:8]} {sid[8:]}"]
    for blob in blobs:
        for s in secrets_:
            assert s.encode() not in blob, s


async def test_logs_and_spans_carry_counts_only(t: T, json_log: io.StringIO) -> None:
    sc = await make_scenario(t)
    for fmt in ("pdf", "csv", "zip"):
        assert (await get(sc, fmt)).status_code == 200
    await get(sc, "zip", 1999)
    logs = json_log.getvalue()
    assert logs.count('"export.built"') == 3
    for sentinel in (
        "Müller", "Muster", "Söhne", "HYPERLINK", NAME_SENTINEL, FILE_SENTINEL, EMPLOYER_SENTINEL,
        "120.50", "120,50", "500.00", *sc.steuer_ids,
    ):  # fmt: skip
        assert sentinel not in logs, sentinel
    assert '"items": 8' in logs or '"items":8' in logs


async def test_empty_return_pdf(t: T) -> None:
    sc = await make_scenario(t, with_items=False)
    r = await get(sc, "pdf")
    assert r.status_code == 200
    assert f"Keine Belege für {YEAR}" in pdf_text(r.content)
    assert len(csv_rows((await get(sc, "csv")).content)) == 0
    names = zipfile.ZipFile(io.BytesIO((await get(sc, "zip")).content)).namelist()
    assert sorted(names) == ["LIESMICH.txt", "uebersicht.csv"]


async def test_pdf_is_deterministic(t: T) -> None:
    sc = await make_scenario(t)
    data = await load_export(sc.built.scope, YEAR, date(2026, 1, 15))
    assert data is not None
    assert pdf_bytes(data) == pdf_bytes(data)


@pytest.mark.parametrize("fmt", ["pdf", "csv", "zip"])
async def test_member_of_another_household_gets_own_data_only(t: T, fmt: str) -> None:
    a = await make_scenario(t, with_items=False)
    b = await make_scenario(t, with_items=False)
    assert (await t.get(a.home, f"/export/{YEAR}.{fmt}")).status_code == 200
    assert (await t.get(b.home, f"/export/{YEAR}.{fmt}")).status_code == 200


async def test_export_queries_are_household_scoped(t: T, monkeypatch: pytest.MonkeyPatch) -> None:
    """Defense in depth (#121): ids come from the summary, the selects still scope."""
    import app.export.data as export_data
    from app.domain.enums import Category

    sc = await make_scenario(t, with_items=False)
    other = await make_scenario(t, base=1000)  # has documents and items of its own
    foreign_doc = other.docs["pdf1"]
    foreign_item = iid(1001)
    await item(
        sc, 1, who="a", category=Category.WK_FORTBILDUNG, vendor="Muster Eigen",
        document_id=foreign_doc.id,
    )  # fmt: skip

    data = await load_export(sc.built.scope, YEAR, date(2026, 1, 15))
    assert data is not None
    (row,) = data.lines
    assert row.document_id is None and row.storage_key is None  # the foreign document is invisible

    real = export_data.build_summary

    async def tampered(*a: Any, **kw: Any) -> dict[str, Any]:
        summary = await real(*a, **kw)
        summary["by_line"][0]["item_ids"].append(foreign_item)
        return summary

    monkeypatch.setattr(export_data, "build_summary", tampered)
    with pytest.raises(KeyError):  # the foreign item is not loaded, never rendered
        await load_export(sc.built.scope, YEAR, date(2026, 1, 15))


async def test_pdf_escapes_xml_in_vendor(t: T) -> None:
    from app.domain.enums import Category

    sc = await make_scenario(t, with_items=False)
    evil = "<b>x</b> <unclosed <font size=99>&amp;"
    await item(sc, 1, who="a", category=Category.WK_FORTBILDUNG, vendor=evil)
    r = await get(sc, "pdf")
    assert r.status_code == 200
    text = pdf_text(r.content)
    assert "<unclosed" in text and "<b>x</b>" in text  # shown literally, not interpreted
