"""Fuzzy duplicate check (#9): same normalised vendor, gross amount and date as an item of
another document in the household. Exact duplicates (same bytes) never get here (#6)."""

from __future__ import annotations

import re
import uuid
from datetime import date
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Document, TaxItem
from app.db.scope import HouseholdScope

BERLIN = ZoneInfo("Europe/Berlin")
_LEGAL_FORMS = re.compile(r"\b(gmbh|ag|e\.\s?k\.?|ek|kg|ug|e\.\s?v\.?|gbr|ohg|mbh|co)(?=\s|$|\W)")


def normalise_vendor(vendor: str | None) -> str:
    text = (vendor or "").casefold()
    text = _LEGAL_FORMS.sub(" ", text)
    text = re.sub(r"[^\w\s]", " ", text)
    return " ".join(text.split())


def _same_date(
    a_invoice: date | None, a_payment: date | None, b_invoice: date | None, b_payment: date | None
) -> bool:
    if a_invoice is not None and b_invoice is not None:
        return a_invoice == b_invoice
    return a_payment is not None and a_payment == b_payment


async def find_duplicate(
    session: AsyncSession,
    household_id: uuid.UUID,
    document_id: uuid.UUID,
    *,
    vendor: str | None,
    gross_amount: Decimal,
    invoice_date: date | None,
    payment_date: date | None,
) -> date | None:
    """The other document's upload date (Europe/Berlin date of `created_at`), or None."""
    key = normalise_vendor(vendor)
    if not key:
        return None
    scope = HouseholdScope(session, household_id)
    rows = await session.execute(
        scope.select(TaxItem)
        .where(
            TaxItem.document_id.is_not(None),
            TaxItem.document_id != document_id,
            TaxItem.gross_amount == gross_amount,
        )
        .add_columns(Document.created_at)
        .join(Document, Document.id == TaxItem.document_id)
    )
    for item, created_at in rows.all():
        if normalise_vendor(item.vendor) == key and _same_date(
            invoice_date, payment_date, item.invoice_date, item.payment_date
        ):
            uploaded: date = created_at.astimezone(BERLIN).date()
            return uploaded
    return None
