from __future__ import annotations

from datetime import date

from sqlalchemy import Boolean, CheckConstraint, Date, Index, SmallInteger, String, false
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models._common import HouseholdOwned, Timestamps, UUIDPrimaryKey
from app.db.types import EncryptedString, enum_type
from app.domain.enums import PersonKind, Religion


class Person(UUIDPrimaryKey, HouseholdOwned, Timestamps, Base):
    """A household member (adult or child). `steuer_id` is encrypted and cannot be filtered."""

    __tablename__ = "person"
    __table_args__ = (
        CheckConstraint("kind <> 'child' OR dob IS NOT NULL", name="child_requires_dob"),
        CheckConstraint(
            "disability_grade IS NULL OR "
            "(disability_grade BETWEEN 20 AND 100 AND disability_grade % 10 = 0)",
            name="disability_grade_range",
        ),
        Index(None, "household_id"),
    )

    kind: Mapped[PersonKind] = mapped_column(enum_type(PersonKind, "kind"), nullable=False)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    dob: Mapped[date | None] = mapped_column(Date, nullable=True)
    steuer_id: Mapped[str | None] = mapped_column(
        EncryptedString("person.steuer_id"), nullable=True
    )
    religion: Mapped[Religion] = mapped_column(
        enum_type(Religion, "religion"),
        nullable=False,
        default=Religion.NONE,
        server_default=Religion.NONE.value,
    )
    disability_grade: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    # Merkzeichen H, Bl or TBl (hilflos / blind / taubblind): 7.400 EUR Pauschbetrag (#76).
    merkzeichen_h_bl_tbl: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
