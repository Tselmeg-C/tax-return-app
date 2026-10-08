"""Miss classes of the first real run (`recordings/bills_v0/pipeline-openai-v1`) and their
v2 fixes (#9). Offline: FakeProvider replies reproduce what gpt-4.1-mini answered.

Class A: a statement / certificate of payments classified `other` (+ `tax_relevant: false`)
  → booked as irrelevant without extract (b010 b011 b014-b018 b021 b025 b039 b043).
Class B: a statement classified as an official type (Kita → `kindergeld_bescheid`, KV →
  `elterngeld_bescheid`) → parked without a tax item (b013 b023 b024).
Both happen in classify only; extract never ran for them. v2 fixes the classify prompt and
also extracts a relevant `other` document (code).
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest

from app.domain.enums import AttentionReason, Category, DocType
from app.pipeline.core import PipelineInput, run_pipeline
from app.pipeline.dev_fake import fake_base_router, scripted_router
from app.pipeline.preprocess import DEFAULT_LIMITS
from app.pipeline.prompts import load_prompt_set
from app.tax_params import mapping_table
from evals.paths import EVALS_DIR
from tests.pipeline import files
from tests.pipeline.helpers import classify_out, extract_out

TODAY = date(2026, 10, 7)
KV = [
    ("4210.80", "vorsorge_kv_pv", "not_applicable"),
    ("1012.20", "vorsorge_kv_pv", "not_applicable"),
]
CLASS_A = ["b010", "b011", "b014", "b015", "b016", "b017", "b018", "b021", "b025", "b039", "b043"]
CLASS_B = ["b013", "b023", "b024"]


async def _run(*replies: object, version: str = "v2") -> object:
    router = scripted_router(fake_base_router(), list(replies))  # type: ignore[arg-type]
    return await run_pipeline(
        PipelineInput(files.pdf_text(1), "application/pdf", DEFAULT_LIMITS),
        router=router,
        prompt_set=load_prompt_set(version),
        mapping=mapping_table(),
        today=TODAY,
    )


def test_recording_shows_classify_only_misses() -> None:
    """The diagnosis: every under-claimed relevant case stopped after classify."""
    path = EVALS_DIR / "recordings" / "bills_v0" / "pipeline-openai-v1" / "predictions.jsonl"
    if not path.is_file():
        pytest.skip("no v1 recording")
    preds = {
        r["case_id"][:4]: r["prediction"] for r in map(json.loads, path.read_text().splitlines())
    }
    for case in CLASS_A:
        p = preds[case]
        assert [c["step"] for c in p["calls"]] == ["classify"], case
        assert (p["doc_type"], p["tax_relevant"]) == ("other", False), case
    for case in CLASS_B:
        p = preds[case]
        assert [c["step"] for c in p["calls"]] == ["classify"], case
        assert p["doc_type"] in {"kindergeld_bescheid", "elterngeld_bescheid"}, case


async def test_class_a_v1_answer_books_irrelevant() -> None:
    result = await _run(classify_out(doc_type="other", tax_relevant=False, total_gross="5223.00"))
    assert result.extract is None  # type: ignore[attr-defined]
    assert result.rules.draft.category is Category.IRRELEVANT  # type: ignore[attr-defined]


async def test_class_a_relevant_other_is_extracted_now() -> None:
    result = await _run(
        classify_out(doc_type="other", tax_relevant=True, total_gross="5223.00"),
        extract_out(lines=KV, invoice_date="2026-01-20", payment_date="2025-12-15"),
    )
    draft = result.rules.draft  # type: ignore[attr-defined]
    assert (draft.category, draft.deductible_amount, draft.tax_year) == (
        Category.VORSORGE_KV_PV,
        Decimal("5223.00"),
        2025,
    )


async def test_class_a_v2_answer_books_the_deduction() -> None:
    result = await _run(
        classify_out(doc_type="generic_bill", total_gross="96.50"),
        extract_out(
            lines=[("96.50", "handwerkerleistung", "labour")],
            invoice_date="2025-03-10",
            payment_date="2025-03-20",
        ),
    )
    draft = result.rules.draft  # type: ignore[attr-defined]
    assert (draft.deductible_amount, draft.labour_share_35a) == (Decimal("96.50"), Decimal("96.50"))


async def test_class_b_official_type_parks_the_document() -> None:
    result = await _run(classify_out(doc_type="kindergeld_bescheid", total_gross="3180.00"))
    assert result.rules.draft is None  # type: ignore[attr-defined]
    assert result.reasons == [AttentionReason.DOC_TYPE_NOT_SUPPORTED]  # type: ignore[attr-defined]


ALWAYS_BILLS = [
    "Kranken-, Pflege-, Renten-, Riester-, Rürup-",
    "Haftpflicht-",
    "Gebäudeversicherung",
    "Bescheinigung nach § 92 EStG",
    "Sachspende",
    "Kita-, Hort-, Tagesmutter-",
    "Schulgeld-Bescheinigung",
    "Mietbescheinigung for a Zweitwohnung",
    "Gewerkschafts- or Berufsverbandsbeitrag",
    "Schornsteinfeger",
]


@pytest.mark.parametrize("phrase", ALWAYS_BILLS)
def test_v2_classify_names_every_missed_kind(phrase: str) -> None:
    system = load_prompt_set("v2").classify.system
    assert phrase in system
    assert "never a Kita or Tagesmutter statement" in system
    assert "never an insurance statement" in system
    assert "If in doubt, answer true" in system


def test_v1_is_unchanged() -> None:
    assert "If in doubt" not in load_prompt_set("v1").classify.system
    assert DocType.OTHER.value in load_prompt_set("v1").classify.system
