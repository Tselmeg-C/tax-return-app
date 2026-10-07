"""Claim, heartbeat and finish jobs (leases with fencing; #6 Decisions 8-10).

Every function here runs its own short transaction and commits; no transaction stays open
while a handler runs. All times come from the DB clock (`now()`).

Lock order (no deadlocks with `DELETE /documents/{id}`): the `document` row is locked
before the `job` row in every path that changes both.

System-level module: the claim scans the `job` table across households (the queue is
global). Document rows are always reached through `HouseholdScope` with the job's
`household_id`.
"""

from __future__ import annotations

import random
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.db.models import Document, Job
from app.db.scope import HouseholdScope
from app.domain.enums import DocumentStatus, JobKind, JobStatus
from app.queue.errors import LEASE_EXPIRED

SessionFactory = Callable[[], AsyncSession]
CANDIDATES = 10


@dataclass(frozen=True)
class ClaimedJob:
    id: uuid.UUID
    kind: JobKind
    household_id: uuid.UUID
    document_id: uuid.UUID | None
    attempt: int  # `attempts` after this claim (1 = first run)
    max_attempts: int
    trace_context: str | None
    worker_id: str
    reclaimed: bool  # its previous lease had expired


@dataclass(frozen=True)
class ExpiredJob:
    id: uuid.UUID
    kind: JobKind


def _claimable() -> ColumnElement[bool]:
    return or_(
        and_(Job.status == JobStatus.QUEUED, Job.run_after <= func.now()),
        and_(
            Job.status == JobStatus.RUNNING,
            Job.locked_until < func.now(),
            Job.attempts < Job.max_attempts,
        ),
    )


def _poisoned() -> ColumnElement[bool]:
    return and_(
        Job.status == JobStatus.RUNNING,
        Job.locked_until < func.now(),
        Job.attempts >= Job.max_attempts,
    )


def _lease(seconds: float) -> Any:
    return func.now() + timedelta(seconds=seconds)


async def _lock_document(
    session: AsyncSession, household_id: uuid.UUID, document_id: uuid.UUID, *, skip_locked: bool
) -> bool:
    stmt = (
        HouseholdScope(session, household_id)
        .select(Document)
        .where(Document.id == document_id)
        .with_for_update(skip_locked=skip_locked)
    )
    result = await session.execute(stmt.with_only_columns(Document.id))
    return result.scalar_one_or_none() is not None


async def _set_document(
    session: AsyncSession, household_id: uuid.UUID, document_id: uuid.UUID, **values: Any
) -> None:
    await session.execute(
        update(Document)
        .where(Document.id == document_id, Document.household_id == household_id)
        .values(**values)
        .execution_options(synchronize_session=False)
    )


async def expire_poisoned(factory: SessionFactory) -> list[ExpiredJob]:
    """Expired leases at `max_attempts` → `failed` / `LeaseExpired` (never claimed again)."""
    expired: list[ExpiredJob] = []
    async with factory() as session, session.begin():
        rows = (
            await session.execute(
                select(Job.id, Job.kind, Job.household_id, Job.document_id)
                .where(_poisoned())
                .order_by(Job.locked_until)
                .limit(CANDIDATES)
            )
        ).all()
        for job_id, kind, household_id, document_id in rows:
            if document_id is not None and not await _lock_document(
                session, household_id, document_id, skip_locked=True
            ):
                continue
            result = await session.execute(
                update(Job)
                .where(Job.id == job_id, _poisoned())
                .values(
                    status=JobStatus.FAILED,
                    last_error_kind=LEASE_EXPIRED,
                    locked_by=None,
                    locked_until=None,
                    finished_at=func.now(),
                )
                .returning(Job.id)
                .execution_options(synchronize_session=False)
            )
            if result.scalar_one_or_none() is None:
                continue
            if document_id is not None:
                await _set_document(
                    session,
                    household_id,
                    document_id,
                    status=DocumentStatus.FAILED,
                    error_kind=LEASE_EXPIRED,
                )
            expired.append(ExpiredJob(job_id, JobKind(kind)))
    return expired


async def claim(
    factory: SessionFactory, *, worker_id: str, lease_seconds: float
) -> ClaimedJob | None:
    """Claim the next due job (queued and due, or running with an expired lease)."""
    async with factory() as session, session.begin():
        candidates = (
            await session.execute(
                select(Job.id, Job.household_id, Job.document_id, Job.status)
                .where(_claimable())
                .order_by(Job.run_after, Job.created_at)
                .limit(CANDIDATES)
            )
        ).all()
        for job_id, household_id, document_id, previous_status in candidates:
            if document_id is not None and not await _lock_document(
                session, household_id, document_id, skip_locked=True
            ):
                continue  # another worker (or a delete) holds the document
            locked_id = (
                select(Job.id)
                .where(Job.id == job_id, _claimable())
                .with_for_update(skip_locked=True)
                .scalar_subquery()
            )
            result = await session.execute(
                update(Job)
                .where(Job.id == locked_id)
                .values(
                    status=JobStatus.RUNNING,
                    attempts=Job.attempts + 1,
                    locked_by=worker_id,
                    locked_until=_lease(lease_seconds),
                    started_at=func.now(),
                    finished_at=None,
                )
                .returning(
                    Job.id,
                    Job.kind,
                    Job.household_id,
                    Job.document_id,
                    Job.attempts,
                    Job.max_attempts,
                    Job.trace_context,
                )
                .execution_options(synchronize_session=False)
            )
            row = result.one_or_none()
            if row is None:
                continue
            if document_id is not None:
                await _set_document(
                    session, household_id, document_id, status=DocumentStatus.PROCESSING
                )
            return ClaimedJob(
                id=row.id,
                kind=JobKind(row.kind),
                household_id=row.household_id,
                document_id=row.document_id,
                attempt=row.attempts,
                max_attempts=row.max_attempts,
                trace_context=row.trace_context,
                worker_id=worker_id,
                reclaimed=JobStatus(previous_status) is JobStatus.RUNNING,
            )
    return None


def _fence(job: ClaimedJob) -> ColumnElement[bool]:
    return and_(
        Job.id == job.id,
        Job.status == JobStatus.RUNNING,
        Job.locked_by == job.worker_id,
        Job.attempts == job.attempt,
    )


async def heartbeat(factory: SessionFactory, job: ClaimedJob, *, lease_seconds: float) -> bool:
    """Extend the lease; `False` if it was lost (another worker took the job over)."""
    async with factory() as session, session.begin():
        result = await session.execute(
            update(Job)
            .where(_fence(job))
            .values(locked_until=_lease(lease_seconds))
            .returning(Job.id)
            .execution_options(synchronize_session=False)
        )
        return result.scalar_one_or_none() is not None


async def _finish(
    factory: SessionFactory,
    job: ClaimedJob,
    job_values: dict[str, Any],
    document_values: dict[str, Any],
) -> bool:
    """Fenced completion: `False` (nothing changed) if this worker no longer holds the lease."""
    async with factory() as session, session.begin():
        if job.document_id is not None:
            await _lock_document(session, job.household_id, job.document_id, skip_locked=False)
        result = await session.execute(
            update(Job)
            .where(_fence(job))
            .values(locked_by=None, locked_until=None, **job_values)
            .returning(Job.id)
            .execution_options(synchronize_session=False)
        )
        if result.scalar_one_or_none() is None:
            return False
        if job.document_id is not None:
            await _set_document(session, job.household_id, job.document_id, **document_values)
        return True


async def succeed(factory: SessionFactory, job: ClaimedJob) -> bool:
    return await _finish(
        factory,
        job,
        {"status": JobStatus.SUCCEEDED, "finished_at": func.now()},
        {"status": DocumentStatus.DONE, "error_kind": None},
    )


async def retry(
    factory: SessionFactory, job: ClaimedJob, *, error_kind: str, delay_s: float
) -> bool:
    return await _finish(
        factory,
        job,
        {
            "status": JobStatus.QUEUED,
            "run_after": func.now() + timedelta(seconds=delay_s),
            "last_error_kind": error_kind,
        },
        {"status": DocumentStatus.QUEUED},
    )


async def fail(factory: SessionFactory, job: ClaimedJob, *, error_kind: str) -> bool:
    return await _finish(
        factory,
        job,
        {"status": JobStatus.FAILED, "finished_at": func.now(), "last_error_kind": error_kind},
        {"status": DocumentStatus.FAILED, "error_kind": error_kind},
    )


async def release(factory: SessionFactory, job: ClaimedJob) -> bool:
    """Shutdown: back to `queued` without using up an attempt (not the job's fault)."""
    return await _finish(
        factory,
        job,
        {"status": JobStatus.QUEUED, "attempts": Job.attempts - 1, "run_after": func.now()},
        {"status": DocumentStatus.QUEUED},
    )


def backoff_seconds(
    attempt: int, *, base: float, maximum: float, rng: Callable[[], float] = random.random
) -> float:
    """`min(base * 2^(attempt-1), maximum)` times a random factor in [0.8, 1.2]."""
    delay: float = min(base * 2.0 ** max(attempt - 1, 0), maximum)
    return delay * (0.8 + 0.4 * rng())


async def queue_stats(session: AsyncSession) -> tuple[dict[str, int], float]:
    """Job counts per status and the age (s) of the oldest claimable queued job (0 if none)."""
    counts = await session.execute(select(Job.status, func.count()).group_by(Job.status))
    depth = {JobStatus(status).value: int(n) for status, n in counts.all()}
    oldest = await session.execute(
        select(func.extract("epoch", func.now() - func.min(Job.created_at))).where(
            Job.status == JobStatus.QUEUED, Job.run_after <= func.now()
        )
    )
    age = oldest.scalar_one_or_none()
    return depth, float(age or 0.0)
