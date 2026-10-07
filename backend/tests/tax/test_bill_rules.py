"""Golden rule cases for `app.tax.bill_rules` (#9 Rules 1-7). One row = one case."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from app.domain.enums import Anlage, AttentionReason, Category, DocType, PaymentMethod
from app.pipeline.schemas import ClassifyOutput, GenericBillExtraction
from app.tax.bill_rules import evaluate, needs_extract
from app.tax_params import mapping_table

R = AttentionReason
C = Category
TODAY = date(2026, 10, 7)
D = Decimal


def classify(**kw: Any) -> ClassifyOutput:
    values: dict[str, Any] = {
        "doc_type": "generic_bill",
        "tax_relevant": True,
        "readable": True,
        "multiple_documents": False,
        "total_gross": "100.00",
        "currency": "EUR",
        "document_date": "2025-03-14",
        "vendor": "Beispiel GmbH",
        "certificate_year": None,
        "reason_de": "Testbeleg.",
        "confidence": "high",
    }
    values.update(kw)
    return ClassifyOutput.model_validate(values)


def line(amount: str, category: str, kind: str = "not_applicable") -> dict[str, str]:
    return {
        "description": "Position",
        "gross_amount": amount,
        "category": category,
        "cost_kind_35a": kind,
    }


def extraction(lines: list[dict[str, str]], **kw: Any) -> GenericBillExtraction:
    total = sum(D(x["gross_amount"]) for x in lines)
    values: dict[str, Any] = {
        "vendor": "Beispiel GmbH",
        "recipient_name": "Alex Muster",
        "invoice_date": "2025-03-14",
        "payment_date": "2025-03-20",
        "payment_method": "bank_transfer",
        "currency": "EUR",
        "total_gross": f"{total:.2f}",
        "total_vat": None,
        "is_credit_note": total < 0,
        "line_items": lines,
        "stated_labour_amount_35a": None,
        "reason_de": "Rechnung.",
        "confidence": "high",
    }
    values.update(kw)
    return GenericBillExtraction.model_validate(values)


@dataclass
class Case:
    id: str
    c: ClassifyOutput
    e: GenericBillExtraction | None
    expect: dict[str, Any] | None  # draft fields to check; None = no item
    reasons: list[AttentionReason] = field(default_factory=list)
    fallback_year: int | None = None


HANDWERK = [
    line("180.00", "handwerkerleistung", "labour"),
    line("132.40", "handwerkerleistung", "material"),
]

CASES = [
    Case(
        "drugstore-prescription-and-cosmetics",
        classify(),
        extraction([line("5.00", "krankheitskosten"), line("12.95", "irrelevant")]),
        {"category": C.KRANKHEITSKOSTEN, "deductible_amount": D("5.00"),
         "gross_amount": D("17.95")},
    ),
    Case(
        "handwerker-labour-and-material-transfer",
        classify(),
        extraction(HANDWERK),
        {"category": C.HANDWERKERLEISTUNG, "deductible_amount": D("180.00"),
         "labour_share_35a": D("180.00"), "is_relevant": True,
         "anlage": Anlage.HAUSHALTSNAHE_AUFWENDUNGEN, "zeile": "6"},
    ),
    Case(
        "handwerker-cash",
        classify(),
        extraction(HANDWERK, payment_method="cash"),
        {"is_relevant": False, "deductible_amount": D("0.00"), "labour_share_35a": D("180.00")},
    ),
    Case(
        "handwerker-payment-unknown",
        classify(),
        extraction(HANDWERK, payment_method="unknown"),
        {"is_relevant": True, "deductible_amount": D("180.00")},
        [R.PAYMENT_METHOD_UNKNOWN_35A],
    ),
    Case(
        "handwerker-stated-labour",
        classify(),
        extraction(
            [line("312.40", "handwerkerleistung", "other")], stated_labour_amount_35a="150.00"
        ),
        {"deductible_amount": D("150.00"), "labour_share_35a": D("150.00")},
    ),
    Case(
        "handwerker-no-labour",
        classify(),
        extraction([line("132.40", "handwerkerleistung", "material")]),
        {"deductible_amount": D("0.00"), "labour_share_35a": None, "is_relevant": True},
        [R.LABOUR_SHARE_MISSING],
    ),
    Case(
        "steuerberatung-split",
        classify(),
        extraction([line("240.00", "steuerberatung"), line("160.00", "irrelevant")]),
        {"category": C.STEUERBERATUNG, "deductible_amount": D("240.00"),
         "gross_amount": D("400.00"), "anlage": Anlage.N, "zeile": "62"},
    ),
    Case(
        "kita-refund",
        classify(total_gross="-150.00"),
        extraction([line("-150.00", "kinderbetreuung")], is_credit_note=True),
        {"category": C.KINDERBETREUUNG, "deductible_amount": D("-150.00"), "is_relevant": True},
    ),
    Case(
        "credit-note-35a",
        classify(),
        extraction([line("-80.00", "haushaltsnahe_dienstleistung", "labour")], is_credit_note=True),
        {"labour_share_35a": None, "deductible_amount": D("0.00")},
        [R.CREDIT_NOTE_35A],
    ),
    Case(
        "two-categories",
        classify(),
        extraction([line("80.00", "wk_arbeitsmittel"), line("20.00", "krankheitskosten")]),
        {"category": C.WK_ARBEITSMITTEL, "deductible_amount": D("80.00")},
        [R.MULTIPLE_CATEGORIES],
    ),
    Case(
        "sum-mismatch",
        classify(),
        extraction([line("50.00", "wk_arbeitsmittel")], total_gross="60.03"),
        {"deductible_amount": D("50.00")},
        [R.SUM_MISMATCH],
    ),
    Case(
        "sum-within-tolerance",
        classify(),
        extraction([line("50.00", "wk_arbeitsmittel")], total_gross="50.02"),
        {"deductible_amount": D("50.00")},
    ),
    Case(
        "sign-mismatch",
        classify(),
        extraction([line("50.00", "wk_arbeitsmittel")], is_credit_note=True),
        {"deductible_amount": D("50.00")},
        [R.SIGN_MISMATCH],
    ),
    Case(
        "implausible-amount",
        classify(),
        extraction([line("100000.01", "v_erhaltung")]),
        {"deductible_amount": D("100000.01")},
        [R.IMPLAUSIBLE_AMOUNT],
    ),
    Case(
        "usd",
        classify(currency="USD"),
        extraction([line("249.00", "wk_fortbildung")], currency="USD"),
        {"deductible_amount": D("0.00"), "gross_amount": D("249.00"), "is_relevant": True},
        [R.FOREIGN_CURRENCY],
    ),
    Case(
        "abfluss-year-boundary",
        classify(),
        extraction(HANDWERK, invoice_date="2025-12-18", payment_date="2026-01-08"),
        {"tax_year": 2026, "deductible_amount": D("180.00")},
    ),
    Case(
        "abfluss-recurring-kinderbetreuung",
        classify(),
        extraction([line("210.00", "kinderbetreuung")], invoice_date="2025-12-18",
                   payment_date="2026-01-08"),
        {"tax_year": 2026},
        [R.YEAR_BOUNDARY_RECURRING],
    ),
    Case(
        "invoice-year-when-unpaid",
        classify(),
        extraction([line("218.70", "krankheitskosten")], invoice_date="2025-10-14",
                   payment_date=None),
        {"tax_year": 2025, "anlage": Anlage.AGB, "zeile": "24"},
    ),
    Case(
        "relevant-without-dates",
        classify(document_date=None),
        extraction([line("49.00", "wk_bewerbung")], invoice_date=None, payment_date=None),
        {"tax_year": None, "anlage": None, "zeile": None},
        [R.DATE_MISSING],
    ),
    Case(
        "relevant-without-dates-fallback-year",
        classify(document_date=None),
        extraction([line("49.00", "wk_bewerbung")], invoice_date=None, payment_date=None),
        {"tax_year": 2026, "anlage": Anlage.N, "zeile": "62"},
        [R.DATE_MISSING],
        fallback_year=2026,
    ),
    Case(
        "irrelevant-without-dates-or-total",
        classify(tax_relevant=False, total_gross=None, document_date=None),
        None,
        {"category": C.IRRELEVANT, "is_relevant": False, "gross_amount": D("0.00"),
         "deductible_amount": D("0.00"), "tax_year": None, "anlage": None},
    ),
    Case(
        "future-date",
        classify(),
        extraction([line("49.00", "wk_bewerbung")], invoice_date="2026-11-01",
                   payment_date="2026-11-01"),
        {"tax_year": 2026},
        [R.IMPLAUSIBLE_DATE],
    ),
    Case(
        "year-2024",
        classify(),
        extraction([line("49.00", "wk_bewerbung")], invoice_date="2024-05-01",
                   payment_date="2024-05-02"),
        {"tax_year": 2024, "anlage": None, "zeile": None},
        [R.UNSUPPORTED_YEAR],
    ),
    Case(
        "classify-only-irrelevant",
        classify(tax_relevant=False, total_gross="11.45", document_date="2025-03-08"),
        None,
        {"category": C.IRRELEVANT, "is_relevant": False, "gross_amount": D("11.45"),
         "deductible_amount": D("0.00"), "invoice_date": date(2025, 3, 8), "tax_year": 2025,
         "payment_method": PaymentMethod.UNKNOWN},
    ),
    Case(
        "extract-all-lines-irrelevant",
        classify(),
        extraction([line("2320.00", "irrelevant", "labour"),
                    line("1480.00", "irrelevant", "material")]),
        {"category": C.IRRELEVANT, "is_relevant": False, "gross_amount": D("3800.00"),
         "deductible_amount": D("0.00")},
    ),
    Case("official", classify(doc_type="lohnsteuerbescheinigung", total_gross=None), None, None,
         [R.DOC_TYPE_NOT_SUPPORTED]),
    Case("multiple-documents", classify(multiple_documents=True), None, None,
         [R.MULTIPLE_DOCUMENTS]),
    Case("unreadable", classify(readable=False), None, None, [R.UNREADABLE]),
    Case(
        "foreign-and-low-confidence",
        classify(confidence="low"),
        extraction([line("249.00", "wk_fortbildung")], currency="USD"),
        {"deductible_amount": D("0.00"), "confidence": D("0.900")},
        [R.FOREIGN_CURRENCY, R.LOW_CONFIDENCE],
    ),
    Case(
        "behinderung-travel",
        classify(),
        extraction([line("192.00", "behinderung", "travel")]),
        {"category": C.BEHINDERUNG, "is_relevant": False, "deductible_amount": D("0.00")},
    ),
    Case(
        "behinderung-umbau",
        classify(),
        extraction([line("900.00", "behinderung")]),
        {"category": C.BEHINDERUNG, "is_relevant": True, "deductible_amount": D("900.00")},
    ),
    Case(
        "asset-above-gwg-afa",
        classify(),
        extraction([line("1240.00", "wk_arbeitszimmer")], total_vat="197.98",
                   invoice_date="2025-08-12", payment_date="2025-08-20"),
        {"deductible_amount": D("39.74"), "gross_amount": D("1240.00"), "tax_year": 2025},
        [R.ASSET_DEPRECIATION],
    ),
    Case(
        "asset-below-gwg",
        classify(),
        extraction([line("888.90", "wk_arbeitsmittel")], total_vat="141.93"),
        {"deductible_amount": D("888.90")},
    ),
]  # fmt: skip


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_rule_case(case: Case) -> None:
    result = evaluate(case.c, case.e, mapping_table(), TODAY, case.fallback_year)
    assert result.reasons == case.reasons
    if case.expect is None:
        assert result.draft is None
        return
    assert result.draft is not None
    actual = {key: getattr(result.draft, key) for key in case.expect}
    assert actual == case.expect
    if not result.draft.is_relevant:
        assert result.draft.deductible_amount == 0


def test_stored_reason_is_the_highest_priority() -> None:
    case = next(c for c in CASES if c.id == "foreign-and-low-confidence")
    result = evaluate(case.c, case.e, mapping_table(), TODAY)
    assert result.stored_reason is R.FOREIGN_CURRENCY


def test_needs_extract() -> None:
    assert needs_extract(classify())
    assert not needs_extract(classify(tax_relevant=False))
    assert needs_extract(classify(doc_type=DocType.OTHER.value))  # v2: relevant `other` too
    assert not needs_extract(classify(doc_type=DocType.OTHER.value, tax_relevant=False))
    assert not needs_extract(classify(doc_type=DocType.KINDERGELD_BESCHEID.value))
    assert not needs_extract(classify(readable=False))
    assert not needs_extract(classify(multiple_documents=True))
