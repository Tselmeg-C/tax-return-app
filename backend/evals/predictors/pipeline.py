"""`pipeline` predictor (#9): runs the production `run_pipeline` on an `EvalCase`.

`--provider openai` (default) uses the router of `routing.yaml` (needs `OPENAI_API_KEY`);
`--model` is one `provider:model` for both steps or `classify=…,extract=…`.
`--provider fake` answers with the perfect reader (`evals/fakes/perfect_reader.py`) and runs
offline. `--prompt-version` selects `app/pipeline/prompts/<version>/` (default: highest).
The `Prediction` holds what the handler would persist before the DB-only steps (person,
dedupe) and one `CallUsage` per LLM call; never raw LLM output or document text.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import structlog

from app.domain.enums import PaymentMethod
from app.pipeline.core import (
    PipelineInput,
    PipelineResult,
    default_router,
    effective_models,
    run_pipeline,
)
from app.pipeline.dev_fake import FAKE_MODELS, fake_base_router, scripted_router
from app.pipeline.preprocess import DEFAULT_LIMITS
from app.pipeline.prompts import prompt_set, read_lock
from app.pipeline.schemas import ClassifyOutput
from app.tax_params import mapping_table
from evals.dataset import EvalCase
from evals.fakes.perfect_reader import load_replies
from evals.paths import EvalPaths
from evals.predictors.base import PredictorOptions
from evals.schema import CallUsage, Prediction

BERLIN = ZoneInfo("Europe/Berlin")
PROVIDERS = ("openai", "fake")


def parse_models(value: str | None) -> dict[str, str]:
    """`provider:model` (both steps) or `classify=provider:model,extract=provider:model`."""
    if not value:
        return {}
    if "=" not in value:
        return {"classify": value, "extract": value}
    out: dict[str, str] = {}
    for part in value.split(","):
        task, _, model = part.partition("=")
        if task.strip() not in ("classify", "extract") or not model.strip():
            raise ValueError("--model: use provider:model or classify=…,extract=…")
        out[task.strip()] = model.strip()
    return out


def to_prediction(result: PipelineResult) -> Prediction:
    calls = [
        CallUsage(
            provider=c.provider,
            model=c.model,
            step=c.task,
            input_tokens=c.input_tokens,
            output_tokens=c.output_tokens,
            cost_eur=c.cost_eur,
            latency_ms=c.latency_ms,
        )
        for c in result.calls
    ]
    if result.rules is None:  # classify / extract output error after the router gave up
        failed = result.extract if result.extract is not None else result.classify
        kind = type(failed.error).__name__ if failed.error else "StepFailed"
        return Prediction(doc_type=result.doc_type, calls=calls, error_kind=kind)
    draft = result.rules.draft
    classify = result.classify.output
    assert isinstance(classify, ClassifyOutput)
    if draft is None:  # official document, unreadable, several documents: no tax item
        return Prediction(
            doc_type=classify.doc_type,
            tax_relevant=classify.tax_relevant,
            deductible_amount=Decimal("0.00"),
            tax_year=classify.certificate_year,
            invoice_date=classify.document_date,
            payment_method=PaymentMethod.UNKNOWN,
            calls=calls,
        )
    return Prediction(
        doc_type=classify.doc_type,
        tax_relevant=draft.is_relevant,
        category=draft.category,
        gross_amount=draft.gross_amount,
        deductible_amount=draft.deductible_amount,
        labour_share_35a=draft.labour_share_35a,
        invoice_date=draft.invoice_date,
        payment_date=draft.payment_date,
        tax_year=draft.tax_year,
        payment_method=draft.payment_method,
        vendor=draft.vendor,
        person_hint=draft.recipient_name,
        calls=calls,
    )


class PipelinePredictor:
    name = "pipeline"

    def __init__(self, provider: str, models: Mapping[str, str], prompt_version: str,
                 paths: EvalPaths | None, dataset: str | None) -> None:  # fmt: skip
        if provider not in PROVIDERS:
            raise ValueError("--provider must be openai or fake")
        self.provider = provider
        self.prompts = prompt_set(prompt_version)
        self.mapping = mapping_table()
        if provider == "fake":
            paths = paths or EvalPaths()
            self.replies = load_replies(paths.specs / f"{dataset or 'bills_v0'}.yaml")
            self.models: dict[str, str] = dict(FAKE_MODELS)
            self._base = fake_base_router()
        else:
            self.replies = {}
            self.models = dict(models)
            self._base = default_router()

    def describe(self) -> dict[str, str]:
        models = effective_models(self._base, self.models)
        return {
            "name": self.name,
            "provider": self.provider,
            "model": f"classify={models['classify']},extract={models['extract']}",
            "classify_model": models["classify"],
            "extract_model": models["extract"],
            "prompt_version": self.prompts.version,
            "prompt_sha256": read_lock().get(self.prompts.version, self.prompts.sha256),
            "routing_version": str(self._base.routing.version),
            "pricing_version": self._base.pricing.version,
        }

    async def predict(self, case: EvalCase) -> Prediction:
        router = self._base
        if self.provider == "fake":
            classify, extract = self.replies[case.id]
            router = scripted_router(self._base, [classify] + ([extract] if extract else []))
        result = await run_pipeline(
            PipelineInput(case.read_bytes(), case.mime_type, DEFAULT_LIMITS),
            router=router,
            prompt_set=self.prompts,
            mapping=self.mapping,
            today=datetime.now(BERLIN).date(),
            models=self.models,
        )
        return to_prediction(result)


def make_pipeline(options: PredictorOptions) -> PipelinePredictor:
    if not structlog.is_configured():  # CLI run: keep stdout to the runner's report lines
        structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING))
    return PipelinePredictor(
        options.provider or "openai",
        parse_models(options.model),
        options.prompt_version or "",
        options.paths,
        options.dataset,
    )


def requires_env(options: PredictorOptions) -> tuple[str, ...]:
    return ("OPENAI_API_KEY",) if (options.provider or "openai") == "openai" else ()
