"""Job handlers: `{JobKind: handler}`.

Contract (kept by #9 when it replaces the stub): a handler is idempotent (a job can run again
after a crash or a lost lease), raises `PermanentJobError` for non-retryable failures, and
never sets the document status itself (the runner does: `processing` → `done` / `queued` /
`failed`).
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Document
from app.db.scope import HouseholdScope
from app.domain.enums import JobKind
from app.queue.errors import ChecksumMismatch, DocumentMissing, FileMissing
from app.queue.runner import ClaimedJob
from app.storage import InvalidStorageKey, ObjectNotFound, Storage


@dataclass(frozen=True)
class JobContext:
    job: ClaimedJob
    sessionmaker: async_sessionmaker[AsyncSession]
    storage: Storage


Handler = Callable[[JobContext], Awaitable[None]]


async def process_document(ctx: JobContext) -> None:
    """Stub (#6): re-hash the stored file and compare it with `document.sha256`."""
    job = ctx.job
    if job.document_id is None:
        raise DocumentMissing()
    async with ctx.sessionmaker() as session:
        doc = await HouseholdScope(session, job.household_id).get(Document, job.document_id)
        if doc is None:
            raise DocumentMissing()
        key, expected = doc.storage_key, doc.sha256
    try:
        reader = await ctx.storage.open(key)
    except (ObjectNotFound, InvalidStorageKey):
        raise FileMissing() from None
    digest = hashlib.sha256()
    try:
        async for chunk in reader:
            digest.update(chunk)
    finally:
        await reader.aclose()
    if digest.hexdigest() != expected:
        raise ChecksumMismatch()


def default_handlers() -> dict[JobKind, Handler]:
    return {JobKind.PROCESS_DOCUMENT: process_document}
