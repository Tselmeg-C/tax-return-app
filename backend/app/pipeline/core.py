"""`run_pipeline`: preprocess → classify → extract → rules (#9 Decision 2). No DB, no Storage.

Shared by the job handler (`handler.py`, which adds load / resume / persons / dedupe /
persist) and the eval predictor (`evals/predictors/pipeline.py`), so the eval measures the
code that runs in production. Only the system prompt of the prompt set and the document
parts reach the LLM (Decision 11): no file name, no names, no ids.

Errors: `PermanentJobError`s from preprocess propagate; an LLM output error after the router
gave up ends the run with `classification_failed` / `extraction_failed`; every other
`LLMError` propagates (the handler maps it, Decision 8). `on_step` runs after every router
call, success or not, so the handler can persist the call records at once.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import structlog
from opentelemetry import metrics, trace
from pydantic import BaseModel

from app.domain.enums import AttentionReason, DocType, ExtractionStep
from app.llm import LLMCallRecord, LLMError, LLMOutputError, LLMRouter, Part
from app.pipeline.preprocess import Limits, Prepared, preprocess
from app.pipeline.prompts import Prompt, PromptSet
from app.pipeline.schemas import ClassifyOutput, GenericBillExtraction
from app.tax.bill_rules import RulesResult, evaluate, needs_extract, ordered
from app.tax.mapping import MappingTable

log = structlog.stdlib.get_logger("app.pipeline")
tracer = trace.get_tracer("app.pipeline")


class PipelineMetrics:
    """`belegbot.pipeline.*` (attributes are low-cardinality codes only)."""

    def __init__(self, provider: metrics.MeterProvider | None = None) -> None:
        meter = (provider or metrics.get_meter_provider()).get_meter("belegbot")
        self.documents = meter.create_counter("belegbot.pipeline.documents", unit="{document}")
        self.attention = meter.create_counter("belegbot.pipeline.attention", unit="{reason}")
        self.step_duration = meter.create_histogram("belegbot.pipeline.step.duration", unit="s")
        self.document_cost = meter.create_histogram("belegbot.pipeline.document.cost", unit="EUR")
        self.items = meter.create_counter("belegbot.pipeline.items", unit="{item}")
        self.steps_reused = meter.create_counter("belegbot.pipeline.steps_reused", unit="{step}")
        self.overrides_kept = meter.create_counter(
            "belegbot.pipeline.overrides_kept", unit="{document}"
        )


_metrics: PipelineMetrics | None = None


def pipeline_metrics() -> PipelineMetrics:
    global _metrics
    if _metrics is None:
        _metrics = PipelineMetrics()
    return _metrics


@dataclass(frozen=True)
class PipelineInput:
    data: bytes = field(repr=False)
    mime_type: str
    limits: Limits


@dataclass(frozen=True)
class StepOutcome:
    step: ExtractionStep
    output: BaseModel | None = field(repr=False)  # validated output (None on failure)
    calls: tuple[LLMCallRecord, ...] = ()
    error: LLMError | None = None
    reused: bool = False

    @property
    def outcome(self) -> str:
        if self.error is not None:
            return type(self.error).__name__
        return "reused" if self.reused else "ok"


@dataclass(frozen=True)
class PipelineResult:
    prepared: Prepared
    classify: StepOutcome
    extract: StepOutcome | None
    rules: RulesResult | None  # None when a step failed
    reasons: list[AttentionReason]  # every reason that applied, priority order

    @property
    def doc_type(self) -> DocType | None:
        out = self.classify.output
        return out.doc_type if isinstance(out, ClassifyOutput) else None

    @property
    def calls(self) -> tuple[LLMCallRecord, ...]:
        return self.classify.calls + (self.extract.calls if self.extract else ())


OnStep = Callable[[StepOutcome], Awaitable[None]]


async def _noop(_: StepOutcome) -> None:
    return None


def _log_step(document_id: str | None, outcome: StepOutcome, started: float) -> None:
    duration = time.monotonic() - started
    pipeline_metrics().step_duration.record(
        duration, {"step": outcome.step.value, "outcome": outcome.outcome}
    )
    if outcome.reused:
        pipeline_metrics().steps_reused.add(1, {"step": outcome.step.value})
    log.info(
        "pipeline.step",
        document_id=document_id,
        step=outcome.step.value,
        outcome=outcome.outcome,
        duration_ms=round(duration * 1000),
        error_kind=type(outcome.error).__name__ if outcome.error else None,
        reused=outcome.reused,
    )


async def _step(
    step: ExtractionStep,
    prompt: Prompt,
    parts: tuple[Part, ...],
    *,
    router: LLMRouter,
    prompt_version: str,
    model: str | None,
    reuse: BaseModel | None,
    on_step: OnStep,
    document_id: str | None,
) -> StepOutcome:
    started = time.monotonic()
    with tracer.start_as_current_span(f"pipeline {step.value}") as span:
        span.set_attribute("belegbot.pipeline.step", step.value)
        span.set_attribute("belegbot.pipeline.prompt_version", prompt_version)
        span.set_attribute("belegbot.pipeline.reused", reuse is not None)
        if document_id:
            span.set_attribute("document.id", document_id)
        if reuse is not None:
            outcome = StepOutcome(step, reuse, reused=True)
        else:
            try:
                result = await router.structured(
                    task=step.value,
                    system=prompt.system,
                    parts=list(parts),
                    schema=prompt.schema,
                    prompt_version=prompt_version,
                    model=model,
                )
            except LLMError as exc:
                outcome = StepOutcome(step, None, exc.calls, exc)
                span.set_attribute("error.type", type(exc).__name__)
                span.set_attribute("belegbot.pipeline.outcome", outcome.outcome)
                await on_step(outcome)
                _log_step(document_id, outcome, started)
                if isinstance(exc, LLMOutputError):
                    return outcome
                raise
            outcome = StepOutcome(step, result.data, result.calls)
            await on_step(outcome)
        span.set_attribute("belegbot.pipeline.outcome", outcome.outcome)
        _log_step(document_id, outcome, started)
        return outcome


async def run_pipeline(
    inp: PipelineInput,
    *,
    router: LLMRouter,
    prompt_set: PromptSet,
    mapping: MappingTable,
    today: date,
    fallback_year: int | None = None,
    reuse: Mapping[ExtractionStep, BaseModel] | None = None,
    models: Mapping[str, str] | None = None,
    on_step: OnStep = _noop,
    document_id: str | None = None,
) -> PipelineResult:
    """`reuse`: stored successful outputs for this prompt version (Decision 7: not called
    again). `models`: per-task `provider:model` overrides (evals)."""
    reuse = reuse or {}
    models = models or {}
    common: dict[str, Any] = {
        "router": router,
        "prompt_version": prompt_set.version,
        "on_step": on_step,
        "document_id": document_id,
    }
    started = time.monotonic()
    with tracer.start_as_current_span("pipeline preprocess") as span:
        span.set_attribute("belegbot.pipeline.step", "preprocess")
        try:
            prepared = await asyncio.to_thread(preprocess, inp.data, inp.mime_type, inp.limits)
        except Exception as exc:
            span.set_attribute("error.type", type(exc).__name__)
            pipeline_metrics().step_duration.record(
                time.monotonic() - started, {"step": "preprocess", "outcome": type(exc).__name__}
            )
            raise
    pipeline_metrics().step_duration.record(
        time.monotonic() - started, {"step": "preprocess", "outcome": "ok"}
    )

    classify = await _step(
        ExtractionStep.CLASSIFY,
        prompt_set.classify,
        prepared.parts_for_classify,
        model=models.get("classify"),
        reuse=reuse.get(ExtractionStep.CLASSIFY),
        **common,
    )
    if not isinstance(classify.output, ClassifyOutput):
        return PipelineResult(
            prepared, classify, None, None, [AttentionReason.CLASSIFICATION_FAILED]
        )
    extract: StepOutcome | None = None
    extraction: GenericBillExtraction | None = None
    if needs_extract(classify.output):
        extract = await _step(
            ExtractionStep.EXTRACT,
            prompt_set.extract,
            prepared.parts_for_extract,
            model=models.get("extract"),
            reuse=reuse.get(ExtractionStep.EXTRACT),
            **common,
        )
        if not isinstance(extract.output, GenericBillExtraction):
            return PipelineResult(
                prepared, classify, extract, None, [AttentionReason.EXTRACTION_FAILED]
            )
        extraction = extract.output

    with tracer.start_as_current_span("pipeline rules") as span:
        span.set_attribute("belegbot.pipeline.step", "rules")
        rules = evaluate(classify.output, extraction, mapping, today, fallback_year)
    return PipelineResult(prepared, classify, extract, rules, ordered(set(rules.reasons)))
