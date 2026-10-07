"""Queue ops CLI: `uv run python -m app.queue.cli stats | retry <job_id>`. Prints ids and counts."""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from collections.abc import Sequence
from typing import TextIO

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Document, Job
from app.domain.enums import DocumentStatus, JobStatus
from app.queue.runner import queue_stats


async def stats(session: AsyncSession, out: TextIO) -> int:
    rows = await session.execute(
        select(Job.kind, Job.status, func.count()).group_by(Job.kind, Job.status).order_by(Job.kind)
    )
    for kind, status, count in rows.all():
        print(f"{kind.value} {status.value} {count}", file=out)
    _, oldest = await queue_stats(session)
    print(f"oldest_queued_age_s {oldest:.0f}", file=out)
    return 0


async def retry(session: AsyncSession, job_id: uuid.UUID, out: TextIO) -> int:
    job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one_or_none()
    if job is None or job.status is not JobStatus.FAILED:
        print(f"job {job_id}: not found or not failed", file=out)
        return 1
    try:
        if job.document_id is not None:
            await session.execute(
                update(Document)
                .where(Document.id == job.document_id, Document.household_id == job.household_id)
                .values(status=DocumentStatus.QUEUED, error_kind=None)
            )
        job.status = JobStatus.QUEUED
        job.attempts = 0
        job.run_after = func.now()
        job.finished_at = None
        await session.commit()
    except IntegrityError:  # another job of this document is already active
        await session.rollback()
        print(f"job {job_id}: document already has an active job", file=out)
        return 1
    print(f"job {job_id}: queued", file=out)
    return 0


async def run(argv: Sequence[str], out: TextIO = sys.stdout) -> int:
    from app.config import get_settings
    from app.db.session import create_engine, create_sessionmaker

    parser = argparse.ArgumentParser(prog="python -m app.queue.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("stats", help="jobs per kind and status, age of the oldest queued job")
    retry_parser = sub.add_parser("retry", help="re-queue a failed job (attempts reset)")
    retry_parser.add_argument("job_id", type=uuid.UUID)
    args = parser.parse_args(argv)

    engine = create_engine(get_settings())
    try:
        async with create_sessionmaker(engine)() as session:
            if args.command == "stats":
                return await stats(session, out)
            return await retry(session, args.job_id, out)
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(run(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    sys.exit(main())
