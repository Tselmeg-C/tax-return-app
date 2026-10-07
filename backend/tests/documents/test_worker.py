"""Queue and worker (#6 Decisions 8-10), against committed rows on the real test DB.

The DB clock is "moved" by updating rows (`run_after`, `locked_until`).
"""

from __future__ import annotations

import asyncio
import io
import json
import uuid
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.clock import FakeClock
from app.config import Settings
from app.db.models import AppUser, Document, Job
from app.db.scope import HouseholdScope
from app.documents.service import ingest_document
from app.domain.enums import Channel, DocumentStatus, JobKind, JobStatus
from app.queue import runner
from app.queue.enqueue import enqueue
from app.queue.handlers import JobContext, check_stored_file
from app.storage import LocalVolume
from app.worker.loop import Worker
from tests.documents import files
from tests.documents.conftest import CommittedUser, committed_user

Sessions = async_sessionmaker[AsyncSession]


async def _one(data: bytes) -> AsyncIterator[bytes]:
    yield data


@pytest.fixture
def volume(tmp_path: Path) -> LocalVolume:
    v = LocalVolume(tmp_path / "storage")
    v.probe()
    return v


@pytest.fixture
async def owner(committed: Sessions, clock: FakeClock) -> CommittedUser:
    return await committed_user(committed, clock, None)


def settings_for(database_url: str, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "database_url": database_url,
        "worker_poll_interval_seconds": 0.05,
        "worker_shutdown_grace_seconds": 1,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def make_worker(
    committed: Sessions,
    volume: LocalVolume,
    database_url: str,
    handler: Callable[[JobContext], Any] | None = None,
    worker_id: str | None = None,
    **overrides: Any,
) -> Worker:
    # #6's tests use the stored-file check (#9's pipeline has its own tests in tests/pipeline).
    handlers = {JobKind.PROCESS_DOCUMENT: handler or check_stored_file}
    return Worker(
        sessionmaker=committed,
        storage=volume,
        settings=settings_for(database_url, **overrides),
        handlers=handlers,
        worker_id=worker_id,
        error_backoff=(0.01, 0.05),
    )


async def upload(
    committed: Sessions, volume: LocalVolume, user: CommittedUser, data: bytes | None = None
) -> Document:
    async with committed() as session:
        result = await ingest_document(
            HouseholdScope(session, user.household_id),
            user.user_id,
            _one(data or files.pdf()),
            channel=Channel.WEB,
            storage=volume,
            max_bytes=10_000_000,
        )
    return result.document


async def job_of(committed: Sessions, document_id: uuid.UUID) -> Job:
    async with committed() as session:
        rows = (
            (
                await session.execute(
                    select(Job).where(Job.document_id == document_id).order_by(Job.created_at)
                )
            )
            .scalars()
            .all()
        )
        return rows[-1]


async def doc_of(committed: Sessions, document_id: uuid.UUID) -> Document:
    async with committed() as session:
        doc = await session.get(Document, document_id)
        assert doc is not None
        return doc


async def seconds_until_run(committed: Sessions, job_id: uuid.UUID) -> float:
    async with committed() as session:
        value = await session.execute(
            select(func.extract("epoch", Job.run_after - func.now())).where(Job.id == job_id)
        )
        return float(value.scalar_one())


async def make_due(committed: Sessions, job_id: uuid.UUID) -> None:
    async with committed() as session:
        await session.execute(update(Job).where(Job.id == job_id).values(run_after=func.now()))
        await session.commit()


async def test_upload_then_stub_succeeds(
    committed: Sessions, volume: LocalVolume, owner: CommittedUser, migrated_database: str
) -> None:
    doc = await upload(committed, volume, owner)
    # Decision 14: the uploader being disabled changes nothing for the job.
    async with committed() as session:
        await session.execute(
            update(AppUser).where(AppUser.id == owner.user_id).values(disabled_at=func.now())
        )
        await session.commit()
    worker = make_worker(committed, volume, migrated_database)
    assert await worker.run_once() is True
    job = await job_of(committed, doc.id)
    assert job.status is JobStatus.SUCCEEDED and job.attempts == 1
    assert job.started_at is not None and job.finished_at is not None
    assert job.locked_until is None and job.locked_by is None
    assert (await doc_of(committed, doc.id)).status is DocumentStatus.DONE
    assert await worker.run_once() is False


async def test_stub_missing_file_and_checksum(
    committed: Sessions, volume: LocalVolume, owner: CommittedUser, migrated_database: str
) -> None:
    missing = await upload(committed, volume, owner)
    volume.path_for(missing.storage_key).unlink()
    changed = await upload(committed, volume, owner)
    volume.path_for(changed.storage_key).write_bytes(b"%PDF-changed")
    worker = make_worker(committed, volume, migrated_database)
    await worker.run_once()
    await worker.run_once()
    for doc, kind in ((missing, "FileMissing"), (changed, "ChecksumMismatch")):
        job = await job_of(committed, doc.id)
        assert (job.status, job.last_error_kind, job.attempts) == (JobStatus.FAILED, kind, 1)
        stored = await doc_of(committed, doc.id)
        assert (stored.status, stored.error_kind) == (DocumentStatus.FAILED, kind)


async def test_backoff_and_final_failure(
    committed: Sessions, volume: LocalVolume, owner: CommittedUser, migrated_database: str
) -> None:
    async def always(ctx: JobContext) -> None:
        raise ValueError("boom with a secret message")

    doc = await upload(committed, volume, owner)
    worker = make_worker(committed, volume, migrated_database, always, job_backoff_max_seconds=60)
    bounds = {1: (24, 36), 2: (48, 72), 3: (0, 72), 4: (0, 72)}
    for attempt in range(1, 5):
        assert await worker.run_once()
        job = await job_of(committed, doc.id)
        assert job.status is JobStatus.QUEUED and job.attempts == attempt
        assert job.last_error_kind == "ValueError"
        low, high = bounds[attempt]
        assert low - 1 <= await seconds_until_run(committed, job.id) <= high
        assert (await doc_of(committed, doc.id)).status is DocumentStatus.QUEUED
        await make_due(committed, job.id)
    assert await worker.run_once()
    job = await job_of(committed, doc.id)
    assert (job.status, job.attempts, job.last_error_kind) == (JobStatus.FAILED, 5, "ValueError")
    stored = await doc_of(committed, doc.id)
    assert (stored.status, stored.error_kind) == (DocumentStatus.FAILED, "ValueError")


async def test_fails_once_then_succeeds(
    committed: Sessions, volume: LocalVolume, owner: CommittedUser, migrated_database: str
) -> None:
    seen: list[DocumentStatus] = []

    async def flaky(ctx: JobContext) -> None:
        assert ctx.job.document_id is not None
        seen.append((await doc_of(committed, ctx.job.document_id)).status)
        if ctx.job.attempt == 1:
            raise RuntimeError()

    doc = await upload(committed, volume, owner)
    seen.append((await doc_of(committed, doc.id)).status)
    worker = make_worker(committed, volume, migrated_database, flaky)
    await worker.run_once()
    seen.append((await doc_of(committed, doc.id)).status)
    await make_due(committed, (await job_of(committed, doc.id)).id)
    await worker.run_once()
    seen.append((await doc_of(committed, doc.id)).status)
    assert [s.value for s in seen] == ["queued", "processing", "queued", "processing", "done"]
    job = await job_of(committed, doc.id)
    assert job.status is JobStatus.SUCCEEDED and job.attempts == 2


async def _expire(committed: Sessions, job_id: uuid.UUID, **values: Any) -> None:
    async with committed() as session:
        await session.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(locked_until=func.now() - text("interval '1 second'"), **values)
        )
        await session.commit()


async def test_lease_expiry(
    committed: Sessions, volume: LocalVolume, owner: CommittedUser, migrated_database: str
) -> None:
    doc = await upload(committed, volume, owner)
    claimed = await runner.claim(committed, worker_id="killed-worker", lease_seconds=60)
    assert claimed is not None and claimed.attempt == 1
    await _expire(committed, claimed.id)

    other = make_worker(committed, volume, migrated_database, worker_id="other-worker")
    again = await other.claim()
    assert again is not None and again.id == claimed.id
    assert again.attempt == 2 and again.reclaimed
    job = await job_of(committed, doc.id)
    assert job.locked_by == "other-worker" and job.attempts == 2

    await _expire(committed, claimed.id, attempts=Job.max_attempts)
    assert await other.run_once() is False
    job = await job_of(committed, doc.id)
    assert (job.status, job.last_error_kind) == (JobStatus.FAILED, "LeaseExpired")
    stored = await doc_of(committed, doc.id)
    assert (stored.status, stored.error_kind) == (DocumentStatus.FAILED, "LeaseExpired")


async def test_fencing(
    committed: Sessions,
    volume: LocalVolume,
    owner: CommittedUser,
    migrated_database: str,
    json_log: io.StringIO,
) -> None:
    async def taken_over(ctx: JobContext) -> None:
        async with committed() as session:  # another worker re-claims after a lost lease
            await session.execute(
                update(Job)
                .where(Job.id == ctx.job.id)
                .values(locked_by="second-worker", attempts=Job.attempts + 1)
            )
            await session.commit()

    doc = await upload(committed, volume, owner)
    worker = make_worker(committed, volume, migrated_database, taken_over, worker_id="first")
    await worker.run_once()
    job = await job_of(committed, doc.id)
    assert (job.status, job.locked_by, job.attempts) == (JobStatus.RUNNING, "second-worker", 2)
    assert (await doc_of(committed, doc.id)).status is DocumentStatus.PROCESSING
    events = [json.loads(line)["event"] for line in json_log.getvalue().splitlines()]
    assert "job.lease_lost" in events


async def test_heartbeat_keeps_the_lease(
    committed: Sessions, volume: LocalVolume, owner: CommittedUser, migrated_database: str
) -> None:
    async def slow(ctx: JobContext) -> None:
        await asyncio.sleep(7)

    doc = await upload(committed, volume, owner)
    first = make_worker(committed, volume, migrated_database, slow, job_lease_seconds=3)
    second = make_worker(committed, volume, migrated_database, slow, job_lease_seconds=3)
    stolen: list[Any] = []

    async def poll() -> None:
        while not done.is_set():
            got = await second.claim()
            if got is not None:
                stolen.append(got)
            await asyncio.sleep(0.25)

    done = asyncio.Event()
    poller = asyncio.create_task(poll())
    try:
        await first.run_once()
    finally:
        done.set()
        await poller
    assert stolen == []
    job = await job_of(committed, doc.id)
    assert job.status is JobStatus.SUCCEEDED and job.attempts == 1


async def test_timeout_is_retried(
    committed: Sessions, volume: LocalVolume, owner: CommittedUser, migrated_database: str
) -> None:
    async def sleeper(ctx: JobContext) -> None:
        await asyncio.sleep(5)

    doc = await upload(committed, volume, owner)
    worker = make_worker(committed, volume, migrated_database, sleeper, job_timeout_seconds=1)
    await worker.run_once()
    job = await job_of(committed, doc.id)
    assert (job.status, job.last_error_kind) == (JobStatus.QUEUED, "JobTimeout")


async def test_two_workers_twenty_jobs(
    committed: Sessions, volume: LocalVolume, owner: CommittedUser, migrated_database: str
) -> None:
    ran: list[tuple[str, uuid.UUID]] = []

    def recorder(name: str) -> Callable[[JobContext], Any]:
        async def handler(ctx: JobContext) -> None:
            ran.append((name, ctx.job.id))
            await asyncio.sleep(0.02)
            await check_stored_file(ctx)

        return handler

    for _ in range(20):
        await upload(committed, volume, owner)
    workers = [
        make_worker(committed, volume, migrated_database, recorder(name), worker_id=name)
        for name in ("w1", "w2")
    ]
    loops = [asyncio.create_task(w.run()) for w in workers]
    try:
        async with asyncio.timeout(30):
            while True:
                async with committed() as session:
                    done = (
                        await session.execute(
                            select(func.count())
                            .select_from(Job)
                            .where(Job.status == JobStatus.SUCCEEDED)
                        )
                    ).scalar_one()
                if done == 20:
                    break
                await asyncio.sleep(0.1)
    finally:
        for w in workers:
            w.stop()
        await asyncio.gather(*loops)
    ids = [job_id for _, job_id in ran]
    assert len(ids) == 20 and len(set(ids)) == 20
    assert {name for name, _ in ran} == {"w1", "w2"}


async def test_enqueue_dedupe_and_rollback(
    committed: Sessions, volume: LocalVolume, owner: CommittedUser
) -> None:
    doc = await upload(committed, volume, owner)
    async with committed() as session:
        again = await enqueue(
            session,
            kind=JobKind.PROCESS_DOCUMENT,
            household_id=owner.household_id,
            document_id=doc.id,
        )
        await session.commit()
    assert again is None
    async with committed() as session:
        count = await session.execute(
            select(func.count()).select_from(Job).where(Job.document_id == doc.id)
        )
        assert count.scalar_one() == 1
        await session.execute(
            update(Job).where(Job.document_id == doc.id).values(status=JobStatus.SUCCEEDED)
        )
        await session.commit()
    async with committed() as session:
        created = await enqueue(
            session,
            kind=JobKind.PROCESS_DOCUMENT,
            household_id=owner.household_id,
            document_id=doc.id,
        )
        assert created is not None
        await session.rollback()
    async with committed() as session:
        count = await session.execute(
            select(func.count()).select_from(Job).where(Job.document_id == doc.id)
        )
        assert count.scalar_one() == 1


async def test_loop_survives_db_outage(
    committed: Sessions,
    volume: LocalVolume,
    owner: CommittedUser,
    migrated_database: str,
    json_log: io.StringIO,
) -> None:
    doc = await upload(committed, volume, owner)
    failures = {"left": 4}

    def flaky_sessions() -> AsyncSession:
        if failures["left"] > 0:
            failures["left"] -= 1
            raise OperationalError("SELECT 1", {}, ConnectionRefusedError("db down"))
        return committed()

    worker = make_worker(committed, volume, migrated_database)
    worker.sessionmaker = flaky_sessions  # type: ignore[assignment]
    loop = asyncio.create_task(worker.run())
    try:
        async with asyncio.timeout(15):
            while (await job_of(committed, doc.id)).status is not JobStatus.SUCCEEDED:
                await asyncio.sleep(0.05)
    finally:
        worker.stop()
        await loop
    records = [json.loads(line) for line in json_log.getvalue().splitlines()]
    errors = [r for r in records if r["event"] == "worker.loop_error"]
    assert errors and all(r["error_kind"] == "OperationalError" for r in errors)
    assert all("db down" not in line for line in json_log.getvalue().splitlines())
