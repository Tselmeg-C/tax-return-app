"""The "perfect reader" (#9 Decision 12): the classify / extract replies a flawless reader
gives for each `bills_v0` case, built from what the generator spec prints on the document
(`evals/synth/specs/bills_v0.yaml`): line items, rows, VAT, vendor, recipient, dates,
payment method and total. Computed label fields (deductible amount, labour share, tax year,
relevance) are never used: the pipeline rules must derive them.

The per-line judgement a reader makes (which line is private, which Fahrdienst line is a
travel cost) is listed below per case id. Used by `--predictor pipeline --provider fake`.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from app.pipeline.schemas import ClassifyOutput, GenericBillExtraction
from evals.synth.generate import CaseSpec, load_spec

CENT = Decimal("0.01")
OFFICIAL_TEMPLATES = frozenset({"lstb", "jstb", "bescheid"})
LABOUR_KINDS = ("labour", "travel", "machine")

PRIVATE_LINES: dict[str, set[int]] = {
    "b012-steuerberatung-split": {1},  # "(privat)" position
    "b023-kita-jahresbescheinigung": {1},  # Verpflegungsgeld
    "b025-schulgeld": {1},  # Mittagessen
    "b028-apotheke-gemischt": {2, 3},  # cosmetics without "R" marker
}
LINE_KINDS: dict[str, dict[int, str]] = {
    "b032-behinderung-fahrdienst": {0: "travel"},  # Fahrdienst = transport cost
}
REASON = "Perfekter Leser: Werte wie gedruckt (synthetische Testdaten)."


def _money(value: Decimal) -> str:
    return f"{value.quantize(CENT, rounding=ROUND_HALF_UP):.2f}"


def _dec(value: Any) -> Decimal:
    return Decimal(str(value))


def _lines(case: CaseSpec, category: str, gross: Decimal) -> list[dict[str, Any]]:
    doc = case.doc
    raw: list[tuple[str, Decimal, str]] = []
    if case.template == "bank_statement":
        for row in doc.get("rows", []):
            if row.get("relevant"):
                raw.append((str(row["text"]), -_dec(row["amount"]), "not_applicable"))
    elif "items" in doc:
        for item in doc["items"]:
            amount = (_dec(item.get("qty", "1")) * _dec(item["price"])).quantize(CENT)
            kind = str(item.get("kind", "labour")) if case.template == "invoice_35a" else None
            raw.append((str(item["text"]), amount, kind or "not_applicable"))
    elif "rows" in doc:
        raw = [(str(r["text"]), _dec(r["amount"]), "not_applicable") for r in doc["rows"]]
    if not raw:
        raw = [("Zuwendung", gross, "not_applicable")]
    private = PRIVATE_LINES.get(case.id, set())
    kinds = LINE_KINDS.get(case.id, {})
    return [
        {
            "description": text[:120],
            "gross_amount": _money(amount),
            "category": "irrelevant" if i in private else category,
            "cost_kind_35a": kinds.get(i, kind),
        }
        for i, (text, amount, kind) in enumerate(raw)
    ]


def _vat(case: CaseSpec, gross: Decimal) -> str | None:
    if case.template == "invoice_35a":
        rate = Decimal(19)
    elif case.template == "invoice":
        rate = _dec(case.doc.get("vat_rate", "19"))
    else:
        return None
    if rate <= 0:
        return None
    return _money(gross * rate / (100 + rate))


def _stated_labour(case: CaseSpec, lines: list[dict[str, Any]]) -> str | None:
    if case.template != "invoice_35a" or not case.doc.get("show_35a", True):
        return None
    return _money(
        sum(
            (_dec(x["gross_amount"]) for x in lines if x["cost_kind_35a"] in LABOUR_KINDS),
            Decimal(0),
        )
    )


def replies(case: CaseSpec) -> tuple[ClassifyOutput, GenericBillExtraction | None]:
    e = case.expected
    category = e.get("category")
    gross_raw = e.get("gross_amount")
    official = e["doc_type"] != "generic_bill"
    irrelevant = category == "irrelevant"
    invoice: date | None = e.get("invoice_date")
    classify = ClassifyOutput.model_validate(
        {
            "doc_type": e["doc_type"],
            "tax_relevant": official or not irrelevant,
            "readable": True,
            "multiple_documents": False,
            "total_gross": gross_raw,
            "currency": "EUR" if gross_raw is not None else None,
            "document_date": invoice,
            "vendor": e.get("vendor"),
            "certificate_year": e.get("tax_year") if case.template in OFFICIAL_TEMPLATES else None,
            "reason_de": REASON,
            "confidence": "high",
        }
    )
    if official or irrelevant or gross_raw is None or category is None:
        return classify, None
    gross = _dec(gross_raw)
    lines = _lines(case, category, gross)
    extract = GenericBillExtraction.model_validate(
        {
            "vendor": e.get("vendor"),
            "recipient_name": e.get("person_hint"),
            "invoice_date": invoice,
            "payment_date": e.get("payment_date"),
            "payment_method": e.get("payment_method", "unknown"),
            "currency": "EUR",
            "total_gross": gross_raw,
            "total_vat": _vat(case, gross),
            "is_credit_note": gross < 0,
            "line_items": lines,
            "stated_labour_amount_35a": _stated_labour(case, lines),
            "reason_de": REASON,
            "confidence": "high",
        }
    )
    return classify, extract


def load_replies(spec_path: Path) -> dict[str, tuple[ClassifyOutput, GenericBillExtraction | None]]:
    return {case.id: replies(case) for case in load_spec(spec_path).cases}
