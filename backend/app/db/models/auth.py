"""Auth tables (#5): one-time login links and server-side sessions.

Both store only `sha256(token)` as 64 lowercase hex chars; the raw token exists only in the
e-mail link / the cookie. Neither stores an IP address or user agent (personal data that
nothing needs). Rows go away with their user (`ON DELETE CASCADE`).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CHAR, CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models._common import CreatedAt, HouseholdOwned, UUIDPrimaryKey

TOKEN_HASH_HEX = "token_hash ~ '^[0-9a-f]{64}$'"


class MagicLinkToken(UUIDPrimaryKey, HouseholdOwned, CreatedAt, Base):
    """A login link. Valid while `now < expires_at` and `used_at IS NULL`.

    `created_at` is written from the auth clock (not `now()`), so the per-e-mail rate limit and
    the expiry use one time source.
    """

    __tablename__ = "magic_link_token"
    __table_args__ = (
        UniqueConstraint("token_hash"),
        CheckConstraint(TOKEN_HASH_HEX, name="token_hash_hex"),
        CheckConstraint("expires_at > created_at", name="expires_after_created"),
        Index(None, "household_id"),
        Index("ix_magic_link_token_user_id_created_at", "user_id", "created_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    redirect_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UserSession(UUIDPrimaryKey, HouseholdOwned, CreatedAt, Base):
    """A login session (not `session`: clashes with SQLAlchemy's `Session`, `SESSION_USER`).

    Valid while not revoked, `now < expires_at` (absolute) and `now < last_seen_at + idle`.
    `created_at` is written from the auth clock as well.
    """

    __tablename__ = "user_session"
    __table_args__ = (
        UniqueConstraint("token_hash"),
        CheckConstraint(TOKEN_HASH_HEX, name="token_hash_hex"),
        Index(None, "household_id"),
        Index(None, "user_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
