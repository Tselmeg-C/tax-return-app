"""Text snapshots of the CSV and the PDF for two fixed scenarios (#21).

Update intentionally: `UPDATE_SNAPSHOTS=1 uv run pytest tests/export/test_snapshots.py`.
The params version (a hash of the params file) is masked, everything else is exact.
"""

from __future__ import annotations

import io
import os
import re
from datetime import date
from pathlib import Path

from pypdf import PdfReader

from app.domain.enums import Category
from app.export.csv_file import csv_bytes
from app.export.data import load_export
from app.export.pdf import pdf_bytes
from tests.api.conftest import T
from tests.export.scenario import item, make_scenario

SNAPSHOTS = Path(__file__).parent / "snapshots"
SINGLE = {
    "persons": {"a": {"kind": "adult", "dob": "1985-05-05"}},
    "profile": {"filing": "single", "bundesland": "be", "taxpayer": "a"},
    "employments": [
        {
            "person": "a",
            "steuerklasse": "1",
            "commute_km": 10,
            "office_days": 100,
            "homeoffice_days": 40,
            "gross_wage": "52000.00",
            "lohnsteuer": "9800.00",
            "rv_employee": "4836.00",
            "kv": "4000.00",
            "pv": "900.00",
        }
    ],
}


def check(name: str, text: str) -> None:
    text = re.sub(r"2025-[0-9a-f]{12}", "2025-HASH", text)
    path = SNAPSHOTS / name
    if os.environ.get("UPDATE_SNAPSHOTS"):
        path.write_text(text, encoding="utf-8")
    assert text == path.read_text(encoding="utf-8"), f"{name} differs (UPDATE_SNAPSHOTS=1)"


def pdf_text(raw: bytes) -> str:
    return "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(raw)).pages) + "\n"


async def render(sc: object, today: date) -> tuple[str, str]:
    data = await load_export(sc.built.scope, 2025, today)  # type: ignore[attr-defined]
    assert data is not None
    return csv_bytes(data).decode("utf-8-sig").replace("\r\n", "\n"), pdf_text(pdf_bytes(data))


async def test_joint_household_snapshot(t: T) -> None:
    sc = await make_scenario(t)
    csv_text, pdf = await render(sc, date(2026, 1, 15))
    check("joint_2025.csv.txt", csv_text)
    check("joint_2025.pdf.txt", pdf)


async def test_single_overdue_snapshot(t: T) -> None:
    sc = await make_scenario(t, spec=SINGLE)
    await item(sc, 1, who="a", category=Category.WK_FAHRTKOSTEN, vendor="Muster Bahn",
               invoice_date=date(2025, 5, 5), gross_amount=100, deductible_amount=100)  # fmt: skip
    await item(sc, 2, who="a", category=Category.WK_BERUFSVERBAND, vendor="Muster Gewerkschaft",
               invoice_date=date(2025, 1, 31), gross_amount=60, deductible_amount=60)  # fmt: skip
    csv_text, pdf = await render(sc, date(2030, 1, 1))
    check("single_2025.csv.txt", csv_text)
    check("single_2025.pdf.txt", pdf)
    assert "(Frist abgelaufen)" in pdf
