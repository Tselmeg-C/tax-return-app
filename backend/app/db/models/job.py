from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models._common import HouseholdOwned, Timestamps, UUIDPrimaryKey
from app.db.types import enum_type
from app.domain.enums import JobKind, JobStatus

# The statuses in which a job counts as "active" for its document (at most one at a time).
ACTIVE_STATUSES_SQL = "status IN ('queued', 'running')"


class Job(UUIDPrimaryKey, HouseholdOwned, Timestamps, Base):
    """A background job (Postgres queue, #6). Stores ids only: never file contents, names or paths.

    Claimed with a lease (`locked_by` / `locked_until`, see `app.queue.runner`); `attempts`
    counts claims, so a job that kills the worker is still bounded by `max_attempts`.
    """

    __tablename__ = "job"
    __table_args__ = (
        CheckConstraint(
            "kind <> 'process_document' OR document_id IS NOT NULL",
            name="process_document_needs_document",
        ),
        CheckConstraint("attempts >= 0 AND max_attempts >= 1", name="attempts_range"),
        CheckConstraint(
            "(status = 'running') = (locked_until IS NOT NULL)", name="running_has_lease"
        ),
        Index(
            "uq_job_kind_document_id_active",
            "kind",
            "document_id",
            unique=True,
            postgresql_where=text(ACTIVE_STATUSES_SQL),
        ),
        Index("ix_job_run_after_queued", "run_after", postgresql_where=text("status = 'queued'")),
        Index(
            "ix_job_locked_until_running",
            "locked_until",
            postgresql_where=text("status = 'running'"),
        ),
        Index(None, "household_id"),
        Index(None, "document_id"),
    )

    kind: Mapped[JobKind] = mapped_column(enum_type(JobKind, "kind"), nullable=False)
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document.id", ondelete="CASCADE"), nullable=True
    )
    status: Mapped[JobStatus] = mapped_column(
        enum_type(JobStatus, "status"),
        nullable=False,
        default=JobStatus.QUEUED,
        server_default=JobStatus.QUEUED.value,
    )
    attempts: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default=text("0")
    )
    max_attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    run_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    locked_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Exception class name only, never a message.
    last_error_kind: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # W3C `traceparent` of the enqueuing request (span link from the job span).
    trace_context: Mapped[str | None] = mapped_column(String(200), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
