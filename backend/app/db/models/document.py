from __future__ import annotations

import uuid

from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models._common import HouseholdOwned, Timestamps, UUIDPrimaryKey
from app.db.types import enum_type
from app.domain.enums import Channel, DocType, DocumentStatus


class Document(UUIDPrimaryKey, HouseholdOwned, Timestamps, Base):
    """An uploaded file. `storage_key` is opaque (never the original file name).

    `original_filename` (#6) is display metadata only: sanitised, plain text, PII. Never use it
    in a path, a `Storage` call, a log line, a span or a metric.
    """

    __tablename__ = "document"
    __table_args__ = (
        UniqueConstraint("household_id", "sha256", name="uq_document_household_id_sha256"),
        UniqueConstraint("storage_key"),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="sha256_hex"),
        CheckConstraint("size_bytes > 0", name="size_bytes_positive"),
        CheckConstraint("page_count IS NULL OR page_count >= 1", name="page_count_positive"),
        CheckConstraint(
            "original_filename IS NULL OR octet_length(original_filename) BETWEEN 1 AND 255",
            name="original_filename_length",
        ),
        Index("ix_document_household_id_created_at", "household_id", "created_at"),
        Index(None, "uploaded_by_user_id"),
    )

    uploaded_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=False
    )
    channel: Mapped[Channel] = mapped_column(enum_type(Channel, "channel"), nullable=False)
    sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    page_count: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[DocumentStatus] = mapped_column(
        enum_type(DocumentStatus, "status"),
        nullable=False,
        default=DocumentStatus.QUEUED,
        server_default=DocumentStatus.QUEUED.value,
    )
    doc_type: Mapped[DocType | None] = mapped_column(enum_type(DocType, "doc_type"), nullable=True)
    # Exception class name only, never a message (messages can carry document content).
    error_kind: Mapped[str | None] = mapped_column(String(100), nullable=True)
    original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
