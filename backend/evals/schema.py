"""Eval schemas: label (`ExpectedLabel`), prediction (`Prediction`, `CallUsage`) and report.

Vocabulary comes from `app.domain.enums` (no copies). Money is `Decimal`, never float: label
amounts are strings with exactly two decimals; predicted amounts may be any decimal string.
Validation errors use custom error types (the "rule") and never echo the input value.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    model_validator,
)
from pydantic_core import PydanticCustomError

from app.domain.enums import CATEGORY_GROUP, Category, CategoryGroup, DocType, PaymentMethod

LABEL_SCHEMA_VERSION = 1
MAX_ABS_AMOUNT = Decimal("1000000")
CASE_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{2,63}$"

OFFICIAL_DOC_TYPES: frozenset[DocType] = frozenset(
    {
        DocType.LOHNSTEUERBESCHEINIGUNG,
        DocType.KINDERGELD_BESCHEID,
        DocType.ELTERNGELD_BESCHEID,
        DocType.ALG_BESCHEID,
    }
)
"""Doc types whose fields are an `official_record` (#18), not a tax item: `category: null`."""


class Source(StrEnum):
    SYNTHETIC = "synthetic"
    ANONYMISED_REAL = "anonymised_real"
    OVERRIDE_EXPORT = "override_export"


class Variant(StrEnum):
    PDF_TEXT = "pdf_text"
    PDF_SCANNED = "pdf_scanned"
    PHOTO_JPEG = "photo_jpeg"
    PNG = "png"


class Tag(StrEnum):
    """Edge-case tags, defined in `evals/LABELLING.md`."""

    CASH_35A = "cash_35a"
    MATERIAL_AND_LABOUR = "material_and_labour"
    MIXED_RECEIPT = "mixed_receipt"
    ABFLUSS_YEAR_BOUNDARY = "abfluss_year_boundary"
    CREDIT_NOTE = "credit_note"
    MULTI_PAGE = "multi_page"
    ENGLISH_LANGUAGE = "english_language"
    DUPLICATE_REPHOTOGRAPHED = "duplicate_rephotographed"
    STEUERBERATUNG_SPLIT = "steuerberatung_split"
    NO_PAYMENT_DATE = "no_payment_date"


VARIANT_EXTENSIONS: dict[Variant, str] = {
    Variant.PDF_TEXT: ".pdf",
    Variant.PDF_SCANNED: ".pdf",
    Variant.PHOTO_JPEG: ".jpg",
    Variant.PNG: ".png",
}

MIME_TYPES: dict[str, str] = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".png": "image/png",
}

_LABEL_MONEY_RE = re.compile(r"^-?[0-9]+\.[0-9]{2}$")
_PRED_MONEY_RE = re.compile(r"^-?[0-9]+(\.[0-9]+)?$")


def _parse_label_money(value: Any) -> Any:
    if value is None or isinstance(value, Decimal):
        return value
    if not isinstance(value, str) or not _LABEL_MONEY_RE.match(value):
        raise PydanticCustomError(
            "money_format",
            "amounts must be quoted decimal strings with exactly two decimals",
        )
    amount = Decimal(value)
    if abs(amount) > MAX_ABS_AMOUNT:
        raise PydanticCustomError("money_range", "amount must be within +/- 1 000 000")
    return amount


def _parse_pred_money(value: Any) -> Any:
    if value is None or isinstance(value, Decimal):
        return value
    if isinstance(value, str) and _PRED_MONEY_RE.match(value):
        try:
            return Decimal(value)
        except InvalidOperation:  # pragma: no cover - regex already guards
            pass
    raise PydanticCustomError("money_format", "amounts must be decimal strings (never float)")


def _money_str(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


LabelMoney = Annotated[
    Decimal,
    BeforeValidator(_parse_label_money),
    PlainSerializer(_money_str, return_type=str),
]
PredMoney = Annotated[
    Decimal,
    BeforeValidator(_parse_pred_money),
    PlainSerializer(_money_str, return_type=str),
]
Money = Annotated[Decimal, PlainSerializer(_money_str, return_type=str)]


def _check_tax_year(value: int | None) -> int | None:
    if value is not None and value not in (2025, 2026):
        raise PydanticCustomError("tax_year_range", "tax_year must be 2025 or 2026")
    return value


class ExpectedFields(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    doc_type: DocType
    tax_relevant: bool
    category: Category | None
    gross_amount: LabelMoney | None
    deductible_amount: LabelMoney
    labour_share_35a: LabelMoney | None
    invoice_date: date | None
    payment_date: date | None
    tax_year: int | None
    payment_method: PaymentMethod
    vendor: str | None
    person_hint: str | None

    @model_validator(mode="after")
    def _rules(self) -> ExpectedFields:
        _check_tax_year(self.tax_year)
        if not self.tax_relevant and self.deductible_amount != 0:
            raise PydanticCustomError(
                "irrelevant_has_deductible",
                "tax_relevant: false requires deductible_amount \"0.00\"",
            )
        if self.category is Category.IRRELEVANT and self.tax_relevant:
            raise PydanticCustomError(
                "irrelevant_category_relevant",
                "category irrelevant requires tax_relevant: false",
            )
        if self.labour_share_35a is not None:
            if self.category is None or CATEGORY_GROUP[self.category] is not (
                CategoryGroup.HAUSHALTSNAHE
            ):
                raise PydanticCustomError(
                    "labour_share_group",
                    "labour_share_35a is only allowed for the haushaltsnahe group",
                )
            gross = abs(self.gross_amount) if self.gross_amount is not None else Decimal(0)
            if not (0 <= self.labour_share_35a <= gross):
                raise PydanticCustomError(
                    "labour_share_range",
                    "labour_share_35a must be between 0 and |gross_amount|",
                )
        if self.category is None and self.doc_type not in OFFICIAL_DOC_TYPES:
            raise PydanticCustomError(
                "category_null",
                "category: null is only allowed for official documents without a tax item",
            )
        return self


class ExpectedLabel(BaseModel):
    """`label.yaml`, schema v1. One document = at most one expected tax item."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    id: str = Field(pattern=CASE_ID_PATTERN)
    source: Source
    file: str
    variant: Variant
    tags: list[Tag] = Field(default_factory=list)
    expected: ExpectedFields
    notes: str | None = None

    @model_validator(mode="after")
    def _file_matches_variant(self) -> ExpectedLabel:
        ext = VARIANT_EXTENSIONS[self.variant]
        if self.file != f"document{ext}":
            raise PydanticCustomError(
                "file_variant",
                "file must be named document{ext} for this variant",
                {"ext": ext},
            )
        return self


class CallUsage(BaseModel):
    """Usage of one LLM call; the same fields as #8's `LLMResult`."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str
    step: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost_eur: PredMoney
    latency_ms: int = Field(ge=0)


class Prediction(BaseModel):
    """What every predictor returns. `None` = not predicted (counts as wrong)."""

    model_config = ConfigDict(extra="forbid")

    doc_type: DocType | None = None
    tax_relevant: bool | None = None
    category: Category | None = None
    gross_amount: PredMoney | None = None
    deductible_amount: PredMoney | None = None
    labour_share_35a: PredMoney | None = None
    invoice_date: date | None = None
    payment_date: date | None = None
    tax_year: int | None = None
    payment_method: PaymentMethod | None = None
    vendor: str | None = None
    person_hint: str | None = None
    calls: list[CallUsage] = Field(default_factory=list)
    error_kind: str | None = None

    @classmethod
    def from_expected(cls, expected: ExpectedFields) -> Prediction:
        return cls.model_validate(expected.model_dump())


# --- report ---------------------------------------------------------------------------


class CaseFields(BaseModel):
    """Codes and numbers only: no vendor, person hint, notes or text."""

    model_config = ConfigDict(extra="forbid")

    doc_type: DocType | None
    tax_relevant: bool | None
    category: Category | None
    gross_amount: Money | None
    deductible_amount: Money | None
    labour_share_35a: Money | None
    tax_year: int | None
    payment_method: PaymentMethod | None


class CaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    tags: list[str]
    error_kind: str | None
    expected: CaseFields
    predicted: CaseFields
    match: dict[str, bool | None]
    cost_eur: Money | None
    latency_ms: int


class DatasetInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    hash: str | None
    n_cases: int | None
    label_schema_version: int


class GateRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: str
    value: float | str | None
    op: Literal["min", "max"] | None
    bound: float | None
    required: bool
    baseline: float | str | None
    delta: float | None
    regression_checked: bool
    result: Literal["pass", "fail", "n/a", "regression", "-"]


class GateResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    passed: bool | None
    thresholds_status: str | None
    compare_to: str | None
    baseline_stale: bool | None
    rows: list[GateRow]


ReportStatus = Literal["ok", "skipped", "gate_failed"]


class Report(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ReportStatus
    dataset: DatasetInfo
    predictor: dict[str, str]
    git_sha: str
    started_at: str
    duration_s: float
    recording_stale: bool
    subset: bool
    skipped_reason: str | None = None
    metrics: dict[str, Any] | None
    gate: GateResult | None
    cases: list[CaseResult]
