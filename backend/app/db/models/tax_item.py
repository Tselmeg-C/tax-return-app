from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    false,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models._common import HouseholdOwned, Timestamps, UUIDPrimaryKey
from app.db.types import enum_type
from app.domain.enums import Anlage, Category, PaymentMethod


def _money() -> Numeric[Decimal]:
    return Numeric(12, 2)


class TaxItem(UUIDPrimaryKey, HouseholdOwned, Timestamps, Base):
    """A (potentially) deductible amount. Amounts may be negative (credit notes, refunds).

    `document_id` NULL = manual entry; `person_id` NULL = household-level (e.g. §35a).
    `version` (#10) is bumped on every update (optimistic locking for user edits).
    """

    __tablename__ = "tax_item"
    __table_args__ = (
        CheckConstraint("year BETWEEN 2000 AND 2100", name="year_range"),
        CheckConstraint("is_relevant OR deductible_amount = 0", name="irrelevant_not_deductible"),
        CheckConstraint(
            "labour_share_35a IS NULL OR labour_share_35a >= 0",
            name="labour_share_35a_nonnegative",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="confidence_range",
        ),
        Index("ix_tax_item_household_id_year", "household_id", "year"),
        Index(None, "document_id"),
        Index(None, "extraction_id"),
        Index(None, "person_id"),
    )

    document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document.id", ondelete="CASCADE"), nullable=True
    )
    extraction_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("extraction.id", ondelete="SET NULL"), nullable=True
    )
    person_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("person.id", ondelete="RESTRICT"), nullable=True
    )
    year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    category: Mapped[Category] = mapped_column(enum_type(Category, "category"), nullable=False)
    anlage: Mapped[Anlage | None] = mapped_column(enum_type(Anlage, "anlage"), nullable=True)
    zeile: Mapped[str | None] = mapped_column(String(20), nullable=True)
    gross_amount: Mapped[Decimal] = mapped_column(_money(), nullable=False)
    deductible_amount: Mapped[Decimal] = mapped_column(_money(), nullable=False)
    labour_share_35a: Mapped[Decimal | None] = mapped_column(_money(), nullable=True)
    vendor: Mapped[str | None] = mapped_column(String(200), nullable=True)
    invoice_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    payment_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    payment_method: Mapped[PaymentMethod] = mapped_column(
        enum_type(PaymentMethod, "payment_method"),
        nullable=False,
        default=PaymentMethod.UNKNOWN,
        server_default=PaymentMethod.UNKNOWN.value,
    )
    is_relevant: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    overridden_by_user: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))

    __mapper_args__: dict[str, Any] = {"version_id_col": version, "eager_defaults": True}
