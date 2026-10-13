from __future__ import annotations

from typing import Any

from sqlalchemy import CHAR, CheckConstraint, SmallInteger, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models._common import CreatedAt, HouseholdOwned, UUIDPrimaryKey


class Estimate(UUIDPrimaryKey, HouseholdOwned, CreatedAt, Base):
    """Cached `GET /summary` result (#17), one row per household, year and inputs hash.

    `result` holds codes, ids and amounts, never names or free text. The unique constraint
    starts with `household_id`, so it is also the FK's index.
    """

    __tablename__ = "estimate"
    __table_args__ = (
        UniqueConstraint("household_id", "year", "inputs_hash"),
        CheckConstraint("year BETWEEN 2000 AND 2100", name="year_range"),
        CheckConstraint("inputs_hash ~ '^[0-9a-f]{64}$'", name="inputs_hash_hex"),
    )

    year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    params_version: Mapped[str] = mapped_column(String(64), nullable=False)
    inputs_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
