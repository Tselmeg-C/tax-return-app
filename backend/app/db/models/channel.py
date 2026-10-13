"""Chat linking (#11): which chat belongs to which user, and the one-time codes that link it.

`channel_link.external_id` (the chat id, needed to send) is encrypted; `external_id_hash` is
the lookup key (`sha256("telegram:" + chat id)`). `channel_link_code` stores only the hash of
the 8-digit code, never the code.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models._common import CreatedAt, HouseholdOwned, UUIDPrimaryKey
from app.db.types import EncryptedString, enum_type
from app.domain.enums import Channel

HASH_HEX = "{col} ~ '^[0-9a-f]{{64}}$'"


class ChannelLink(UUIDPrimaryKey, HouseholdOwned, CreatedAt, Base):
    __tablename__ = "channel_link"
    __table_args__ = (
        UniqueConstraint(
            "channel", "external_id_hash", name="uq_channel_link_channel_external_id_hash"
        ),
        UniqueConstraint("user_id", "channel", name="uq_channel_link_user_id_channel"),
        CheckConstraint(HASH_HEX.format(col="external_id_hash"), name="external_id_hash_hex"),
        Index(None, "household_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=False
    )
    channel: Mapped[Channel] = mapped_column(enum_type(Channel, "channel"), nullable=False)
    external_id: Mapped[str] = mapped_column(
        EncryptedString("channel_link.external_id"), nullable=False
    )
    external_id_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    year: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    last_update_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class ChannelLinkCode(UUIDPrimaryKey, HouseholdOwned, CreatedAt, Base):
    __tablename__ = "channel_link_code"
    __table_args__ = (
        UniqueConstraint("code_hash"),
        CheckConstraint(HASH_HEX.format(col="code_hash"), name="code_hash_hex"),
        Index(None, "household_id"),
        Index(None, "user_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False
    )
    code_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
