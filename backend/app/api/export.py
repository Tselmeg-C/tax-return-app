"""`GET /export/{year}.pdf|csv|zip` (#21): the report, the receipt list and the originals.

Behind the normal session; every query goes through the household scope. The files only render
`build_summary` (`app.export`). Names are fixed (nothing user-controlled), responses are
`no-store`. Logs and metrics carry counts only: never a vendor, amount or file name.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date
from zoneinfo import ZoneInfo

import structlog
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from opentelemetry import metrics

from app.api.deps import Scope, SignedIn
from app.auth.clock import Clock
from app.config import Settings
from app.documents.filenames import content_disposition
from app.export.archive import Archive, build_archive
from app.export.csv_file import csv_bytes
from app.export.data import ExportData, load_export
from app.export.pdf import pdf_bytes
from app.storage import Storage
from app.tax_params import UnsupportedTaxYear

router = APIRouter()
log = structlog.stdlib.get_logger("app.export")
BERLIN = ZoneInfo("Europe/Berlin")
CHUNK = 256 * 1024
HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


def _count(fmt: str, result: str) -> None:
    metrics.get_meter("belegbot").create_counter(
        "belegbot.export", unit="{export}", description="Export downloads"
    ).add(1, {"format": fmt, "result": result})


def _attachment(name: str) -> dict[str, str]:
    return {
        **HEADERS,
        "Content-Disposition": content_disposition(name, extended=False, kind="attachment"),
    }


async def _load(request: Request, scope: Scope, year: int) -> ExportData | JSONResponse:
    clock: Clock = request.app.state.clock
    today: date = clock.now().astimezone(BERLIN).date()
    try:
        data = await load_export(scope, year, today)
    except UnsupportedTaxYear:
        return JSONResponse(
            {"detail": "unsupported_year", "field": "year"}, status_code=422, headers=HEADERS
        )
    if data is None:
        return JSONResponse({"detail": "no_profile"}, status_code=409, headers=HEADERS)
    return data


@router.get("/export/{year}.pdf")
async def export_pdf(request: Request, user: SignedIn, scope: Scope, year: int) -> Response:
    data = await _load(request, scope, year)
    if isinstance(data, JSONResponse):
        return data
    body = pdf_bytes(data)
    log.info(
        "export.built", year=year, format="pdf", items=len(data.lines), bytes=len(body), missing=0
    )
    _count("pdf", "ok")
    return Response(
        body, media_type="application/pdf", headers=_attachment(f"belegbot-{year}-bericht.pdf")
    )


@router.get("/export/{year}.csv")
async def export_csv(request: Request, user: SignedIn, scope: Scope, year: int) -> Response:
    data = await _load(request, scope, year)
    if isinstance(data, JSONResponse):
        return data
    body = csv_bytes(data)
    log.info(
        "export.built", year=year, format="csv", items=len(data.lines), bytes=len(body), missing=0
    )
    _count("csv", "ok")
    return Response(
        body,
        media_type="text/csv; charset=utf-8",
        headers=_attachment(f"belegbot-{year}-belege.csv"),
    )


async def _stream(archive: Archive) -> AsyncIterator[bytes]:
    try:
        while chunk := archive.file.read(CHUNK):
            yield chunk
    finally:
        archive.close()


@router.get("/export/{year}.zip")
async def export_zip(request: Request, user: SignedIn, scope: Scope, year: int) -> Response:
    data = await _load(request, scope, year)
    if isinstance(data, JSONResponse):
        return data
    settings: Settings = request.app.state.settings
    storage: Storage = request.app.state.storage
    by_doc = {line.document_id: line.size_bytes for line in data.lines if line.document_id}
    if sum(by_doc.values()) > settings.export_zip_max_bytes:
        _count("zip", "too_large")
        return JSONResponse(
            {"detail": "export_too_large", "max_bytes": settings.export_zip_max_bytes},
            status_code=413,
            headers=HEADERS,
        )
    archive = await build_archive(data, storage)
    log.info(
        "export.built",
        year=year,
        format="zip",
        items=len(data.lines),
        bytes=archive.size,
        missing=archive.missing,
    )
    _count("zip", "ok")
    return StreamingResponse(
        _stream(archive),
        media_type="application/zip",
        headers={
            **_attachment(f"belegbot-{year}-belege.zip"),
            "Content-Length": str(archive.size),
            "X-Export-Missing": str(archive.missing),
        },
    )
