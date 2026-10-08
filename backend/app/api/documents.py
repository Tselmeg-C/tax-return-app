"""Documents api (#6): raw-body upload, list, poll, download, delete.

Error bodies, logs, spans and metrics never contain file bytes or the file name; the name
appears only in `DocumentOut` and the download's `Content-Disposition`.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from app.api.deps import Scope, SignedIn
from app.config import Settings
from app.db.models import Document, TaxItem
from app.documents.filenames import content_disposition, download_name, sanitize_filename
from app.documents.service import (
    DocumentBusy,
    EmptyFile,
    UnsupportedType,
    delete_document,
    ingest_document,
)
from app.domain.enums import AttentionReason, Channel, DocType, DocumentStatus
from app.queue.enqueue import current_traceparent
from app.queue.metrics import UploadMetrics
from app.storage import ObjectNotFound, ObjectTooLarge, Storage, StorageFull
from app.storage.base import InvalidStorageKey

router = APIRouter()
log = structlog.stdlib.get_logger("app.api.documents")

NOT_FOUND = "not_found"


class DocumentOut(BaseModel):
    id: uuid.UUID
    status: DocumentStatus
    original_filename: str | None
    mime_type: str
    size_bytes: int
    channel: Channel
    doc_type: DocType | None
    error_kind: str | None
    attention_reason: AttentionReason | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, doc: Document) -> DocumentOut:
        return cls(
            id=doc.id,
            status=doc.status,
            original_filename=doc.original_filename,
            mime_type=doc.mime_type,
            size_bytes=doc.size_bytes,
            channel=doc.channel,
            doc_type=doc.doc_type,
            error_kind=doc.error_kind,
            attention_reason=doc.attention_reason,
            created_at=doc.created_at,
            updated_at=doc.updated_at,
        )


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def _storage(request: Request) -> Storage:
    storage: Storage = request.app.state.storage
    return storage


def _metrics(request: Request) -> UploadMetrics:
    metrics: UploadMetrics = request.app.state.upload_metrics
    return metrics


def _reject(request: Request, status: int, outcome: str, body: dict[str, Any]) -> JSONResponse:
    log.info("document.rejected", reason=outcome)
    _metrics(request).record(outcome)
    return JSONResponse(body, status_code=status)


@router.post("/documents", status_code=201)
async def upload_document(request: Request, user: SignedIn, scope: Scope) -> JSONResponse:
    settings = _settings(request)
    max_bytes = settings.max_upload_bytes
    too_large = {"detail": "too_large", "max_bytes": max_bytes}
    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > max_bytes:
        return _reject(request, 413, "too_large", too_large)  # the body is never read
    name = sanitize_filename(request.headers.get("x-filename"))
    try:
        result = await ingest_document(
            scope,
            user.user_id,
            request.stream(),
            channel=Channel.WEB,
            storage=_storage(request),
            max_bytes=max_bytes,
            original_filename=name,
            trace_context=current_traceparent(),
            max_attempts=settings.job_max_attempts,
        )
    except ObjectTooLarge:
        return _reject(request, 413, "too_large", too_large)
    except UnsupportedType:
        return _reject(request, 415, "unsupported_type", {"detail": "unsupported_type"})
    except EmptyFile:
        return _reject(request, 422, "empty", {"detail": "empty_file"})
    except StorageFull:
        return _reject(request, 507, "storage_full", {"detail": "storage_full"})

    doc = result.document
    log.info(
        "document.uploaded",
        document_id=str(doc.id),
        mime_type=doc.mime_type,
        size_bytes=doc.size_bytes,
        duplicate=not result.created,
        requeued=result.requeued,
    )
    _metrics(request).record("created" if result.created else "duplicate", doc.mime_type)
    body: dict[str, Any] = {
        "document": DocumentOut.of(doc).model_dump(mode="json"),
        "duplicate": not result.created,
    }
    if not result.created:
        body["requeued"] = result.requeued
    return JSONResponse(body, status_code=201 if result.created else 200)


def _parse_statuses(raw: str | None) -> list[DocumentStatus] | None:
    if raw is None or not raw.strip():
        return None
    try:
        return [DocumentStatus(part.strip()) for part in raw.split(",") if part.strip()]
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid_request") from None


@router.get("/documents")
async def list_documents(
    scope: Scope,
    status: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    without_tax_item: Annotated[bool, Query()] = False,
) -> dict[str, list[DocumentOut]]:
    stmt = scope.select(Document)
    statuses = _parse_statuses(status)
    if statuses:
        stmt = stmt.where(Document.status.in_(statuses))
    if without_tax_item:  # #10: in flight, failed and needs-attention documents without item
        has_item = scope.select(TaxItem).where(TaxItem.document_id == Document.id).exists()
        stmt = stmt.where(~has_item)
    stmt = stmt.order_by(Document.created_at.desc(), Document.id.desc()).limit(limit)
    docs = (await scope.session.execute(stmt)).scalars().all()
    return {"documents": [DocumentOut.of(doc) for doc in docs]}


async def _get(scope: Scope, document_id: uuid.UUID) -> Document:
    doc = await scope.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return doc


@router.get("/documents/{document_id}")
async def get_document(scope: Scope, document_id: uuid.UUID) -> DocumentOut:
    return DocumentOut.of(await _get(scope, document_id))


@router.get("/documents/{document_id}/file")
async def download_document(
    request: Request, scope: Scope, document_id: uuid.UUID
) -> StreamingResponse:
    doc = await _get(scope, document_id)
    try:
        reader = await _storage(request).open(doc.storage_key)
    except (ObjectNotFound, InvalidStorageKey):
        raise HTTPException(status_code=404, detail="file_not_found") from None
    name = download_name(doc.original_filename, doc.mime_type, doc.id)

    async def body() -> AsyncIterator[bytes]:
        try:
            async for chunk in reader:
                yield chunk
        finally:
            await reader.aclose()

    return StreamingResponse(
        body(),
        media_type=doc.mime_type,
        headers={
            "Content-Length": str(reader.size),
            "Content-Disposition": content_disposition(
                name, extended=doc.original_filename is not None
            ),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@router.delete("/documents/{document_id}", status_code=204)
async def remove_document(
    request: Request, user: SignedIn, scope: Scope, document_id: uuid.UUID
) -> Response:
    try:
        found = await delete_document(scope, document_id, user.user_id, _storage(request))
    except DocumentBusy:
        raise HTTPException(status_code=409, detail="document_busy") from None
    if not found:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    log.info("document.deleted", document_id=str(document_id))
    return Response(status_code=204)
