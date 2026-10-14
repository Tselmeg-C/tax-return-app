"""Document previews (#35): `preview.jpg` next to the original, written through `Storage`.

`ensure_preview` runs as a best-effort step of the `process_document` job and in the backfill
(`uv run python -m app.documents.previews [--dry-run]`). It never raises for a bad file and
never leaves a partial preview. Logs carry ids and error class names only.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Document, Household
from app.db.scope import HouseholdScope
from app.pipeline.preprocess import DEFAULT_LIMITS, Limits, preview_jpeg
from app.storage import ObjectExists, Storage, preview_key

log = structlog.stdlib.get_logger("app.documents.previews")


async def _chunks(data: bytes) -> AsyncIterator[bytes]:
    yield data


async def ensure_preview(
    storage: Storage,
    household_id: uuid.UUID,
    document_id: uuid.UUID,
    data: bytes,
    mime_type: str,
    limits: Limits = DEFAULT_LIMITS,
) -> bool:
    """Write the preview unless it exists. `True` if one exists afterwards; never raises."""
    key = preview_key(household_id, document_id)
    try:
        if await storage.exists(key):
            return True
        jpeg = await asyncio.to_thread(preview_jpeg, data, mime_type, limits)
        staged = await storage.stage(_chunks(jpeg), max_bytes=len(jpeg))
        try:
            await storage.commit(staged, key)
        except ObjectExists:  # a concurrent run won
            await storage.discard(staged)
        except BaseException:
            await storage.discard(staged)
            raise
        return True
    except Exception as exc:  # a preview never fails the document
        log.warning("preview.failed", document_id=str(document_id), error_kind=type(exc).__name__)
        return False


@dataclass
class BackfillResult:
    created: int = 0
    existing: int = 0
    failed: int = 0


async def backfill(
    storage: Storage,
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    dry_run: bool = False,
    limits: Limits = DEFAULT_LIMITS,
) -> BackfillResult:
    result = BackfillResult()
    async with sessionmaker() as session:
        household_ids = list((await session.execute(select(Household.id))).scalars())
        for household_id in household_ids:
            scope = HouseholdScope(session, household_id)
            docs = (await session.execute(scope.select(Document))).scalars().all()
            for doc in docs:
                if await storage.exists(preview_key(household_id, doc.id)):
                    result.existing += 1
                    continue
                if dry_run:
                    result.created += 1
                    continue
                try:
                    reader = await storage.open(doc.storage_key)
                    try:
                        data = b"".join([chunk async for chunk in reader])
                    finally:
                        await reader.aclose()
                except Exception as exc:
                    log.warning(
                        "preview.failed", document_id=str(doc.id), error_kind=type(exc).__name__
                    )
                    result.failed += 1
                    continue
                if await ensure_preview(storage, household_id, doc.id, data, doc.mime_type, limits):
                    result.created += 1
                else:
                    result.failed += 1
    log.info(
        "previews.backfill",
        dry_run=dry_run,
        created=result.created,
        existing=result.existing,
        failed=result.failed,
    )
    return result


async def _main(dry_run: bool) -> int:
    from app.config import get_settings
    from app.db.session import create_engine, create_sessionmaker
    from app.storage import storage_from_settings

    settings = get_settings()
    engine = create_engine(settings)
    try:
        result = await backfill(
            storage_from_settings(settings), create_sessionmaker(engine), dry_run=dry_run
        )
    finally:
        await engine.dispose()
    verb = "would create" if dry_run else "created"
    print(
        f"{verb}: {result.created} preview(s); existing: {result.existing}; failed: {result.failed}"
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.documents.previews")
    parser.add_argument("--dry-run", action="store_true", help="only count what would be created")
    args = parser.parse_args(argv)
    return asyncio.run(_main(args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
