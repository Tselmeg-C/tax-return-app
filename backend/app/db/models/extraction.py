from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models._common import CreatedAt, HouseholdOwned, UUIDPrimaryKey
from app.db.types import EncryptedJSON, enum_type
from app.domain.enums import DocType, ExtractionStep


class Extraction(UUIDPrimaryKey, HouseholdOwned, CreatedAt, Base):
    """One LLM call on a document (`plan.md` §6 `LLMResult`). `raw_json` is encrypted."""

    __tablename__ = "extraction"
    __table_args__ = (
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="confidence_range",
        ),
        CheckConstraint("input_tokens >= 0", name="input_tokens_nonnegative"),
        CheckConstraint("output_tokens >= 0", name="output_tokens_nonnegative"),
        CheckConstraint("cost_eur >= 0", name="cost_eur_nonnegative"),
        CheckConstraint("latency_ms >= 0", name="latency_ms_nonnegative"),
        Index(None, "household_id"),
        Index(None, "document_id"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document.id", ondelete="CASCADE"), nullable=False
    )
    step: Mapped[ExtractionStep] = mapped_column(enum_type(ExtractionStep, "step"), nullable=False)
    doc_type: Mapped[DocType | None] = mapped_column(enum_type(DocType, "doc_type"), nullable=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(50), nullable=False)
    # Raw LLM output is document content (may hold a Steuer-ID). NULL on failed calls.
    raw_json: Mapped[Any | None] = mapped_column(
        EncryptedJSON("extraction.raw_json"), nullable=True
    )
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_eur: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    error_kind: Mapped[str | None] = mapped_column(String(100), nullable=True)
