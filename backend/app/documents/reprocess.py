"""Reprocess ("Neu verarbeiten", #45): re-queue `process_document` for finished documents.

Per document: lock the `document` row (id order, so bulk cannot deadlock) -> check status and
`overridden_by_user` items -> `enqueue` (job row) -> one `document` audit row, all inside the
caller's transaction. The audit row's `after` carries a `reprocess` marker (`single` or
`bulk:<request id>`) that the rate limit counts; ids and codes only, never names or values.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from datetime import timedelta

from opentelemetry import metrics
from opentelemetry.metrics import MeterProvider
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.audit import Actor, record, snapshot
from app.db.models import AuditLog, Document, TaxItem
from app.db.scope import HouseholdScope
from app.domain.enums import ActorType, AuditAction, DocumentStatus, JobKind
from app.queue.enqueue import enqueue

SINGLE_PER_HOUR = 20
BULK_PER_HOUR = 3
WINDOW = timedelta(hours=1)
REPROCESSABLE = {DocumentStatus.DONE, DocumentStatus.NEEDS_ATTENTION, DocumentStatus.FAILED}

BUSY = "busy"
HAS_OVERRIDES = "has_overrides"
NOT_FOUND = "not_found"


class ReprocessMetrics:
    """`belegbot.documents.reprocess{outcome=queued|busy|has_overrides|rate_limited}`."""

    def __init__(self, provider: MeterProvider | None = None) -> None:
        meter = (provider or metrics.get_meter_provider()).get_meter("belegbot")
        self.counter = meter.create_counter(
            "belegbot.documents.reprocess", unit="{document}", description="Reprocess outcomes"
        )

    def record(self, outcome: str, n: int = 1) -> None:
        if n:
            self.counter.add(n, {"outcome": outcome})


@dataclass
class Outcome:
    queued: list[Document] = field(default_factory=list)
    skipped: list[tuple[uuid.UUID, str]] = field(default_factory=list)


async def retry_after(session: AsyncSession, user_id: uuid.UUID, *, bulk: bool) -> int | None:
    """Seconds until this user may reprocess again, `None` if the hourly limit is not reached.

    Counted from audit rows (no extra table): single = rows marked `single`, bulk = distinct
    request ids marked `bulk:<id>`.

    Takes a per-user transaction-scoped advisory lock first: concurrent requests of one user
    queue up until the holder commits (its audit rows are then visible to the count). The caller
    must run this and `reprocess_documents` in one transaction.
    """
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"reprocess:{user_id}"}
    )
    marker = AuditLog.after["reprocess"].astext
    base = (
        AuditLog.entity == "document",
        AuditLog.action == AuditAction.UPDATE,
        AuditLog.actor_type == ActorType.USER,
        AuditLog.actor_user_id == user_id,
        AuditLog.created_at > func.now() - WINDOW,
    )
    if bulk:
        stmt = (
            select(func.min(AuditLog.created_at))
            .where(*base, marker.like("bulk:%"))
            .group_by(marker)
            .order_by(func.min(AuditLog.created_at).desc())
        )
        limit = BULK_PER_HOUR
    else:
        stmt = (
            select(AuditLog.created_at)
            .where(*base, marker == "single")
            .order_by(AuditLog.created_at.desc())
        )
        limit = SINGLE_PER_HOUR
    stamps = list((await session.execute(stmt.limit(limit))).scalars())
    if len(stamps) < limit:
        return None
    now = (await session.execute(select(func.now()))).scalar_one()
    return max(1, math.ceil((stamps[-1] + WINDOW - now).total_seconds()))


async def reprocess_documents(
    scope: HouseholdScope,
    document_ids: list[uuid.UUID],
    user_id: uuid.UUID,
    *,
    fresh: bool,
    bulk: bool,
    trace_context: str | None,
    max_attempts: int,
) -> Outcome:
    """Queue what can be queued; the caller commits. Never raises per document."""
    session = scope.session
    marker = f"bulk:{uuid.uuid4()}" if bulk else "single"
    out = Outcome()
    for doc_id in sorted(set(document_ids)):
        stmt = scope.select(Document).where(Document.id == doc_id).with_for_update()
        stmt = stmt.execution_options(populate_existing=True)
        doc = (await session.execute(stmt)).scalar_one_or_none()
        if doc is None:
            out.skipped.append((doc_id, NOT_FOUND))
            continue
        if doc.status not in REPROCESSABLE:
            out.skipped.append((doc_id, BUSY))
            continue
        overridden = await session.execute(
            scope.select(TaxItem)
            .with_only_columns(TaxItem.id)
            .where(TaxItem.document_id == doc.id, TaxItem.overridden_by_user.is_(True))
            .limit(1)
        )
        if overridden.first() is not None:
            out.skipped.append((doc_id, HAS_OVERRIDES))
            continue
        before = snapshot(doc)
        job_id = await enqueue(
            session,
            kind=JobKind.PROCESS_DOCUMENT,
            household_id=scope.household_id,
            document_id=doc.id,
            trace_context=trace_context,
            max_attempts=max_attempts,
            fresh=fresh,
        )
        if job_id is None:  # an active job exists although the status said otherwise
            out.skipped.append((doc_id, BUSY))
            continue
        doc.status = DocumentStatus.QUEUED
        doc.error_kind = None
        doc.attention_reason = None
        await session.flush()
        after = snapshot(doc)
        after["reprocess"] = marker
        after["fresh"] = fresh
        await record(
            session,
            household_id=scope.household_id,
            entity="document",
            entity_id=doc.id,
            action=AuditAction.UPDATE,
            before=before,
            after=after,
            actor=Actor.user(user_id),
        )
        out.queued.append(doc)
    return out


__all__ = [
    "BULK_PER_HOUR",
    "SINGLE_PER_HOUR",
    "Outcome",
    "ReprocessMetrics",
    "reprocess_documents",
    "retry_after",
]
