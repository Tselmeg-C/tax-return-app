"""What the three export formats render: `build_summary` plus the receipt rows of its `by_line`.

No number is computed here: sums come from the summary (`by_anlage`, `by_line`), the rows only
add the display facts of the counted items (vendor, date, payment method, document). Persons
appear as role labels, never by name; the Steuer-ID is never loaded.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from app.db.models import Document, Person, TaxItem, TaxProfile
from app.db.scope import HouseholdScope
from app.domain.enums import (
    CATEGORY_GROUP,
    Anlage,
    Category,
    CategoryGroup,
    DocumentStatus,
    FilingStatus,
    PaymentMethod,
    PersonKind,
)
from app.estimate.service import build_summary
from app.tax_params import load_params

NOT_COMPUTED_GROUPS = frozenset({CategoryGroup.KAPITAL, CategoryGroup.VERMIETUNG})
VENDOR_MAX = 60
_ANLAGE_ORDER = {a.value: i for i, a in enumerate(Anlage)}


def de_money(value: str | Decimal) -> str:
    """`1234.5` -> `1.234,50` (PDF display)."""
    d = Decimal(value)
    whole, _, frac = f"{abs(d):.2f}".partition(".")
    grouped = re.sub(r"(?<=\d)(?=(\d{3})+$)", ".", whole)
    return f"{'-' if d < 0 else ''}{grouped},{frac}"


def de_plain(value: str | Decimal) -> str:
    """`1234.5` -> `1234,50` (CSV: no thousands separator)."""
    return f"{Decimal(value):.2f}".replace(".", ",")


def de_date(value: date) -> str:
    return value.strftime("%d.%m.%Y")


def zeile_key(zeile: str | None) -> tuple[int, int, str]:
    """Numeric-aware: numbers first, other text next, no Zeile last."""
    if zeile is None:
        return (2, 0, "")
    return (0, int(zeile), "") if zeile.isdigit() else (1, 0, zeile)


@dataclass(frozen=True)
class Line:
    """One counted receipt row (a `tax_item`)."""

    item_id: str
    beleg_nr: str  # first 8 characters of the item id
    anlage: str
    zeile: str | None
    category: Category
    person: str  # role label
    document_id: str | None
    day: date | None
    vendor: str | None
    payment: PaymentMethod
    gross: Decimal
    deductible: Decimal
    labour_35a: Decimal | None
    unreviewed: bool
    not_computed: bool  # Kapital / Vermietung: listed, not in the estimate
    mime_type: str | None
    storage_key: str | None
    size_bytes: int


@dataclass(frozen=True)
class Group:
    """A `by_line` entry (one category line of an Anlage) with its rows."""

    anlage: str
    zeile: str | None
    category: Category
    amount: str
    source: str
    lines: tuple[Line, ...]

    @property
    def not_computed(self) -> bool:
        return CATEGORY_GROUP[self.category] in NOT_COMPUTED_GROUPS


@dataclass(frozen=True)
class ExportData:
    summary: dict[str, Any]
    filing: FilingStatus
    groups: tuple[Group, ...]
    roles: dict[str, str]  # person id -> role label (also for the notes)
    today: date

    @property
    def year(self) -> int:
        return int(self.summary["year"])

    @property
    def lines(self) -> list[Line]:
        """All rows in export order: Anlage order, Zeile, date, item id."""
        rows = [line for g in self.groups for line in g.lines]
        return sorted(
            rows,
            key=lambda r: (
                _ANLAGE_ORDER[r.anlage],
                zeile_key(r.zeile),
                r.day or date.max,
                r.item_id,
            ),
        )


def beleg_nr(item_id: str) -> str:
    return item_id[:8]


async def _roles(scope: HouseholdScope, profile: TaxProfile) -> dict[str, str]:
    roles = {str(profile.taxpayer_person_id): "Steuerpflichtige/r (Person 1)"}
    if profile.spouse_person_id is not None:
        roles[str(profile.spouse_person_id)] = "Ehepartner/in (Person 2)"
    kids = (
        (await scope.session.execute(scope.select(Person).where(Person.kind == PersonKind.CHILD)))
        .scalars()
        .all()
    )
    for n, kid in enumerate(sorted(kids, key=lambda p: str(p.id)), start=1):
        roles[str(kid.id)] = f"Kind {n}"
    return roles


async def load_export(scope: HouseholdScope, year: int, today: date) -> ExportData | None:
    """`None` when the year has no profile. `UnsupportedTaxYear` is raised as is."""
    summary = await build_summary(scope, year, today)
    if summary["status"] != "ok":
        return None
    session = scope.session
    profile = (
        await session.execute(scope.select(TaxProfile).where(TaxProfile.year == year))
    ).scalar_one()
    roles = await _roles(scope, profile)
    ids = [uuid.UUID(i) for g in summary["by_line"] for i in g["item_ids"]]
    items = {
        str(i.id): i
        for i in (await session.execute(scope.select(TaxItem).where(TaxItem.id.in_(ids))))
        .scalars()
        .all()
    }
    doc_ids = {i.document_id for i in items.values() if i.document_id is not None}
    docs = {
        d.id: d
        for d in (await session.execute(scope.select(Document).where(Document.id.in_(doc_ids))))
        .scalars()
        .all()
    }
    mapping = load_params(year).mapping
    groups: list[Group] = []
    for entry in summary["by_line"]:
        category = Category(entry["category"])
        lines = []
        for item_id in entry["item_ids"]:
            item = items[item_id]
            doc = docs.get(item.document_id) if item.document_id is not None else None
            lines.append(
                Line(
                    item_id=item_id,
                    beleg_nr=beleg_nr(item_id),
                    anlage=entry["anlage"],
                    zeile=entry["zeile"],
                    category=category,
                    person=roles.get(str(item.person_id), "Haushalt")
                    if item.person_id is not None
                    else "Haushalt",
                    document_id=None if doc is None else str(doc.id),
                    day=item.invoice_date or item.payment_date,
                    vendor=item.vendor,
                    payment=item.payment_method,
                    gross=item.gross_amount,
                    deductible=item.deductible_amount,
                    labour_35a=item.labour_share_35a,
                    unreviewed=(
                        not item.overridden_by_user
                        and doc is not None
                        and doc.status is DocumentStatus.NEEDS_ATTENTION
                    ),
                    not_computed=CATEGORY_GROUP[category] in NOT_COMPUTED_GROUPS,
                    mime_type=None if doc is None else doc.mime_type,
                    storage_key=None if doc is None else doc.storage_key,
                    size_bytes=0 if doc is None else doc.size_bytes,
                )
            )
        groups.append(
            Group(
                anlage=entry["anlage"],
                zeile=entry["zeile"],
                category=category,
                amount=entry["amount"],
                source=mapping[category].source,
                lines=tuple(lines),
            )
        )
    return ExportData(summary, profile.filing_status, tuple(groups), roles, today)
