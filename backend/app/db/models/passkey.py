"""WebAuthn credentials (#12). `credential_id` and `public_key` are public by definition
(no encryption needed) but are never logged and are redacted in audit rows."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, LargeBinary, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models._common import CreatedAt, HouseholdOwned, UUIDPrimaryKey


class Passkey(UUIDPrimaryKey, HouseholdOwned, CreatedAt, Base):
    __tablename__ = "passkey"
    __table_args__ = (
        UniqueConstraint("credential_id"),
        Index(None, "household_id"),
        Index(None, "user_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=False
    )
    credential_id: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    public_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    sign_count: Mapped[int] = mapped_column(Integer, nullable=False)
    transports: Mapped[list[Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
