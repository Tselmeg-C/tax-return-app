"""Per-year household profile (#13): one `tax_profile` per household and year, 0..n
`employment` per person and year, 0..1 `child_year` per child and year.

Writes go through `app/household/service.py` (validation + audit rows). `employer_name` is
display text and never logged. Church tax is not stored: it follows `person.religion` and the
params' `church_tax.rate_by_state`.
"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    UniqueConstraint,
    false,
    text,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models._common import HouseholdOwned, Timestamps, UUIDPrimaryKey
from app.db.types import enum_type
from app.domain.enums import AllowanceShare, Bundesland, FilingStatus, Steuerklasse

YEAR_RANGE = "year BETWEEN 2000 AND 2100"


class TaxProfile(UUIDPrimaryKey, HouseholdOwned, Timestamps, Base):
    """Who files for `year`: the taxpayer (Person A) and, for `joint`, the spouse (Person B)."""

    __tablename__ = "tax_profile"
    __table_args__ = (
        UniqueConstraint("household_id", "year"),
        CheckConstraint(YEAR_RANGE, name="year_range"),
        CheckConstraint(
            "(filing_status = 'joint') = (spouse_person_id IS NOT NULL)", name="spouse_iff_joint"
        ),
        CheckConstraint(
            "spouse_person_id IS NULL OR spouse_person_id <> taxpayer_person_id",
            name="spouse_not_taxpayer",
        ),
        Index(None, "taxpayer_person_id"),
        Index(None, "spouse_person_id"),
    )

    year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    filing_status: Mapped[FilingStatus] = mapped_column(
        enum_type(FilingStatus, "filing_status"), nullable=False
    )
    bundesland: Mapped[Bundesland] = mapped_column(
        enum_type(Bundesland, "bundesland"), nullable=False
    )
    taxpayer_person_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("person.id", ondelete="RESTRICT"), nullable=False
    )
    spouse_person_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("person.id", ondelete="RESTRICT"), nullable=True
    )


class Employment(UUIDPrimaryKey, HouseholdOwned, Timestamps, Base):
    """One employer of a person in a year (several in sequence or in parallel are allowed)."""

    __tablename__ = "employment"
    __table_args__ = (
        CheckConstraint(YEAR_RANGE, name="year_range"),
        CheckConstraint("NOT has_factor OR steuerklasse = '4'", name="factor_requires_iv"),
        CheckConstraint("commute_km IS NULL OR commute_km BETWEEN 0 AND 999", name="commute_km"),
        CheckConstraint("office_days BETWEEN 0 AND 366", name="office_days"),
        CheckConstraint("homeoffice_days BETWEEN 0 AND 366", name="homeoffice_days"),
        Index(None, "household_id"),
        Index(None, "person_id", "year"),
    )

    person_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("person.id", ondelete="CASCADE"), nullable=False
    )
    year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    employer_name: Mapped[str] = mapped_column(String(200), nullable=False)
    steuerklasse: Mapped[Steuerklasse] = mapped_column(
        enum_type(Steuerklasse, "steuerklasse"), nullable=False
    )
    has_factor: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    commute_km: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    office_days: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default=text("0")
    )
    homeoffice_days: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default=text("0")
    )


class ChildYear(UUIDPrimaryKey, HouseholdOwned, Timestamps, Base):
    """What no receipt shows about a child in a year: months, Freibetrag share, household."""

    __tablename__ = "child_year"
    __table_args__ = (
        UniqueConstraint("person_id", "year"),
        CheckConstraint(YEAR_RANGE, name="year_range"),
        CheckConstraint("months BETWEEN 0 AND 12", name="months_range"),
        Index(None, "household_id"),
    )

    person_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("person.id", ondelete="CASCADE"), nullable=False
    )
    year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    months: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    allowance_share: Mapped[AllowanceShare] = mapped_column(
        enum_type(AllowanceShare, "allowance_share"), nullable=False
    )
    in_household: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
