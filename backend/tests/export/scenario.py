"""A fixed fictional household for the export tests (#21): fixed item ids, "Muster" vendors,
no Steuer-ID, originals generated in code. Persons carry sentinel names that must never reach
an export."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from app.db.models import Document
from app.domain.enums import AttentionReason, Category, DocumentStatus, PaymentMethod
from tests.api.conftest import Home, T
from tests.documents import files
from tests.estimate.helpers import Built, build
from tests.household.steuer_ids import generate_steuer_id

NAME_SENTINEL = "Zorbaxia"  # in every person name
FILE_SENTINEL = "Geheimname"  # in every original_filename
EMPLOYER_SENTINEL = "Employer-Quux"

SPEC: dict[str, Any] = {
    "persons": {
        "a": {"kind": "adult", "religion": "ev", "dob": "1985-05-05"},
        "b": {"kind": "adult", "dob": "1986-06-06"},
        "c": {"kind": "child", "dob": "2018-03-03"},
        "x": {"kind": "adult"},  # not in the return
    },
    "profile": {"filing": "joint", "bundesland": "by", "taxpayer": "a", "spouse": "b"},
    "child_years": [{"person": "c", "months": 12, "share": "full"}],
    "employments": [
        {
            "person": "a",
            "steuerklasse": "3",
            "gross_wage": "60000.00",
            "lohnsteuer": "4800.00",
            "kirchensteuer_withheld": "384.00",
            "rv_employee": "5580.00",
            "kv": "4200.00",
            "pv": "1000.00",
        },
        {
            "person": "b",
            "steuerklasse": "5",
            "gross_wage": "24000.00",
            "lohnsteuer": "5600.00",
            "rv_employee": "2232.00",
            "kv": "1700.00",
            "pv": "400.00",
        },
    ],
}


def iid(n: int) -> str:
    return f"{n:08x}-0000-4000-8000-000000000000"


@dataclass
class Scenario:
    t: T
    home: Home
    built: Built
    docs: dict[str, Document] = field(default_factory=dict)
    contents: dict[str, bytes] = field(default_factory=dict)
    steuer_ids: list[str] = field(default_factory=list)
    base: int = 0  # id offset (a second household needs other ids)

    def ids(self, key: str) -> str:
        return self.built.ids[key]


async def upload(t: T, home: Home, content: bytes, filename: str) -> uuid.UUID:
    from urllib.parse import quote

    r = await t.api.request(
        "POST",
        "/documents",
        content=content,
        cookie=home.cookie,
        headers={
            "Content-Type": "application/octet-stream",
            "X-Filename": "UTF-8''" + quote(filename),
        },
    )
    assert r.status_code == 201, r.text
    return uuid.UUID(r.json()["document"]["id"])


async def make_scenario(
    t: T, *, with_items: bool = True, base: int = 0, spec: dict[str, Any] | None = None
) -> Scenario:
    custom = spec is not None
    built = await build(t.session, spec or SPEC)
    # Name sentinel on every person and employer (must never be exported).
    from app.db.models import Employment, Person

    steuer_ids: list[str] = []
    for person in (
        await t.session.execute(select(Person).where(Person.household_id == built.household.id))
    ).scalars():
        person.first_name = f"{NAME_SENTINEL}{person.first_name}"
        person.last_name = NAME_SENTINEL
        person.steuer_id = generate_steuer_id()
        steuer_ids.append(person.steuer_id)
    for job in (
        await t.session.execute(
            select(Employment).where(Employment.household_id == built.household.id)
        )
    ).scalars():
        job.employer_name = EMPLOYER_SENTINEL
    await t.session.commit()
    home = await t.home(built.household)
    sc = Scenario(t, home, built, steuer_ids=steuer_ids, base=base)
    if with_items and not custom:
        await fill(sc)
    return sc


async def doc(sc: Scenario, key: str, content: bytes, **kw: Any) -> Document:
    doc_id = await upload(sc.t, sc.home, content, f"{FILE_SENTINEL}-{key}.pdf")
    row = (await sc.t.session.execute(select(Document).where(Document.id == doc_id))).scalar_one()
    for k, v in kw.items():
        setattr(row, k, v)
    await sc.t.session.commit()
    sc.docs[key] = row
    sc.contents[key] = content
    return row


async def item(sc: Scenario, n: int, **kw: Any) -> Any:
    from tests.domain.factories import make_tax_item

    who = kw.pop("who", None)
    document = kw.pop("doc", None)
    values: dict[str, Any] = {
        "id": uuid.UUID(iid(sc.base + n)),
        "year": 2025,
        "person_id": None if who is None else uuid.UUID(sc.ids(who)),
        "document_id": None if document is None else sc.docs[document].id,
    }
    values.update(kw)
    row = await make_tax_item(sc.t.session, sc.built.household, **values)
    await sc.t.session.commit()
    return row


def money(value: str) -> Decimal:
    return Decimal(value)


async def fill(sc: Scenario) -> None:
    await doc(sc, "pdf1", files.pdf())
    await doc(sc, "jpg1", files.jpeg())
    await doc(sc, "png1", files.png())
    await doc(sc, "pdf2", files.pdf(1))
    await doc(sc, "pdf3", files.pdf(2))
    await doc(
        sc,
        "bad",
        files.pdf(3),
        status=DocumentStatus.NEEDS_ATTENTION,
        attention_reason=AttentionReason.IMPLAUSIBLE_AMOUNT,
    )
    await doc(
        sc,
        "unrev",
        files.pdf(4),
        status=DocumentStatus.NEEDS_ATTENTION,
        attention_reason=AttentionReason.LOW_CONFIDENCE,
    )
    d = date
    await item(sc, 1, who="a", doc="pdf1", category=Category.WK_ARBEITSMITTEL,
               vendor='Müller & Söhne; "Büro"', invoice_date=d(2025, 3, 4),
               gross_amount=money("150.50"), deductible_amount=money("120.50"))  # fmt: skip
    await item(sc, 2, who="a", doc="pdf1", category=Category.WK_FORTBILDUNG,
               vendor="Muster Verlag", invoice_date=d(2025, 3, 4),
               gross_amount=money("80.00"), deductible_amount=money("80.00"))  # fmt: skip
    await item(sc, 3, doc="jpg1", category=Category.HANDWERKERLEISTUNG,
               vendor='=HYPERLINK("http://x")', invoice_date=d(2025, 6, 1),
               payment_method=PaymentMethod.BANK_TRANSFER,
               gross_amount=money("500.00"), deductible_amount=money("500.00"),
               labour_share_35a=money("400.00"))  # fmt: skip
    await item(sc, 4, who="a", doc="png1", category=Category.KAPITAL_BESCHEINIGUNG,
               vendor="Muster Bank", invoice_date=d(2025, 12, 31),
               gross_amount=money("40.00"), deductible_amount=money("40.00"))  # fmt: skip
    await item(sc, 5, who="b", category=Category.SPENDEN, vendor="Muster Verein",
               payment_date=d(2025, 11, 2),
               gross_amount=money("25.00"), deductible_amount=money("25.00"))  # fmt: skip
    await item(sc, 6, who="a", doc="pdf2", category=Category.WK_ARBEITSMITTEL,
               vendor="Muster Irrelevant", is_relevant=False,
               gross_amount=money("9.00"), deductible_amount=money("0.00"))  # fmt: skip
    await item(sc, 7, who="x", doc="pdf3", category=Category.WK_ARBEITSMITTEL,
               vendor="Muster Fremdperson",
               gross_amount=money("9.00"), deductible_amount=money("9.00"))  # fmt: skip
    await item(sc, 8, who="a", doc="bad", category=Category.WK_ARBEITSMITTEL,
               vendor="Muster Ausgeschlossen",
               gross_amount=money("30.00"), deductible_amount=money("30.00"))  # fmt: skip
    await item(sc, 9, who="a", doc="unrev", category=Category.WK_BEWERBUNG,
               vendor="Muster Karriere", invoice_date=d(2025, 2, 2),
               gross_amount=money("15.00"), deductible_amount=money("15.00"))  # fmt: skip
    await item(sc, 10, who="a", category=Category.WK_DOPPELTE_HAUSHALTSFUEHRUNG,
               vendor="Muster Zweitwohnung", invoice_date=d(2025, 1, 15),
               gross_amount=money("100.00"), deductible_amount=money("100.00"))  # fmt: skip
    await item(sc, 11, who="c", category=Category.KRANKHEITSKOSTEN, vendor="Muster Apotheke",
               invoice_date=d(2025, 4, 1), payment_method=PaymentMethod.CARD,
               gross_amount=money("60.00"), deductible_amount=money("60.00"))  # fmt: skip
    await item(sc, 12, who="a", doc="pdf1", year=2024, category=Category.WK_ARBEITSMITTEL,
               vendor="Muster Vorjahr",
               gross_amount=money("5.00"), deductible_amount=money("5.00"))  # fmt: skip
