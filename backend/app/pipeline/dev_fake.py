"""Canned `FakeProvider` replies (local dev without a key) and scripted routers.

With `LLM_CLASSIFY_MODEL=fake:test` and `LLM_EXTRACT_MODEL=fake:test` the handler installs a
per-job `FakeProvider` with `DEV_REPLIES`. The fake provider is refused with
`APP_ENV=production` (#8). The eval's perfect reader (`evals/fakes/perfect_reader.py`) uses
`scripted_router` too, so evals reach `app.llm` only through `app.pipeline`.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.config import LLMSettings
from app.llm import LLMError, LLMRouter
from app.llm.fake import FakeProvider, FakeReply
from app.pipeline.schemas import ClassifyOutput, GenericBillExtraction

FAKE_MODEL = "fake:test"
FAKE_MODELS = {"classify": FAKE_MODEL, "extract": FAKE_MODEL}
DEV_REASON = "Testmodus: Werte vom Fake-Provider, nicht aus dem Beleg"

DEV_CLASSIFY = ClassifyOutput(
    doc_type="generic_bill",  # type: ignore[arg-type]
    tax_relevant=True,
    readable=True,
    multiple_documents=False,
    total_gross="12.34",
    currency="EUR",
    document_date="2025-06-01",  # type: ignore[arg-type]
    vendor="Testmodus Beispiel GmbH",
    certificate_year=None,
    reason_de=DEV_REASON,
    confidence="high",  # type: ignore[arg-type]
)
DEV_EXTRACT = GenericBillExtraction.model_validate(
    {
        "vendor": "Testmodus Beispiel GmbH",
        "recipient_name": None,
        "invoice_date": "2025-06-01",
        "payment_date": "2025-06-01",
        "payment_method": "card",
        "currency": "EUR",
        "total_gross": "12.34",
        "total_vat": None,
        "is_credit_note": False,
        "line_items": [
            {
                "description": "Testmodus Arbeitsmittel",
                "gross_amount": "12.34",
                "category": "wk_arbeitsmittel",
                "cost_kind_35a": "not_applicable",
            }
        ],
        "stated_labour_amount_35a": None,
        "reason_de": DEV_REASON,
        "confidence": "high",
    }
)


def is_dev_fake(classify_model: str, extract_model: str) -> bool:
    return classify_model == FAKE_MODEL and extract_model == FAKE_MODEL


def scripted_router(
    base: LLMRouter, script: Sequence[ClassifyOutput | GenericBillExtraction | LLMError]
) -> LLMRouter:
    """`base` with a fresh `FakeProvider` answering `script` in order (refused in production)."""
    items = [item if isinstance(item, LLMError) else FakeReply(data=item) for item in script]
    return base.with_providers({"fake": FakeProvider(items, app_env=base.settings.app_env)})


def dev_router(base: LLMRouter) -> LLMRouter:
    return scripted_router(base, [DEV_CLASSIFY, DEV_EXTRACT])


def fake_base_router() -> LLMRouter:
    """A router whose tasks both route to `fake:test` (no fallback), for scripted runs."""
    settings = LLMSettings(
        _env_file=None,
        llm_classify_model=FAKE_MODEL,
        llm_extract_model=FAKE_MODEL,
        llm_fallback_model="none",
    )
    return LLMRouter(settings)
