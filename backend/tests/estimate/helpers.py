"""Builds a household from a scenario spec (fictional data) on the rolled-back test connection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    ChildYear,
    Document,
    Employment,
    Household,
    Lohnersatz,
    PersonVorsorge,
    TaxProfile,
)
from app.db.scope import HouseholdScope
from app.domain.enums import (
    AllowanceShare,
    AttentionReason,
    Bundesland,
    Category,
    DocumentStatus,
    FilingStatus,
    PaymentMethod,
    PersonKind,
    Religion,
    Steuerklasse,
)
from app.estimate.service import build_summary
from tests.domain.factories import (
    make_document,
    make_household,
    make_person,
    make_tax_item,
    make_user,
)

SCENARIOS = Path(__file__).resolve().parent / "scenarios"
TODAY = date(2026, 3, 1)  # before every 2025 deadline
MONEY_FIELDS = (
    "gross_wage",
    "lohnsteuer",
    "soli_withheld",
    "kirchensteuer_withheld",
    "rv_employee",
    "rv_employer",
    "basisrente",
    "kv",
    "pv",
    "sonstige_vorsorge",
)


def load_yaml(name: str) -> Any:
    return yaml.safe_load((SCENARIOS / name).read_text(encoding="utf-8"))


@dataclass
class Built:
    session: AsyncSession
    household: Household
    ids: dict[str, str]  # spec key -> person id
    year: int

    @property
    def scope(self) -> HouseholdScope:
        return HouseholdScope(self.session, self.household.id)

    async def summary(self, today: date = TODAY) -> dict[str, Any]:
        return await build_summary(self.scope, self.year, today)


async def add_item(b: Built, spec: dict[str, Any], document_id: Any = None) -> Any:
    who = spec.get("person")
    deductible = Decimal(spec["deductible"])
    return await make_tax_item(
        b.session,
        b.household,
        year=b.year,
        category=Category(spec["category"]),
        gross_amount=Decimal(spec.get("gross", spec["deductible"])),
        deductible_amount=deductible,
        labour_share_35a=Decimal(spec["labour_35a"]) if "labour_35a" in spec else None,
        payment_method=PaymentMethod(spec.get("payment_method", "unknown")),
        person_id=None if who is None else b.ids[who],
        is_relevant=spec.get("relevant", True),
        overridden_by_user=spec.get("overridden", False),
        document_id=document_id,
    )


async def add_employment(b: Built, spec: dict[str, Any]) -> Employment:
    row = Employment(
        household_id=b.household.id,
        person_id=b.ids[spec["person"]],
        year=b.year,
        employer_name="Muster GmbH",
        steuerklasse=Steuerklasse(spec["steuerklasse"]),
        has_factor=spec.get("has_factor", False),
        commute_km=spec.get("commute_km"),
        office_days=spec.get("office_days", 0),
        homeoffice_days=spec.get("homeoffice_days", 0),
        **{k: Decimal(spec[k]) for k in MONEY_FIELDS if k in spec},
    )
    b.session.add(row)
    await b.session.flush()
    return row


async def build(session: AsyncSession, spec: dict[str, Any], year: int = 2025) -> Built:
    household = await make_household(session)
    await make_user(session, household, email=f"u-{household.id}@example.com")
    ids: dict[str, str] = {}
    people = {}
    for key, p in spec["persons"].items():
        kind = PersonKind(p["kind"])
        person = await make_person(
            session,
            household,
            kind=kind,
            first_name=f"Muster{key}",
            dob=date.fromisoformat(p["dob"]) if "dob" in p else None,
            religion=Religion(p.get("religion", "none")),
        )
        ids[key] = str(person.id)
        people[key] = person
    prof = spec["profile"]
    session.add(
        TaxProfile(
            household_id=household.id,
            year=year,
            filing_status=FilingStatus(prof["filing"]),
            bundesland=Bundesland(prof["bundesland"]),
            taxpayer_person_id=people[prof["taxpayer"]].id,
            spouse_person_id=people[prof["spouse"]].id if "spouse" in prof else None,
        )
    )
    await session.flush()
    b = Built(session, household, ids, year)
    for e in spec.get("employments", []):
        await add_employment(b, e)
    for c in spec.get("child_years", []):
        session.add(
            ChildYear(
                household_id=household.id,
                person_id=people[c["person"]].id,
                year=year,
                months=c["months"],
                allowance_share=AllowanceShare(c["share"]),
            )
        )
    for ls in spec.get("lohnersatz", []):
        session.add(
            Lohnersatz(
                household_id=household.id,
                person_id=people[ls["person"]].id,
                year=year,
                amount=Decimal(ls["amount"]),
            )
        )
    for pv in spec.get("person_vorsorge", []):
        session.add(
            PersonVorsorge(
                household_id=household.id,
                person_id=people[pv["person"]].id,
                year=year,
                **{k: Decimal(v) for k, v in pv.items() if k != "person"},
            )
        )
    for item in spec.get("items", []):
        await add_item(b, item)
    await session.flush()
    return b


async def needs_attention_document(b: Built, reason: AttentionReason) -> Document:
    user = (await b.session.execute(b.scope.select(_user()))).scalars().first()
    assert user is not None
    return await make_document(
        b.session,
        b.household,
        user,
        status=DocumentStatus.NEEDS_ATTENTION,
        attention_reason=reason,
    )


def _user() -> Any:
    from app.db.models import AppUser

    return AppUser
