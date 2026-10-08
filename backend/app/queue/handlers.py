"""Job handlers: `{JobKind: handler}`.

Contract: a handler is idempotent (a job can run again after a crash or a lost lease) and
raises `PermanentJobError` for non-retryable failures. The runner sets `processing` /
`queued` / `failed`; on success the document ends `done`, unless the handler finished it
as `needs_attention` itself (#9).
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
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
    settings: Settings | None = None


Handler = Callable[[JobContext], Awaitable[None]]


async def read_stored_file(ctx: JobContext, storage_key: str, sha256: str) -> bytes:
    """The document's bytes through `Storage` (#33's wrapper applies), checksum verified."""
    try:
        reader = await ctx.storage.open(storage_key)
    except (ObjectNotFound, InvalidStorageKey):
        raise FileMissing() from None
    digest = hashlib.sha256()
    chunks: list[bytes] = []
    try:
        async for chunk in reader:
            digest.update(chunk)
            chunks.append(chunk)
    finally:
        await reader.aclose()
    if digest.hexdigest() != sha256:
        raise ChecksumMismatch()
    return b"".join(chunks)


async def load_document(ctx: JobContext) -> Document:
    job = ctx.job
    if job.document_id is None:
        raise DocumentMissing()
    async with ctx.sessionmaker() as session:
        doc = await HouseholdScope(session, job.household_id).get(Document, job.document_id)
        if doc is None:
            raise DocumentMissing()
        session.expunge(doc)
        return doc


async def check_stored_file(ctx: JobContext) -> None:
    """#6's stub check: re-hash the stored file and compare it with `document.sha256`."""
    doc = await load_document(ctx)
    await read_stored_file(ctx, doc.storage_key, doc.sha256)


def default_handlers() -> dict[JobKind, Handler]:
    from app.pipeline.handler import process_document  # pipeline imports the queue

    return {JobKind.PROCESS_DOCUMENT: process_document}


__all__ = [
    "Handler",
    "JobContext",
    "check_stored_file",
    "default_handlers",
    "load_document",
    "read_stored_file",
]
