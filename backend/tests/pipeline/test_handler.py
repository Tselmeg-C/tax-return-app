"""`process_document` with the DB (#9 "Handler with the DB"): FakeProvider scripts, #6's
worker loop run once per job attempt."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import SettingsError
from app.db.models import Extraction, Job, TaxItem
from app.domain.enums import (
    AttentionReason,
    Category,
    DocType,
    DocumentStatus,
    ExtractionStep,
    JobStatus,
)
from app.llm import LLMAuthError, LLMRouter, LLMUnavailable
from app.llm.fake import FakeReply
from app.pipeline import core, events
from app.pipeline.core import PipelineMetrics
from app.pipeline.handler import check_pipeline_settings
from app.pipeline.schemas import GenericBillExtraction
from app.queue.handlers import JobContext
from app.queue.runner import claim
from tests.documents.conftest import metric_points
from tests.pipeline import files
from tests.pipeline.helpers import (
    CapturingProvider,
    World,
    classify_out,
    extract_out,
    llm_settings,
    make_world,
    scripted,
)

Sessions = async_sessionmaker[AsyncSession]
HANDWERK = [
    ("180.00", "handwerkerleistung", "labour"),
    ("132.40", "handwerkerleistung", "material"),
]


@pytest.fixture
async def world(committed: Sessions, tmp_path: Path, migrated_database: str) -> World:
    return await make_world(committed, tmp_path, migrated_database)


@pytest.fixture
def pmetrics(monkeypatch: pytest.MonkeyPatch, meter_provider: object) -> PipelineMetrics:
    m = PipelineMetrics(meter_provider)  # type: ignore[arg-type]
    monkeypatch.setattr(core, "_metrics", m)
    return m


async def _run(world: World, router: LLMRouter, **overrides: object) -> None:
    assert await world.worker(router, **overrides).run_once()


async def test_relevant_bill(world: World) -> None:
    extract = extract_out()
    s = scripted(classify_out(), extract)
    doc = await world.upload(files.jpeg())
    await _run(world, s.router)
    d = await world.doc(doc.id)
    assert (d.status, d.doc_type, d.attention_reason) == (
        DocumentStatus.DONE,
        DocType.GENERIC_BILL,
        None,
    )
    assert d.page_count == 1
    rows = await world.rows(Extraction, doc.id)
    assert [r.step for r in rows] == [ExtractionStep.CLASSIFY, ExtractionStep.EXTRACT]
    for r in rows:
        assert r.prompt_version == "v1" and r.error_kind is None
        assert r.input_tokens == 100 and r.output_tokens == 20 and r.latency_ms == 5
        assert r.cost_eur > 0 and r.confidence == Decimal("0.900")
    assert rows[1].raw_json == extract.model_dump(mode="json")
    async with world.sessions() as session:
        raw = (
            await session.execute(
                text("SELECT raw_json FROM extraction WHERE id = :id"), {"id": rows[1].id}
            )
        ).scalar_one()
    assert bytes(raw).startswith(b"gAAAAA")
    (item,) = await world.items(doc.id)
    assert item.extraction_id == rows[1].id
    assert (item.category, item.deductible_amount, item.year) == (
        Category.WK_ARBEITSMITTEL,
        Decimal("180.00"),
        2025,
    )
    assert (item.anlage, item.zeile, item.is_relevant) == ("n", "54", True)
    assert s.calls == 2


async def test_irrelevant_bill(world: World) -> None:
    s = scripted(classify_out(tax_relevant=False, total_gross="11.45"))
    doc = await world.upload(files.jpeg())
    await _run(world, s.router)
    assert (await world.doc(doc.id)).status is DocumentStatus.DONE
    assert len(await world.rows(Extraction, doc.id)) == 1
    (item,) = await world.items(doc.id)
    assert (item.category, item.is_relevant, item.deductible_amount, item.gross_amount) == (
        Category.IRRELEVANT,
        False,
        Decimal("0.00"),
        Decimal("11.45"),
    )


async def test_lohnsteuerbescheinigung_is_parked(world: World) -> None:
    s = scripted(classify_out(doc_type="lohnsteuerbescheinigung", total_gross=None))
    doc = await world.upload(files.pdf_text(1))
    await _run(world, s.router)
    d = await world.doc(doc.id)
    assert (d.status, d.attention_reason, d.doc_type) == (
        DocumentStatus.NEEDS_ATTENTION,
        AttentionReason.DOC_TYPE_NOT_SUPPORTED,
        DocType.LOHNSTEUERBESCHEINIGUNG,
    )
    assert len(await world.rows(Extraction, doc.id)) == 1
    assert await world.items(doc.id) == []


async def test_schema_invalid_extract_needs_attention(world: World) -> None:
    s = scripted(classify_out())
    s.provider._script.extend([FakeReply(raw_text="{}"), FakeReply(raw_text="{}")])
    doc = await world.upload(files.jpeg())
    await _run(world, s.router)
    d = await world.doc(doc.id)
    assert (d.status, d.attention_reason, d.doc_type) == (
        DocumentStatus.NEEDS_ATTENTION,
        AttentionReason.EXTRACTION_FAILED,
        DocType.GENERIC_BILL,
    )
    rows = await world.rows(Extraction, doc.id)
    assert len(rows) == 3
    failed = [r for r in rows if r.step is ExtractionStep.EXTRACT]
    assert [(r.error_kind, r.raw_json) for r in failed] == [
        ("LLMSchemaValidationError", None),
        ("LLMSchemaValidationError", None),
    ]
    assert await world.items(doc.id) == []
    assert (await world.job(doc.id)).status is JobStatus.SUCCEEDED


async def test_transient_error_retries_and_reuses_classify(
    world: World, pmetrics: PipelineMetrics, metric_reader: object
) -> None:
    first = scripted(classify_out(), LLMUnavailable(provider="fake"))
    doc = await world.upload(files.jpeg())
    await _run(world, first.router)
    job = await world.job(doc.id)
    assert (job.status, job.last_error_kind) == (JobStatus.QUEUED, "LLMUnavailable")
    assert (await world.doc(doc.id)).status is DocumentStatus.QUEUED
    rows = await world.rows(Extraction, doc.id)
    assert [(r.step, r.error_kind) for r in rows] == [
        (ExtractionStep.CLASSIFY, None),
        (ExtractionStep.EXTRACT, "LLMUnavailable"),
    ]
    async with world.sessions() as session:
        await session.execute(
            update(Job).where(Job.id == job.id).values(run_after=datetime.now(UTC))
        )
        await session.commit()
    second = scripted(extract_out())  # no classify reply: it must be reused
    await _run(world, second.router)
    assert second.calls == 1
    assert (await world.doc(doc.id)).status is DocumentStatus.DONE
    assert await world.extraction_count(doc.id, ExtractionStep.CLASSIFY) == 1
    points = metric_points(metric_reader)  # type: ignore[arg-type]
    reused = [
        p for p in points["belegbot.pipeline.steps_reused"] if p.attributes["step"] == "classify"
    ]
    assert sum(p.value for p in reused) == 1


async def test_auth_error_fails_once(world: World) -> None:
    s = scripted(LLMAuthError(provider="fake"))
    doc = await world.upload(files.jpeg())
    await _run(world, s.router)
    job = await world.job(doc.id)
    assert (job.status, job.last_error_kind, job.attempts) == (JobStatus.FAILED, "LLMAuthError", 1)
    d = await world.doc(doc.id)
    assert (d.status, d.error_kind) == (DocumentStatus.FAILED, "LLMAuthError")


async def test_not_configured_without_key(world: World) -> None:
    router = LLMRouter(llm_settings(llm_classify_model="", llm_extract_model=""))  # openai:…
    doc = await world.upload(files.jpeg())
    await _run(world, router)
    job = await world.job(doc.id)
    assert (job.status, job.last_error_kind, job.attempts) == (
        JobStatus.FAILED,
        "LLMNotConfigured",
        1,
    )


@pytest.mark.parametrize(
    ("data", "mime_kind"),
    [
        (files.truncated(files.jpeg((400, 300))), "CorruptDocument"),
        (files.truncated(files.pdf_text(2)), "CorruptDocument"),
        (files.pdf_encrypted(), "EncryptedPdf"),
        (files.pdf_text(21), "TooManyPages"),
        (files.png_bomb(), "ImageTooLarge"),
        (files.jxl(), "UnsupportedImageFormat"),
    ],
    ids=["jpeg", "pdf", "encrypted", "pages", "bomb", "jxl"],
)
async def test_undecodable_file_fails_without_llm(
    world: World, data: bytes, mime_kind: str
) -> None:
    s = scripted()
    doc = await world.upload(data)
    await _run(world, s.router)
    job = await world.job(doc.id)
    assert (job.status, job.last_error_kind, job.attempts) == (JobStatus.FAILED, mime_kind, 1)
    assert s.calls == 0 and await world.items(doc.id) == []
    assert await world.rows(Extraction, doc.id) == []


async def _ctx(world: World, document_id: uuid.UUID) -> JobContext:
    job = await claim(world.sessions, worker_id="test", lease_seconds=60)
    assert job is not None and job.document_id == document_id
    return JobContext(job, world.sessions, world.volume, world.settings())


async def test_idempotent_rerun(world: World) -> None:
    s = scripted(classify_out(), extract_out())
    doc = await world.upload(files.jpeg())
    ctx = await _ctx(world, doc.id)
    handler = world.handler(s.router)
    await handler(ctx)
    before = [
        (i.category, i.deductible_amount, i.year, i.extraction_id)
        for i in await world.items(doc.id)
    ]
    n_rows = len(await world.rows(Extraction, doc.id))
    calls = s.calls
    await handler(ctx)  # lease expiry after commit: same job runs again
    after = [
        (i.category, i.deductible_amount, i.year, i.extraction_id)
        for i in await world.items(doc.id)
    ]
    assert before == after and len(after) == 1
    assert s.calls == calls == 2
    assert len(await world.rows(Extraction, doc.id)) == n_rows


async def test_override_is_kept(world: World, json_log: object) -> None:
    s = scripted(classify_out(), extract_out())
    doc = await world.upload(files.jpeg())
    ctx = await _ctx(world, doc.id)
    await world.handler(s.router)(ctx)
    (item,) = await world.items(doc.id)
    async with world.sessions() as session:
        await session.execute(
            update(TaxItem)
            .where(TaxItem.id == item.id)
            .values(overridden_by_user=True, category=Category.WK_FORTBILDUNG)
        )
        await session.commit()
    async with world.sessions() as session:
        before = (
            await session.execute(text("SELECT * FROM tax_item WHERE id = :id"), {"id": item.id})
        ).one()
    again = scripted(classify_out(), extract_out(lines=[("99.00", "spenden", "not_applicable")]))
    ctx2 = JobContext(
        ctx.job, world.sessions, world.volume, world.settings(pipeline_prompt_version="v1")
    )
    # a new prompt version in the test: nothing is reused, the LLM is called again
    async with world.sessions() as session:
        await session.execute(
            update(Extraction).where(Extraction.document_id == doc.id).values(prompt_version="v0")
        )
        await session.commit()
    rows_before = len(await world.rows(Extraction, doc.id))
    await world.handler(again.router)(ctx2)
    assert again.calls == 2
    assert len(await world.rows(Extraction, doc.id)) == rows_before + 2
    async with world.sessions() as session:
        after = (
            await session.execute(text("SELECT * FROM tax_item WHERE id = :id"), {"id": item.id})
        ).one()
    assert after == before
    assert len(await world.items(doc.id)) == 1
    assert '"pipeline.overrides_kept"' in json_log.getvalue()  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("recipient", "category", "expect"),
    [
        ("Herr Alex Muster", "wk_arbeitsmittel", "Alex"),
        ("A. Muster", "wk_arbeitsmittel", "Alex"),
        ("Muster", "wk_arbeitsmittel", "uploader"),
        ("Unbekannte Person", "wk_arbeitsmittel", "uploader"),
        ("Herr Alex Muster", "handwerkerleistung", None),
    ],
)
async def test_person_matching(
    world: World, recipient: str, category: str, expect: str | None
) -> None:
    alex = await world.add_person("Alex")
    await world.add_person("Sam")
    uploader = await world.add_person("Kim")
    await world.set_uploader_person(uploader)
    kind = "labour" if category == "handwerkerleistung" else "not_applicable"
    s = scripted(
        classify_out(), extract_out(lines=[("180.00", category, kind)], recipient_name=recipient)
    )
    doc = await world.upload(files.jpeg())
    await _run(world, s.router)
    (item,) = await world.items(doc.id)
    wanted = {"Alex": alex, "uploader": uploader, None: None}[expect]
    assert item.person_id == wanted


async def test_unknown_person_and_no_uploader_person(world: World) -> None:
    await world.add_person("Alex")
    s = scripted(classify_out(), extract_out(recipient_name="Unbekannte Person"))
    doc = await world.upload(files.jpeg())
    await _run(world, s.router)
    (item,) = await world.items(doc.id)
    assert item.person_id is None


async def test_dedupe(
    world: World, committed: Sessions, tmp_path: Path, migrated_database: str
) -> None:
    first = scripted(classify_out(), extract_out(lines=HANDWERK))
    a = await world.upload(files.jpeg())
    await _run(world, first.router)
    dup = scripted(classify_out(), extract_out(lines=HANDWERK, vendor="malerbetrieb beispiel"))
    b = await world.upload(files.jpeg())
    await _run(world, dup.router)
    d = await world.doc(b.id)
    assert (d.status, d.attention_reason) == (
        DocumentStatus.NEEDS_ATTENTION,
        AttentionReason.POSSIBLE_DUPLICATE,
    )
    (item,) = await world.items(b.id)
    assert (item.is_relevant, item.deductible_amount) == (False, Decimal("0.00"))
    assert item.reason is not None and "hochgeladene Dokument" in item.reason
    other = scripted(
        classify_out(),
        extract_out(
            lines=[
                ("180.00", "handwerkerleistung", "labour"),
                ("132.41", "handwerkerleistung", "material"),
            ]
        ),
    )
    c = await world.upload(files.jpeg())
    await _run(world, other.router)
    assert (await world.doc(c.id)).status is DocumentStatus.DONE
    # household B never matches A's items
    world_b = await make_world(committed, tmp_path / "b", migrated_database)
    again = scripted(classify_out(), extract_out(lines=HANDWERK))
    e = await world_b.upload(files.jpeg())
    await _run(world_b, again.router)
    assert (await world_b.doc(e.id)).status is DocumentStatus.DONE
    assert (await world.doc(a.id)).status is DocumentStatus.DONE


async def test_tax_year_falls_back_to_upload_date(world: World) -> None:
    s = scripted(
        classify_out(document_date=None),
        extract_out(invoice_date=None, payment_date=None),
    )
    doc = await world.upload(files.jpeg(), created_at=datetime(2025, 12, 31, 23, 30, tzinfo=UTC))
    await _run(world, s.router)
    (item,) = await world.items(doc.id)
    assert item.year == 2026
    d = await world.doc(doc.id)
    assert d.attention_reason is AttentionReason.DATE_MISSING


async def test_event_published_and_subscriber_errors_ignored(
    world: World, json_log: object
) -> None:
    seen: list[events.DocumentProcessed] = []

    async def good(event: events.DocumentProcessed) -> None:
        seen.append(event)

    async def bad(event: events.DocumentProcessed) -> None:
        raise RuntimeError("subscriber boom")

    events.subscribe(bad)
    events.subscribe(good)
    try:
        s = scripted(classify_out(), extract_out())
        doc = await world.upload(files.jpeg())
        await _run(world, s.router)
    finally:
        events.unsubscribe(bad)
        events.unsubscribe(good)
    assert len(seen) == 1
    event = seen[0]
    assert (event.document_id, event.household_id, event.status, event.doc_type) == (
        doc.id,
        world.household_id,
        DocumentStatus.DONE,
        DocType.GENERIC_BILL,
    )
    assert (await world.job(doc.id)).status is JobStatus.SUCCEEDED
    log = json_log.getvalue()  # type: ignore[attr-defined]
    assert '"pipeline.subscriber_failed"' in log and "RuntimeError" in log
    assert "subscriber boom" not in log


async def test_dev_fake_routing(
    world: World, monkeypatch: pytest.MonkeyPatch, migrated_database: str
) -> None:
    from app.config import get_llm_settings
    from app.llm import get_router
    from app.pipeline.handler import PipelineHandler
    from tests.documents.test_worker import make_worker

    monkeypatch.setenv("LLM_CLASSIFY_MODEL", "fake:test")
    monkeypatch.setenv("LLM_EXTRACT_MODEL", "fake:test")
    get_llm_settings.cache_clear()
    get_router.cache_clear()
    doc = await world.upload(files.pdf_text(1))
    worker = make_worker(world.sessions, world.volume, migrated_database, PipelineHandler())
    assert await worker.run_once()
    assert (await world.doc(doc.id)).status is DocumentStatus.DONE
    (item,) = await world.items(doc.id)
    assert item.vendor == "Testmodus Beispiel GmbH" and item.gross_amount == Decimal("12.34")
    assert item.reason is not None and item.reason.startswith("Testmodus")
    # production refuses the fake routing at worker start
    monkeypatch.setenv("APP_ENV", "production")
    get_llm_settings.cache_clear()
    with pytest.raises(SettingsError, match="LLM_CLASSIFY_MODEL"):
        check_pipeline_settings(world.settings(), get_llm_settings())


@pytest.mark.parametrize(
    ("settings_kw", "llm_kw", "names"),
    [
        ({"pipeline_prompt_version": "v9"}, {}, ["PIPELINE_PROMPT_VERSION"]),
        (
            {"pipeline_max_pages": 25},
            {"llm_max_pdf_pages": 20},
            ["PIPELINE_MAX_PAGES", "LLM_MAX_PDF_PAGES"],
        ),
        (
            {"job_timeout_seconds": 600},
            {"llm_deadline_seconds": 300},
            ["JOB_TIMEOUT_SECONDS", "LLM_DEADLINE_SECONDS"],
        ),
    ],
)
def test_worker_start_checks(
    world: World, settings_kw: dict[str, object], llm_kw: dict[str, object], names: list[str]
) -> None:
    with pytest.raises(SettingsError) as info:
        check_pipeline_settings(world.settings(**settings_kw), llm_settings(**llm_kw))
    assert all(name in str(info.value) for name in names)


def test_default_settings_pass(world: World) -> None:
    check_pipeline_settings(
        world.settings(), llm_settings(llm_classify_model="", llm_extract_model="")
    )


async def test_never_send_identifying_data(world: World) -> None:
    marker = uuid.uuid4().hex[:10]
    alex = await world.add_person(f"Alex{marker}", f"Muster{marker}")
    await world.set_uploader_person(alex)
    async with world.sessions() as session:
        from app.db.models import AppUser

        await session.execute(
            update(AppUser)
            .where(AppUser.id == world.user_id)
            .values(email=f"mail{marker}@example.com")
        )
        await session.commit()
    s = scripted(classify_out(), extract_out())
    for data in (files.pdf_text(2), files.jpeg()):
        doc = await world.upload(data, original_filename=f"datei-{marker}.pdf")
        await _run(world, s.router)
        sentinels = [
            marker,
            f"datei-{marker}.pdf",
            f"mail{marker}@example.com",
            doc.storage_key,
            str(doc.id),
            str(world.household_id),
        ]
        provider: CapturingProvider = s.provider
        blobs = [sys.encode() for sys in provider.systems]
        for part in provider.parts:
            blobs.append(getattr(part, "data", b"") or getattr(part, "text", "").encode())
        for sentinel in sentinels:
            assert not any(sentinel.encode() in blob for blob in blobs), sentinel
        s = scripted(classify_out(), extract_out())


def test_extract_schema_is_the_spec() -> None:
    assert set(GenericBillExtraction.model_fields) >= {"line_items", "stated_labour_amount_35a"}
