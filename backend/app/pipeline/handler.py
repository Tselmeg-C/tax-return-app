"""`process_document` job handler (#9): `run_pipeline` plus the DB parts.

Load (`HouseholdScope`) → read through `Storage` (checksum) → reuse stored steps of the current
prompt version (Decision 7) → `run_pipeline` (each router call's records are written at once,
in their own transaction) → person + duplicate check → one final transaction (replace the
document's tax item unless the user overrode one, Decision 6) → `DocumentProcessed`.

Errors (Decision 8): transient LLM errors re-raise (#6 retries), the other permanent LLM
errors become `PermanentJobError(error_kind=<class>)`, output errors end `needs_attention`.
Logs, spans and metrics carry ids and codes only.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from opentelemetry import trace
from pydantic import BaseModel, ValidationError
from sqlalchemy import delete, update

from app.config import LLMSettings, Settings, SettingsError, get_llm_settings, get_settings
from app.db.models import Document, Extraction, TaxItem
from app.db.scope import HouseholdScope
from app.domain.enums import (
    CATEGORY_GROUP,
    AttentionReason,
    DocType,
    DocumentStatus,
    ExtractionStep,
)
from app.llm import LLMError, LLMNotConfigured, LLMRouter, get_router, is_permanent
from app.pipeline.core import (
    PipelineInput,
    PipelineResult,
    StepOutcome,
    pipeline_metrics,
    run_pipeline,
)
from app.pipeline.dedupe import find_duplicate
from app.pipeline.dev_fake import dev_router, is_dev_fake
from app.pipeline.events import DocumentProcessed, publish
from app.pipeline.persons import match_person
from app.pipeline.preprocess import Limits
from app.pipeline.prompts import PromptError, prompt_set, resolve_version
from app.pipeline.schemas import CONFIDENCE_VALUE, ClassifyOutput, GenericBillExtraction
from app.queue.errors import DocumentMissing, PermanentJobError
from app.queue.handlers import JobContext, load_document, read_stored_file
from app.tax.bill_rules import ordered
from app.tax_params import mapping_table

log = structlog.stdlib.get_logger("app.pipeline")
tracer = trace.get_tracer("app.pipeline")
BERLIN = ZoneInfo("Europe/Berlin")
SCHEMA_OF: dict[ExtractionStep, type[BaseModel]] = {
    ExtractionStep.CLASSIFY: ClassifyOutput,
    ExtractionStep.EXTRACT: GenericBillExtraction,
}
ZERO = Decimal("0.00")


def check_pipeline_settings(settings: Settings, llm: LLMSettings) -> None:
    """Worker start: names the variables, never their values (#9 Settings, Decision 14)."""
    problems = []
    try:
        resolve_version(settings.pipeline_prompt_version)
    except PromptError:
        problems.append("PIPELINE_PROMPT_VERSION: unknown prompt version")
    if settings.pipeline_max_pages > llm.llm_max_pdf_pages:
        problems.append("PIPELINE_MAX_PAGES: must be <= LLM_MAX_PDF_PAGES")
    if 2 * llm.llm_deadline_seconds + 120 > settings.job_timeout_seconds:
        problems.append("JOB_TIMEOUT_SECONDS: must be >= 2 x LLM_DEADLINE_SECONDS + 120")
    try:
        LLMRouter(llm)  # validates routing.yaml + LLM_* overrides (fake refused in production)
    except LLMNotConfigured as exc:
        problems.append(str(exc))  # fixed template naming the setting / key
    if problems:
        raise SettingsError("invalid settings: " + "; ".join(problems))


def _berlin_today() -> date:
    return datetime.now(BERLIN).date()


@dataclass
class _Run:
    """Per-job bookkeeping: successful extraction row ids and the job's LLM cost."""

    ok_rows: dict[ExtractionStep, uuid.UUID] = field(default_factory=dict)
    cost: Decimal = Decimal("0")


@dataclass
class PipelineHandler:
    router: Callable[[], LLMRouter] | None = None  # None: get_router() (+ dev fake routing)
    today: Callable[[], date] = _berlin_today

    def _router(self) -> LLMRouter:
        if self.router is not None:
            return self.router()
        llm = get_llm_settings()
        base = get_router()
        if is_dev_fake(llm.llm_classify_model, llm.llm_extract_model):
            return dev_router(base)
        return base

    async def __call__(self, ctx: JobContext) -> None:
        with tracer.start_as_current_span("pipeline process_document") as span:
            if ctx.job.document_id is not None:
                span.set_attribute("document.id", str(ctx.job.document_id))
            try:
                await self._process(ctx)
            except Exception as exc:
                span.set_attribute("error.type", type(exc).__name__)
                outcome = "failed" if isinstance(exc, PermanentJobError) else "retried"
                pipeline_metrics().documents.add(1, {"outcome": outcome, "doc_type": "unknown"})
                raise

    async def _process(self, ctx: JobContext) -> None:
        settings = ctx.settings or get_settings()
        llm = get_llm_settings()
        doc = await load_document(ctx)
        data = await read_stored_file(ctx, doc.storage_key, doc.sha256)
        prompts = prompt_set(settings.pipeline_prompt_version)
        run = _Run()
        reuse = await _stored_outputs(ctx, doc, prompts.version, run)
        router = self._router()
        upload_year = doc.created_at.astimezone(BERLIN).year

        async def on_step(outcome: StepOutcome) -> None:
            await _write_calls(ctx, doc, outcome, prompts.version, run)

        try:
            result = await run_pipeline(
                PipelineInput(
                    data,
                    doc.mime_type,
                    Limits(
                        max_pages=settings.pipeline_max_pages,
                        classify_max_chars=settings.pipeline_classify_max_chars,
                        max_image_pixels=llm.llm_max_image_pixels,
                    ),
                ),
                router=router,
                prompt_set=prompts,
                mapping=mapping_table(),
                today=self.today(),
                fallback_year=upload_year,
                reuse=reuse,
                on_step=on_step,
                document_id=str(doc.id),
            )
        except LLMError as exc:
            if not is_permanent(exc):
                raise  # transient: #6 retries with backoff
            raise PermanentJobError(error_kind=type(exc).__name__) from None
        with tracer.start_as_current_span("pipeline persist") as span:
            span.set_attribute("belegbot.pipeline.step", "persist")
            event = await _persist(ctx, doc, result, run)
        n_items = 0 if result.rules is None or result.rules.draft is None else 1
        status = event.status.value
        metrics = pipeline_metrics()
        doc_type = event.doc_type.value if event.doc_type else "unknown"
        metrics.documents.add(1, {"outcome": status, "doc_type": doc_type})
        for reason in event_reasons(event, result):
            metrics.attention.add(1, {"reason": reason.value})
        metrics.document_cost.record(float(run.cost), {"outcome": status})
        log.info(
            "pipeline.document",
            document_id=str(doc.id),
            status=status,
            attention_reasons=[r.value for r in event_reasons(event, result)],
            doc_type=event.doc_type.value if event.doc_type else None,
            n_items=n_items,
            cost_eur=str(run.cost),
            prompt_version=prompts.version,
        )
        await publish(event)


def event_reasons(event: DocumentProcessed, result: PipelineResult) -> list[AttentionReason]:
    found = set(result.reasons)
    if event.attention_reason is not None:
        found.add(event.attention_reason)
    return ordered(found) if event.status is DocumentStatus.NEEDS_ATTENTION else []


async def _stored_outputs(
    ctx: JobContext, doc: Document, version: str, run: _Run
) -> dict[ExtractionStep, BaseModel]:
    """Successful outputs of earlier attempts for this prompt version (newest first)."""
    async with ctx.sessionmaker() as session:
        rows = (
            (
                await session.execute(
                    HouseholdScope(session, doc.household_id)
                    .select(Extraction)
                    .where(
                        Extraction.document_id == doc.id,
                        Extraction.prompt_version == version,
                        Extraction.error_kind.is_(None),
                        Extraction.raw_json.is_not(None),
                    )
                    .order_by(Extraction.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
    found: dict[ExtractionStep, BaseModel] = {}
    for row in rows:
        step = ExtractionStep(row.step)
        if step in found:
            continue
        try:
            found[step] = SCHEMA_OF[step].model_validate(row.raw_json)
        except ValidationError:
            continue  # stale shape: call again
        run.ok_rows[step] = row.id
    return found


async def _write_calls(
    ctx: JobContext, doc: Document, outcome: StepOutcome, version: str, run: _Run
) -> None:
    """One `extraction` row per call record, failed attempts included (Decision 7)."""
    output = outcome.output
    doc_type: DocType | None = None
    if isinstance(output, ClassifyOutput):
        doc_type = output.doc_type
    async with ctx.sessionmaker() as session, session.begin():
        scope = HouseholdScope(session, doc.household_id)
        for record in outcome.calls:
            ok = record.outcome == "ok" and output is not None
            row = Extraction(
                document_id=doc.id,
                step=outcome.step,
                doc_type=doc_type,
                provider=record.provider,
                model=record.model,
                prompt_version=version,
                raw_json=output.model_dump(mode="json") if ok and output is not None else None,
                confidence=CONFIDENCE_VALUE[output.confidence]  # type: ignore[attr-defined]
                if ok and output is not None
                else None,
                input_tokens=record.input_tokens,
                output_tokens=record.output_tokens,
                cost_eur=record.cost_eur,
                latency_ms=record.latency_ms,
                error_kind=None if ok else record.outcome,
            )
            scope.add(row)
            run.cost += record.cost_eur
            if ok:
                run.ok_rows[outcome.step] = row.id


def _item_values(result: PipelineResult) -> dict[str, Any] | None:
    if result.rules is None or result.rules.draft is None:
        return None
    d = result.rules.draft
    return {
        "category": d.category,
        "anlage": d.anlage,
        "zeile": d.zeile,
        "gross_amount": d.gross_amount,
        "deductible_amount": d.deductible_amount,
        "labour_share_35a": d.labour_share_35a,
        "vendor": d.vendor,
        "invoice_date": d.invoice_date,
        "payment_date": d.payment_date,
        "payment_method": d.payment_method,
        "is_relevant": d.is_relevant,
        "reason": d.reason,
        "confidence": d.confidence,
        "year": d.tax_year,
    }


async def _persist(
    ctx: JobContext, doc: Document, result: PipelineResult, run: _Run
) -> DocumentProcessed:
    reasons = set(result.reasons)
    values = _item_values(result)
    started = time.monotonic()
    async with ctx.sessionmaker() as session, session.begin():
        scope = HouseholdScope(session, doc.household_id)
        locked = (
            await session.execute(
                scope.select(Document).where(Document.id == doc.id).with_for_update()
            )
        ).scalar_one_or_none()
        if locked is None:
            raise DocumentMissing()
        overridden: Any = (
            await session.execute(
                scope.select(TaxItem)
                .with_only_columns(TaxItem.id)
                .where(TaxItem.document_id == doc.id, TaxItem.overridden_by_user.is_(True))
                .limit(1)
            )
        ).first()
        kept = overridden is not None
        if kept:
            pipeline_metrics().overrides_kept.add(1)
            log.info("pipeline.overrides_kept", document_id=str(doc.id))
            reasons = set()
        elif values is not None:
            draft = result.rules.draft if result.rules else None
            assert draft is not None
            if values["is_relevant"]:
                other = await find_duplicate(
                    session,
                    doc.household_id,
                    doc.id,
                    vendor=values["vendor"],
                    gross_amount=values["gross_amount"],
                    invoice_date=values["invoice_date"],
                    payment_date=values["payment_date"],
                )
                if other is not None:
                    reasons.add(AttentionReason.POSSIBLE_DUPLICATE)
                    values.update(
                        is_relevant=False,
                        deductible_amount=ZERO,
                        reason=f"Möglicherweise doppelt: gleicher Beleg wie das am "
                        f"{other:%d.%m.%Y} hochgeladene Dokument.",
                    )
            if values["year"] is None:  # no fallback: never happens in the handler
                values["year"] = doc.created_at.astimezone(BERLIN).year
            values["person_id"] = await match_person(
                session,
                doc.household_id,
                doc.uploaded_by_user_id,
                draft.recipient_name,
                values["category"],
            )
            await session.execute(
                delete(TaxItem)
                .where(TaxItem.document_id == doc.id, TaxItem.household_id == doc.household_id)
                .execution_options(synchronize_session=False)
            )
            extraction_id = run.ok_rows.get(ExtractionStep.EXTRACT) or run.ok_rows.get(
                ExtractionStep.CLASSIFY
            )
            scope.add(TaxItem(document_id=doc.id, extraction_id=extraction_id, **values))
            pipeline_metrics().items.add(
                1,
                {
                    "category_group": CATEGORY_GROUP[values["category"]].value,
                    "is_relevant": bool(values["is_relevant"]),
                },
            )
        else:
            await session.execute(
                delete(TaxItem)
                .where(TaxItem.document_id == doc.id, TaxItem.household_id == doc.household_id)
                .execution_options(synchronize_session=False)
            )
        stored = ordered(reasons)
        status = DocumentStatus.NEEDS_ATTENTION if stored else DocumentStatus.DONE
        attention = stored[0] if stored else None
        await session.execute(
            update(Document)
            .where(Document.id == doc.id, Document.household_id == doc.household_id)
            .values(
                doc_type=result.doc_type,
                page_count=result.prepared.page_count,
                status=status,
                attention_reason=attention,
            )
            .execution_options(synchronize_session=False)
        )
    pipeline_metrics().step_duration.record(
        time.monotonic() - started, {"step": "persist", "outcome": "ok"}
    )
    return DocumentProcessed(
        document_id=doc.id,
        household_id=doc.household_id,
        channel=doc.channel,
        status=status,
        attention_reason=attention,
        doc_type=result.doc_type,
    )


process_document = PipelineHandler()

__all__ = ["PipelineHandler", "check_pipeline_settings", "process_document"]
