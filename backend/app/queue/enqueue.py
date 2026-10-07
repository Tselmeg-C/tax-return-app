"""`enqueue`: add a job inside the caller's transaction (never commits)."""

from __future__ import annotations

import uuid

from opentelemetry import trace
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Job
from app.db.models.job import ACTIVE_STATUSES_SQL
from app.domain.enums import JobKind, JobStatus

DEFAULT_MAX_ATTEMPTS = 5


def current_traceparent() -> str | None:
    """W3C `traceparent` of the current span (stored on the job for a span link)."""
    if not trace.get_current_span().get_span_context().is_valid:
        return None
    carrier: dict[str, str] = {}
    TraceContextTextMapPropagator().inject(carrier)
    value = carrier.get("traceparent")
    return value if value and len(value) <= 200 else None


async def enqueue(
    session: AsyncSession,
    *,
    kind: JobKind,
    household_id: uuid.UUID,
    document_id: uuid.UUID | None,
    trace_context: str | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> uuid.UUID | None:
    """Insert a `queued` job; returns its id, or `None` if one is already active.

    At most one `queued`/`running` job exists per (kind, document): a second enqueue is a
    no-op (`ON CONFLICT DO NOTHING` on the partial unique index).
    """
    stmt = (
        insert(Job)
        .values(
            id=uuid.uuid4(),
            household_id=household_id,
            kind=kind,
            document_id=document_id,
            status=JobStatus.QUEUED,
            attempts=0,
            max_attempts=max_attempts,
            trace_context=trace_context,
        )
        .on_conflict_do_nothing(
            index_elements=["kind", "document_id"], index_where=text(ACTIVE_STATUSES_SQL)
        )
        .returning(Job.id)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()
