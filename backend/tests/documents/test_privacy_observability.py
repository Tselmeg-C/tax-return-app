"""No file bytes or file names in logs, spans, metrics, job rows or error bodies; metrics and
the upload → job span link exist (#6 Privacy / Observability criteria)."""

from __future__ import annotations

import io
import json
import secrets
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker

from app.auth.clock import FakeClock
from app.db.models import AuditLog, Job
from app.domain.enums import JobKind
from app.queue.errors import PermanentJobError
from app.queue.handlers import JobContext
from app.queue.metrics import QueueMetrics
from app.worker.loop import Worker
from tests.auth.conftest import auth_settings, running_app
from tests.documents import files
from tests.documents.conftest import Docs, metric_points
from tests.documents.test_worker import settings_for


@pytest.fixture
async def mdocs(
    migrated_database: str,
    db_connection: AsyncConnection,
    db_session: AsyncSession,
    clock: FakeClock,
    tmp_path: Path,
    meter_provider: MeterProvider,
) -> AsyncIterator[Docs]:
    root = tmp_path / "storage"
    settings = auth_settings(migrated_database, storage_path=root)
    async with running_app(
        settings, clock=clock, conn=db_connection, meter_provider=meter_provider
    ) as api:
        yield Docs(api=api, root=root, session=db_session, clock=clock)


def conn_worker(
    conn: AsyncConnection,
    docs: Docs,
    database_url: str,
    handler: Any = None,
    metrics: QueueMetrics | None = None,
) -> Worker:
    sessions = async_sessionmaker(
        bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
    )
    return Worker(
        sessionmaker=sessions,
        storage=docs.storage,
        settings=settings_for(database_url),
        handlers={JobKind.PROCESS_DOCUMENT: handler} if handler else None,
        metrics=metrics,
    )


def _span_texts(spans: InMemorySpanExporter) -> list[str]:
    texts: list[str] = []
    for span in spans.get_finished_spans():
        texts.append(span.name)
        texts.append(json.dumps(dict(span.attributes or {}), default=str))
        for event in span.events:
            texts.append(event.name)
            texts.append(json.dumps(dict(event.attributes or {}), default=str))
    return texts


async def test_sentinels_never_leak(
    mdocs: Docs,
    db_connection: AsyncConnection,
    migrated_database: str,
    json_log: io.StringIO,
    spans: InMemorySpanExporter,
    metric_reader: InMemoryMetricReader,
    meter_provider: MeterProvider,
) -> None:
    docs = mdocs
    content_sentinel = f"content-sentinel-{secrets.token_hex(8)}"
    name_sentinel = f"sentinel-name-{secrets.token_hex(8)}"
    header = f"UTF-8''{name_sentinel}.pdf"
    data = files.png(content_sentinel.encode())
    _, cookie = await docs.user()
    bodies: list[str] = []

    first = await docs.upload(data, cookie, filename=header, headers={"Content-Type": "image/png"})
    assert first.status_code == 201
    doc_id = first.json()["document"]["id"]
    assert first.json()["document"]["original_filename"] == f"{name_sentinel}.pdf"

    download = await docs.api.get(f"/documents/{doc_id}/file", cookie=cookie)
    assert name_sentinel in download.headers["content-disposition"]

    async def leaky(ctx: JobContext) -> None:
        raise PermanentJobError(f"failed on {content_sentinel} in {name_sentinel}")

    worker = conn_worker(
        db_connection, docs, migrated_database, leaky, QueueMetrics(meter_provider)
    )
    assert await worker.run_once()

    dup = await docs.upload(data, cookie, filename=header)
    assert dup.status_code == 200 and dup.json()["requeued"] is True
    bad = await docs.upload(files.rejected()["text"], cookie, filename=header)
    assert bad.status_code == 415
    bodies.append(bad.text)
    deleted = await docs.api.request("DELETE", f"/documents/{doc_id}", cookie=cookie)
    assert deleted.status_code == 204
    bodies.append(deleted.text)

    jobs = (await docs.session.execute(select(Job))).scalars().all()
    job_texts = [
        json.dumps({c.name: getattr(j, c.key) for c in Job.__table__.columns}, default=str)
        for j in jobs
    ]
    metric_texts = [
        json.dumps(dict(p.attributes or {}), default=str)
        for points in metric_points(metric_reader).values()
        for p in points
    ]
    haystacks = {
        "logs": [json_log.getvalue()],
        "spans": _span_texts(spans),
        "metrics": metric_texts,
        "jobs": job_texts,
        "bodies": bodies,
    }
    for where, texts in haystacks.items():
        for text in texts:
            assert content_sentinel not in text, where
            assert name_sentinel not in text, where
    # The failed attempt stored the class name only (the job row is gone with the document,
    # so check the audit snapshot of the deleted document instead).
    audits = (
        (await docs.session.execute(select(AuditLog).where(AuditLog.entity_id == doc_id)))
        .scalars()
        .all()
    )
    assert any(a.before and a.before.get("error_kind") == "PermanentJobError" for a in audits)
    assert any(
        (a.after or a.before or {}).get("original_filename") == f"{name_sentinel}.pdf"
        for a in audits
    )


async def test_metrics_and_span_link(
    mdocs: Docs,
    db_connection: AsyncConnection,
    migrated_database: str,
    spans: InMemorySpanExporter,
    metric_reader: InMemoryMetricReader,
    meter_provider: MeterProvider,
) -> None:
    docs = mdocs
    _, cookie = await docs.user()
    ok = await docs.upload(files.pdf(), cookie)
    trace_id = ok.headers["x-trace-id"]
    data = files.png()
    await docs.upload(data, cookie)
    assert (await docs.upload(data, cookie)).json()["duplicate"] is True

    calls = {"n": 0}

    async def first_ok_then_fail(ctx: JobContext) -> None:
        calls["n"] += 1
        if calls["n"] == 2:
            raise PermanentJobError()

    metrics = QueueMetrics(meter_provider, free_bytes=docs.storage.free_bytes)
    worker = conn_worker(db_connection, docs, migrated_database, first_ok_then_fail, metrics)
    assert await worker.run_once()
    assert await worker.run_once()

    points = metric_points(metric_reader)
    outcomes = {p.attributes["outcome"] for p in points["belegbot.jobs"]}
    assert {"succeeded", "failed"} <= outcomes
    for name in (
        "belegbot.job.duration",
        "belegbot.queue.depth",
        "belegbot.queue.oldest_age",
        "belegbot.storage.free_bytes",
    ):
        assert points.get(name), name
    uploads: dict[str, int] = {}
    for p in points["belegbot.uploads"]:
        outcome = str(p.attributes["outcome"])
        uploads[outcome] = uploads.get(outcome, 0) + p.value
    assert uploads["created"] == 2 and uploads["duplicate"] == 1

    job_spans = [s for s in spans.get_finished_spans() if s.name == "job process_document"]
    assert job_spans
    linked = {format(link.context.trace_id, "032x") for s in job_spans for link in s.links}
    assert trace_id in linked
    attrs = dict(job_spans[0].attributes or {})
    assert {"job.id", "job.kind", "job.attempt", "document.id"} <= set(attrs)
