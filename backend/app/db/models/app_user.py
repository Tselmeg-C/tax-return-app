from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, validates

from app.db.base import Base
from app.db.models._common import HouseholdOwned, Timestamps, UUIDPrimaryKey
from app.db.types import enum_type
from app.domain.enums import UserRole


class AppUser(UUIDPrimaryKey, HouseholdOwned, Timestamps, Base):
    """A login (`user` is reserved in Postgres), optionally linked to the person it represents.

    `email` is unique globally (login looks it up) and lowercased + trimmed on assignment.
    """

    __tablename__ = "app_user"
    __table_args__ = (
        UniqueConstraint("email"),
        UniqueConstraint("person_id"),
        CheckConstraint("email = lower(email)", name="email_lowercase"),
        Index(None, "household_id"),
    )

    email: Mapped[str] = mapped_column(String(320), nullable=False)
    role: Mapped[UserRole] = mapped_column(enum_type(UserRole, "role"), nullable=False)
    person_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("person.id", ondelete="SET NULL"), nullable=True
    )
    # Set = no new login links and no sessions (CLI `disable`); the household's documents stay.
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @validates("email")
    def _normalise_email(self, _key: str, value: str) -> str:
        return value.strip().lower()
