"""Document ingest and delete (#6 Decisions 7 and 12). Used by the api and later by #11.

Write order for crash consistency: stream to a temp file → validate → dedupe → insert
document + job + audit (flush) → move the file to its key → commit. A crash leaves at most
a file without a row (removed by the sweep), never a row without a file.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterable
from dataclasses import dataclass

import structlog
from sqlalchemy.exc import IntegrityError

from app.db.audit import Actor, record, snapshot
from app.db.models import Document, Job, TaxItem
from app.db.scope import HouseholdScope
from app.documents.sniff import detect_type
from app.domain.enums import AuditAction, Channel, DocumentStatus, JobKind, JobStatus
from app.queue.enqueue import DEFAULT_MAX_ATTEMPTS, enqueue
from app.storage import ObjectExists, Storage, document_key, document_prefix

log = structlog.stdlib.get_logger("app.documents")


class IngestError(Exception):
    reason = "error"


class EmptyFile(IngestError):
    reason = "empty"


class UnsupportedType(IngestError):
    reason = "unsupported_type"


class DocumentBusy(Exception):
    """The document's job is running; it cannot be deleted now."""


@dataclass(frozen=True)
class IngestResult:
    document: Document
    created: bool
    requeued: bool


async def _by_sha(scope: HouseholdScope, sha256: str) -> Document | None:
    stmt = scope.select(Document).where(Document.sha256 == sha256)
    return (await scope.session.execute(stmt)).scalar_one_or_none()


async def _requeue_if_failed(
    scope: HouseholdScope,
    doc: Document,
    user_id: uuid.UUID,
    *,
    trace_context: str | None,
    max_attempts: int,
) -> bool:
    """Re-queue a `failed` document (a family member's retry). Locks the document first."""
    session = scope.session
    locked = scope.select(Document).where(Document.id == doc.id).with_for_update()
    await session.execute(locked.execution_options(populate_existing=True))
    if doc.status is not DocumentStatus.FAILED:
        return False
    before = snapshot(doc)
    doc.status = DocumentStatus.QUEUED
    doc.error_kind = None
    await enqueue(
        session,
        kind=JobKind.PROCESS_DOCUMENT,
        household_id=scope.household_id,
        document_id=doc.id,
        trace_context=trace_context,
        max_attempts=max_attempts,
    )
    await session.flush()
    await record(
        session,
        household_id=scope.household_id,
        entity="document",
        entity_id=doc.id,
        action=AuditAction.UPDATE,
        before=before,
        after=snapshot(doc),
        actor=Actor.user(user_id),
    )
    await session.commit()
    return True


async def _duplicate(
    scope: HouseholdScope,
    doc: Document,
    user_id: uuid.UUID,
    trace_context: str | None,
    max_attempts: int,
) -> IngestResult:
    requeued = await _requeue_if_failed(
        scope, doc, user_id, trace_context=trace_context, max_attempts=max_attempts
    )
    return IngestResult(doc, created=False, requeued=requeued)


async def ingest_document(
    scope: HouseholdScope,
    user_id: uuid.UUID,
    chunks: AsyncIterable[bytes],
    *,
    channel: Channel,
    storage: Storage,
    max_bytes: int,
    original_filename: str | None = None,
    trace_context: str | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> IngestResult:
    """Store an upload and queue its job. Raises `ObjectTooLarge`, `StorageFull`, `EmptyFile`,
    `UnsupportedType`; in every error case no temp file, row or audit entry remains.

    `original_filename` must already be sanitised (`sanitize_filename`).
    """
    session = scope.session
    staged = await storage.stage(chunks, max_bytes=max_bytes)
    try:
        if staged.size == 0:
            raise EmptyFile()
        file_type = detect_type(staged.head)
        if file_type is None:
            raise UnsupportedType()
        existing = await _by_sha(scope, staged.sha256)
    except BaseException:
        await storage.discard(staged)
        raise
    if existing is not None:
        await storage.discard(staged)
        return await _duplicate(scope, existing, user_id, trace_context, max_attempts)

    document_id = uuid.uuid4()
    key = document_key(scope.household_id, document_id)
    doc = Document(
        id=document_id,
        uploaded_by_user_id=user_id,
        channel=channel,
        sha256=staged.sha256,
        mime_type=file_type.mime_type,
        size_bytes=staged.size,
        storage_key=key,
        status=DocumentStatus.QUEUED,
        original_filename=original_filename,
    )
    try:
        scope.add(doc)
        await session.flush()
        await enqueue(
            session,
            kind=JobKind.PROCESS_DOCUMENT,
            household_id=scope.household_id,
            document_id=document_id,
            trace_context=trace_context,
            max_attempts=max_attempts,
        )
        await record(
            session,
            household_id=scope.household_id,
            entity="document",
            entity_id=document_id,
            action=AuditAction.CREATE,
            before=None,
            after=snapshot(doc),
            actor=Actor.user(user_id),
        )
    except IntegrityError:
        # The same bytes were uploaded at the same moment: the other request won.
        await session.rollback()
        await storage.discard(staged)
        winner = await _by_sha(scope, staged.sha256)
        if winner is None:
            raise
        return await _duplicate(scope, winner, user_id, trace_context, max_attempts)
    except BaseException:
        await session.rollback()
        await storage.discard(staged)
        raise

    try:
        await storage.commit(staged, key)
    except BaseException as exc:
        await session.rollback()
        await storage.discard(staged)
        if not isinstance(exc, ObjectExists):  # a partial move may have left the file
            await storage.delete_prefix(document_prefix(scope.household_id, document_id))
        raise
    try:
        await session.commit()
    except BaseException:
        await storage.delete_prefix(document_prefix(scope.household_id, document_id))
        raise
    return IngestResult(doc, created=True, requeued=False)


async def delete_document(
    scope: HouseholdScope, document_id: uuid.UUID, user_id: uuid.UUID, storage: Storage
) -> bool:
    """Delete a document (row, jobs, extractions and tax items via cascades) and then its files.

    `False` if it does not exist in this household; `DocumentBusy` while its job runs.
    Locks the document row before any job row (same order as the worker's claim).
    """
    session = scope.session
    stmt = scope.select(Document).where(Document.id == document_id).with_for_update()
    doc = (await session.execute(stmt)).scalar_one_or_none()
    if doc is None:
        return False
    running = await session.execute(
        scope.select(Job)
        .where(Job.document_id == doc.id, Job.status == JobStatus.RUNNING)
        .with_only_columns(Job.id)
    )
    if running.first() is not None:
        await session.rollback()
        raise DocumentBusy()
    # #10 Decision 10: the items go with the document (CASCADE); the audit trail stays.
    items = await session.execute(scope.select(TaxItem).where(TaxItem.document_id == doc.id))
    for item in items.scalars():
        await record(
            session,
            household_id=scope.household_id,
            entity="tax_item",
            entity_id=item.id,
            action=AuditAction.DELETE,
            before=snapshot(item),
            after=None,
            actor=Actor.user(user_id),
        )
    before = snapshot(doc)
    storage_key = doc.storage_key
    await session.delete(doc)
    await session.flush()
    await record(
        session,
        household_id=scope.household_id,
        entity="document",
        entity_id=document_id,
        action=AuditAction.DELETE,
        before=before,
        after=None,
        actor=Actor.user(user_id),
    )
    await session.commit()
    # After the commit; if this fails, the sweep removes the orphan directory later.
    expected = document_key(scope.household_id, document_id)
    if storage_key == expected:
        try:
            await storage.delete_prefix(document_prefix(scope.household_id, document_id))
        except Exception as exc:  # the sweep removes it later
            log.warning("document.files_not_deleted", error_kind=type(exc).__name__)
    return True


__all__ = [
    "DocumentBusy",
    "EmptyFile",
    "IngestError",
    "IngestResult",
    "UnsupportedType",
    "delete_document",
    "ingest_document",
]
