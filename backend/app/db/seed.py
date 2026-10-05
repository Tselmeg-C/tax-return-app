"""Dev seed: `uv run python -m app.db.seed`.

Mirrors the *shape* of `frontend/src/lib/mock.ts` with fictional people (household
"Musterhaushalt"). Idempotent (fixed UUIDs), refuses to run with `APP_ENV=production`, writes
no audit rows and prints counts only (never names or e-mails). `SEED_OWNER_EMAIL` overrides
the owner's e-mail so #5's login can be used locally. No Steuer-ID is seeded, so no
`FIELD_ENCRYPTION_KEY` is needed. Zeile values are illustrative, not tax-correct.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import sys
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import AppUser, Document, Household, Person, TaxItem
from app.db.session import create_engine, create_sessionmaker
from app.domain.enums import (
    Anlage,
    Category,
    Channel,
    DocType,
    DocumentStatus,
    PersonKind,
    UserRole,
)

_NAMESPACE = uuid.UUID("5eed0000-0000-4000-8000-000000000000")
DEFAULT_OWNER_EMAIL = "owner@example.com"
MEMBER_EMAIL = "member@example.com"
ALREADY_PRESENT = "seed: already present, nothing to do"


def seed_id(name: str) -> uuid.UUID:
    """Deterministic id for a seed row, so re-runs detect existing data."""
    return uuid.uuid5(_NAMESPACE, name)


HOUSEHOLD_ID = seed_id("household")
ALEX, SAM, KIM = seed_id("person/alex"), seed_id("person/sam"), seed_id("person/kim")
OWNER, MEMBER = seed_id("user/owner"), seed_id("user/member")


@dataclass(frozen=True)
class _Item:
    vendor: str
    invoice_date: date
    gross: str
    deductible: str
    labour_35a: str | None
    category: Category
    anlage: Anlage | None
    zeile: str | None
    person: uuid.UUID | None  # None = household-level ("Haushalt" in the mock)
    channel: Channel
    confidence: str
    relevant: bool = True
    overridden: bool = False
    doc_type: DocType = DocType.GENERIC_BILL


_ITEMS: tuple[_Item, ...] = (
    _Item("Malerbetrieb Huber", date(2025, 3, 14), "1312.40", "720.00", "720.00",
          Category.HANDWERKERLEISTUNG, Anlage.HAUSHALTSNAHE_AUFWENDUNGEN, "7",
          None, Channel.TELEGRAM, "0.94"),
    _Item("Kita Sonnenschein", date(2025, 12, 31), "2160.00", "1440.00", None,
          Category.KINDERBETREUUNG, Anlage.KIND, "64", KIM, Channel.WEB, "0.98"),
    _Item("Apple Store", date(2025, 5, 2), "1499.00", "1499.00", None,
          Category.WK_ARBEITSMITTEL, Anlage.N, "46", ALEX, Channel.WEB, "0.71",
          overridden=True),
    _Item("ADAC Versicherung", date(2025, 1, 15), "389.50", "389.50", None,
          Category.VORSORGE_SONSTIGE, Anlage.VORSORGEAUFWAND, "49", None, Channel.TELEGRAM,
          "0.88"),
    _Item("Zahnarztpraxis Dr. Lenz", date(2025, 8, 22), "860.00", "860.00", None,
          Category.KRANKHEITSKOSTEN, Anlage.AGB, "4", SAM, Channel.TELEGRAM, "0.90"),
    _Item("Comdirect", date(2026, 2, 10), "412.30", "412.30", None,
          Category.KAPITAL_BESCHEINIGUNG, Anlage.KAP, "7", ALEX, Channel.WEB, "0.97",
          doc_type=DocType.JAHRESSTEUERBESCHEINIGUNG),
    _Item("Gebäudereinigung Blitz", date(2025, 11, 30), "540.00", "540.00", "540.00",
          Category.HAUSHALTSNAHE_DIENSTLEISTUNG, Anlage.HAUSHALTSNAHE_AUFWENDUNGEN, "4",
          None, Channel.WEB, "0.62"),
    _Item("REWE", date(2025, 6, 3), "54.12", "0.00", None,
          Category.IRRELEVANT, None, None, None, Channel.TELEGRAM, "0.99", relevant=False),
    _Item("Verdi", date(2025, 12, 1), "312.00", "312.00", None,
          Category.WK_BERUFSVERBAND, Anlage.N, "41", SAM, Channel.WEB, "0.95"),
)  # fmt: skip

TAX_YEAR = 2025  # the Jahressteuerbescheinigung dated 2026-02 belongs to tax year 2025


async def seed(session: AsyncSession, *, owner_email: str = DEFAULT_OWNER_EMAIL) -> dict[str, int]:
    """Insert the seed rows (no commit). Returns row counts; empty if already present."""
    if await session.get(Household, HOUSEHOLD_ID) is not None:
        return {}

    hh = HOUSEHOLD_ID
    session.add(Household(id=hh, name="Musterhaushalt"))
    await session.flush()
    persons = [
        Person(id=ALEX, household_id=hh, kind=PersonKind.ADULT, first_name="Alex",
               last_name="Muster"),
        Person(id=SAM, household_id=hh, kind=PersonKind.ADULT, first_name="Sam",
               last_name="Muster"),
        Person(id=KIM, household_id=hh, kind=PersonKind.CHILD, first_name="Kim",
               last_name="Muster", dob=date(2019, 1, 1)),
    ]  # fmt: skip
    session.add_all(persons)
    await session.flush()
    users = [
        AppUser(id=OWNER, household_id=hh, email=owner_email, role=UserRole.OWNER,
                person_id=ALEX),
        AppUser(id=MEMBER, household_id=hh, email=MEMBER_EMAIL, role=UserRole.MEMBER,
                person_id=SAM),
    ]  # fmt: skip
    session.add_all(users)
    await session.flush()

    documents: list[Document] = []
    items: list[TaxItem] = []
    for n, spec in enumerate(_ITEMS, start=1):
        doc = Document(
            id=seed_id(f"document/{n}"),
            household_id=hh,
            uploaded_by_user_id=MEMBER if spec.person == SAM else OWNER,
            channel=spec.channel,
            sha256=hashlib.sha256(f"seed/{n}".encode()).hexdigest(),
            mime_type="application/pdf",
            size_bytes=1024 * n,
            page_count=1,
            storage_key=f"seed/{n}",  # no real file exists
            status=DocumentStatus.DONE,
            doc_type=spec.doc_type,
        )
        documents.append(doc)
        items.append(
            TaxItem(
                id=seed_id(f"tax_item/{n}"),
                household_id=hh,
                document_id=doc.id,
                person_id=spec.person,
                year=TAX_YEAR,
                category=spec.category,
                anlage=spec.anlage,
                zeile=spec.zeile,
                gross_amount=Decimal(spec.gross),
                deductible_amount=Decimal(spec.deductible),
                labour_share_35a=Decimal(spec.labour_35a) if spec.labour_35a else None,
                vendor=spec.vendor,
                invoice_date=spec.invoice_date,
                is_relevant=spec.relevant,
                confidence=Decimal(spec.confidence),
                overridden_by_user=spec.overridden,
            )
        )
    session.add_all(documents)
    await session.flush()
    session.add_all(items)
    await session.flush()
    return {
        "household": 1,
        "app_user": len(users),
        "person": len(persons),
        "document": len(documents),
        "tax_item": len(items),
    }


def owner_email_from_env() -> str:
    return os.environ.get("SEED_OWNER_EMAIL", "").strip() or DEFAULT_OWNER_EMAIL


async def _run() -> int:
    settings = get_settings()
    if settings.app_env == "production":
        print("seed: refusing to run with APP_ENV=production", file=sys.stderr)
        return 2
    owner_email = owner_email_from_env()
    engine = create_engine(settings)
    try:
        async with create_sessionmaker(engine)() as session, session.begin():
            counts = await seed(session, owner_email=owner_email)
    finally:
        await engine.dispose()
    if not counts:
        print(ALREADY_PRESENT)
    else:
        print("seed: created " + " ".join(f"{k}={v}" for k, v in counts.items()))
    return 0


def main() -> int:
    try:
        return asyncio.run(_run())
    except Exception as exc:
        # Class name only: messages can contain row values or connection details.
        print(f"seed: failed ({type(exc).__name__})", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
