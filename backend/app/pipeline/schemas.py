"""LLM output schemas of the pipeline (#9 spec). Pure: pydantic + enums only.

Money is a string with exactly two decimals (`^-?\\d{1,9}\\.\\d{2}$`), converted with
`money()`; no float anywhere (confidence is an enum). Free text is capped in code (an
AfterValidator), not with `maxLength`, so the strict JSON schema stays within what every
provider accepts. Changing anything here is an LLM schema change: new prompt version
(`prompts/vN/`) and an eval run.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from app.domain.enums import Category, DocType, PaymentMethod

MONEY_PATTERN = r"^-?\d{1,9}\.\d{2}$"
CURRENCY_PATTERN = r"^[A-Z]{3}$"


def _cap(n: int) -> AfterValidator:
    return AfterValidator(lambda value: value[:n] if isinstance(value, str) else value)


Money = Annotated[str, Field(pattern=MONEY_PATTERN)]
Currency = Annotated[str, Field(pattern=CURRENCY_PATTERN)]
Name = Annotated[str, _cap(200)]
Reason = Annotated[str, _cap(300)]
Description = Annotated[str, _cap(120)]


def money(value: str) -> Decimal:
    return Decimal(value)


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


CONFIDENCE_VALUE: dict[Confidence, Decimal] = {
    Confidence.HIGH: Decimal("0.900"),
    Confidence.MEDIUM: Decimal("0.600"),
    Confidence.LOW: Decimal("0.300"),
}
"""Stored in `extraction.confidence` / `tax_item.confidence`."""


class CostKind35a(StrEnum):
    LABOUR = "labour"
    TRAVEL = "travel"
    MACHINE = "machine"
    MATERIAL = "material"
    OTHER = "other"
    NOT_APPLICABLE = "not_applicable"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ClassifyOutput(_Strict):
    """Task `classify` (cheap model)."""

    doc_type: DocType
    tax_relevant: bool
    readable: bool
    multiple_documents: bool
    total_gross: Money | None
    currency: Currency | None
    document_date: date | None
    vendor: Name | None
    # Added in v1 (listed in the PR): the year an official certificate / Bescheid covers
    # ("Bescheinigungsjahr"); null for bills. The eval's tax_year for official documents.
    certificate_year: int | None
    reason_de: Reason
    confidence: Confidence


class LineItem(_Strict):
    description: Description
    gross_amount: Money
    category: Category
    cost_kind_35a: CostKind35a


class GenericBillExtraction(_Strict):
    """Task `extract` (strong model), only for relevant, readable single `generic_bill`s."""

    vendor: Name | None
    recipient_name: Name | None
    invoice_date: date | None
    payment_date: date | None
    payment_method: PaymentMethod
    currency: Currency
    total_gross: Money
    total_vat: Money | None
    is_credit_note: bool
    line_items: list[LineItem] = Field(min_length=1, max_length=60)
    stated_labour_amount_35a: Money | None
    reason_de: Reason
    confidence: Confidence
