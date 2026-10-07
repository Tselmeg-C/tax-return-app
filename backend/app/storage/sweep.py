"""Sweep (#6 Decision 11): stale temp files, orphan document directories, old jobs.

- temp files in `<STORAGE_PATH>/tmp/` older than 1 h
- `households/<h>/documents/<d>` directories without a `document` row whose newest file is
  older than 1 h (the grace period protects uploads in flight)
- `succeeded` jobs that finished more than 30 days ago

Logs and prints counts and document ids only.
`uv run python -m app.storage.sweep [--dry-run]`
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
import uuid
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import timedelta

import structlog
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Document, Job
from app.db.scope import HouseholdScope
from app.domain.enums import JobStatus
from app.storage.local import DocumentDir, LocalVolume

TMP_MAX_AGE = timedelta(hours=1)
ORPHAN_MAX_AGE = timedelta(hours=1)
JOB_RETENTION = timedelta(days=30)

log = structlog.stdlib.get_logger("app.storage.sweep")


@dataclass
class SweepResult:
    tmp_files: int = 0
    orphan_documents: list[uuid.UUID] = field(default_factory=list)
    old_jobs: int = 0


async def _orphans(session: AsyncSession, dirs: list[DocumentDir]) -> list[DocumentDir]:
    by_household: dict[uuid.UUID, list[DocumentDir]] = defaultdict(list)
    for d in dirs:
        by_household[d.household_id].append(d)
    orphans: list[DocumentDir] = []
    for household_id, entries in by_household.items():
        ids = [d.document_id for d in entries]
        stmt = HouseholdScope(session, household_id).select(Document).where(Document.id.in_(ids))
        known = set((await session.execute(stmt.with_only_columns(Document.id))).scalars())
        orphans.extend(d for d in entries if d.document_id not in known)
    return orphans


async def sweep(
    volume: LocalVolume,
    sessionmaker: Callable[[], AsyncSession],
    *,
    dry_run: bool = False,
) -> SweepResult:
    result = SweepResult()
    result.tmp_files = await volume.sweep_tmp(TMP_MAX_AGE, dry_run=dry_run)

    cutoff = time.time() - ORPHAN_MAX_AGE.total_seconds()
    old_dirs = [d for d in await asyncio.to_thread(volume.document_dirs) if d.newest_mtime < cutoff]
    async with sessionmaker() as session:
        for orphan in await _orphans(session, old_dirs):
            result.orphan_documents.append(orphan.document_id)
            if not dry_run:
                await volume.delete_prefix(orphan.prefix)

        # System-level cleanup across households (the queue is global).
        old = (Job.status == JobStatus.SUCCEEDED) & (
            func.coalesce(Job.finished_at, Job.updated_at) < func.now() - JOB_RETENTION
        )
        if dry_run:
            count = await session.execute(select(func.count()).select_from(Job).where(old))
            result.old_jobs = int(count.scalar_one())
        else:
            deleted = await session.execute(delete(Job).where(old).returning(Job.id))
            result.old_jobs = len(deleted.all())
            await session.commit()

    log.info(
        "storage.sweep",
        dry_run=dry_run,
        tmp_files=result.tmp_files,
        orphan_documents=len(result.orphan_documents),
        old_jobs=result.old_jobs,
    )
    return result


async def _main(dry_run: bool) -> int:
    from app.config import get_settings
    from app.db.session import create_engine, create_sessionmaker

    settings = get_settings()
    volume = LocalVolume(settings.resolved_storage_path)
    engine = create_engine(settings)
    try:
        result = await sweep(volume, create_sessionmaker(engine), dry_run=dry_run)
    finally:
        await engine.dispose()
    verb = "would delete" if dry_run else "deleted"
    print(f"{verb}: {result.tmp_files} temp file(s)")
    print(f"{verb}: {len(result.orphan_documents)} orphan document dir(s)")
    for document_id in result.orphan_documents:
        print(f"  {document_id}")
    print(f"{verb}: {result.old_jobs} succeeded job(s) older than 30 days")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.storage.sweep")
    parser.add_argument("--dry-run", action="store_true", help="only print what would go")
    args = parser.parse_args(argv)
    return asyncio.run(_main(args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
