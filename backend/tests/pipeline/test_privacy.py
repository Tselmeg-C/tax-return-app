"""Sentinels in the document text, the LLM replies and the file name never reach logs,
spans, metric attributes, job rows, `document.error_kind` or exception messages (#9)."""

from __future__ import annotations

import io
import json
import secrets
from pathlib import Path

import pytest
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Document, Job
from app.llm import LLMAuthError
from app.llm.fake import FakeReply
from app.pipeline import core
from app.pipeline.core import PipelineMetrics
from tests.pipeline import files
from tests.pipeline.helpers import classify_out, extract_out, make_world, scripted


async def test_no_sentinel_anywhere(
    committed: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    migrated_database: str,
    spans: InMemorySpanExporter,
    json_log: io.StringIO,
    metric_reader: InMemoryMetricReader,
    meter_provider: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(core, "_metrics", PipelineMetrics(meter_provider))  # type: ignore[arg-type]
    tag = secrets.token_hex(6)
    sentinels = {
        "text": f"Textlayer{tag}",
        "vendor": f"Vendor{tag}",
        "recipient": f"Recipient{tag}",
        "description": f"Description{tag}",
        "reason": f"Reason{tag}",
        "filename": f"file{tag}.pdf",
    }
    world = await make_world(committed, tmp_path, migrated_database)
    extract = extract_out(vendor=sentinels["vendor"], recipient_name=sentinels["recipient"])
    extract = extract.model_copy(
        update={
            "reason_de": sentinels["reason"],
            "line_items": [
                extract.line_items[0].model_copy(update={"description": sentinels["description"]})
            ],
        }
    )
    classify = classify_out(vendor=sentinels["vendor"], reason_de=sentinels["reason"])
    pdf = files.pdf_text(1, text=f"Rechnung {sentinels['text']} Gesamtbetrag 180,00 EUR ok")

    ok = scripted(classify, extract)
    a = await world.upload(pdf, original_filename=sentinels["filename"])
    assert await world.worker(ok.router).run_once()

    bad = scripted(classify)
    bad.provider._script.extend([FakeReply(raw_text=f'{{"x": "{tag}"}}')] * 2)
    b = await world.upload(files.pdf_text(1, text=f"Zweite {sentinels['text']} Rechnung ok"))
    assert await world.worker(bad.router).run_once()

    perm = scripted(LLMAuthError(provider="fake"))
    c = await world.upload(files.pdf_text(1, text=f"Dritte {sentinels['text']} Rechnung ok"))
    assert await world.worker(perm.router).run_once()

    texts = [json_log.getvalue()]
    for span in spans.get_finished_spans():
        texts.append(span.name)
        texts.append(json.dumps(dict(span.attributes or {}), default=str))
        texts.extend(
            e.name + json.dumps(dict(e.attributes or {}), default=str) for e in span.events
        )
    data = metric_reader.get_metrics_data()
    if data is not None:
        for rm in data.resource_metrics:
            for sm in rm.scope_metrics:
                for metric in sm.metrics:
                    for point in metric.data.data_points:
                        texts.append(json.dumps(dict(point.attributes or {}), default=str))
    async with committed() as session:
        for job in (await session.execute(select(Job))).scalars():
            texts.append(f"{job.last_error_kind} {job.locked_by} {job.trace_context}")
        for doc in (await session.execute(select(Document))).scalars():
            texts.append(str(doc.error_kind))
    blob = "\n".join(texts)
    assert "pipeline.document" in blob and "pipeline extract" in blob  # captured something
    for name, value in sentinels.items():
        assert value not in blob, name
    assert tag not in blob
    assert (await world.doc(c.id)).error_kind == "LLMAuthError"
    assert (await world.doc(b.id)).attention_reason is not None
    assert (await world.doc(a.id)).status.value in ("done", "needs_attention")
