"""Metric unit tests on hand-written predictions."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from evals.metrics import Scored, compute_metrics, nearest_rank
from evals.report import fmt
from evals.schema import CallUsage, ExpectedFields, Prediction


def exp(**overrides: Any) -> ExpectedFields:
    base: dict[str, Any] = {
        "doc_type": "generic_bill",
        "tax_relevant": True,
        "category": "krankheitskosten",
        "gross_amount": "100.00",
        "deductible_amount": "100.00",
        "labour_share_35a": None,
        "invoice_date": date(2025, 3, 1),
        "payment_date": date(2025, 3, 2),
        "tax_year": 2025,
        "payment_method": "card",
        "vendor": "Optik Beispiel",
        "person_hint": "Alex Muster",
    }
    if overrides.get("tax_relevant") is False and "deductible_amount" not in overrides:
        base["deductible_amount"] = "0.00"
    base.update(overrides)
    return ExpectedFields.model_validate(base)


def perfect(e: ExpectedFields, **overrides: Any) -> Prediction:
    data = Prediction.from_expected(e).model_dump(mode="json")
    data.update(overrides)
    return Prediction.model_validate(data)


def scored(*pairs: tuple[ExpectedFields, Prediction], wall_ms: int = 5) -> list[Scored]:
    return [Scored(f"c{i:03d}", (), e, p, wall_ms) for i, (e, p) in enumerate(pairs)]


def test_relevance_precision_recall_f1_hand_computed() -> None:
    pos1 = exp()
    pos2 = exp(category="pflege")
    neg1 = exp(tax_relevant=False, category="irrelevant")
    neg2 = exp(tax_relevant=False, category="irrelevant")
    m = compute_metrics(
        scored(
            (pos1, perfect(pos1)),  # TP
            (pos2, perfect(pos2, tax_relevant=False)),  # FN
            (neg1, perfect(neg1, tax_relevant=True)),  # FP
            (neg2, perfect(neg2)),  # TN
        )
    )
    # TP=1, FP=1, FN=1 -> P = 1/2, R = 1/2, F1 = 1/2
    assert m["relevance_precision"] == 0.5
    assert m["relevance_recall"] == 0.5
    assert m["relevance_f1"] == 0.5


def test_relevance_without_positive_case_is_null_and_shown_na() -> None:
    neg = exp(tax_relevant=False, category="irrelevant")
    m = compute_metrics(scored((neg, perfect(neg)), (neg, perfect(neg))))
    assert m["relevance_precision"] is None
    assert m["relevance_recall"] is None
    assert m["relevance_f1"] is None
    assert fmt(m["relevance_precision"]) == "n/a"


def test_none_and_error_predictions_count_as_wrong() -> None:
    pos = exp(category="handwerkerleistung", labour_share_35a="60.00")
    neg = exp(tax_relevant=False, category="irrelevant")
    nothing = Prediction()
    failed = perfect(pos, error_kind="ValueError")  # fields ignored when error_kind is set
    m = compute_metrics(scored((pos, nothing), (neg, Prediction()), (pos, failed)))
    assert m["relevance_recall"] == 0.0  # both positives are FN
    assert m["relevance_precision"] == 0.0  # the negative with None is a FP
    assert m["category_accuracy"] == 0.0
    assert m["gross_exact_match"] == 0.0
    assert m["deductible_exact_match"] == 0.0
    assert m["tax_year_accuracy"] == 0.0
    assert m["n_errors"] == 1
    assert m["error_rate"] == round(1 / 3, 4)
    # None -> 0 for the absolute error
    assert m["deductible_abs_error_eur_sum"] == "200.00"
    assert m["labour_share_35a_abs_error_eur_mean"] == "60.00"


def test_decimal_exact_match_and_credit_note() -> None:
    small = exp(gross_amount="0.30", deductible_amount="0.30")
    pred_sum = Decimal("0.10") + Decimal("0.20")
    credit = exp(category="kinderbetreuung", gross_amount="-120.00", deductible_amount="-120.00")
    m = compute_metrics(
        scored(
            (small, perfect(small, gross_amount=str(pred_sum), deductible_amount=str(pred_sum))),
            (credit, perfect(credit)),
        )
    )
    assert m["gross_exact_match"] == 1.0
    assert m["deductible_exact_match"] == 1.0
    m2 = compute_metrics(scored((credit, perfect(credit, deductible_amount="-100.00"))))
    assert m2["deductible_abs_error_eur_sum"] == "20.00"
    assert m2["overclaim_eur_sum"] == "20.00"  # -100 is 20 more than -120
    assert m2["underclaim_eur_sum"] == "0.00"


def test_over_and_underclaim_add_up_to_abs_error_sum() -> None:
    a = exp(deductible_amount="100.00")
    b = exp(deductible_amount="50.00")
    c = exp(tax_relevant=False, category="irrelevant")
    m = compute_metrics(
        scored(
            (a, perfect(a, deductible_amount="130.25")),  # +30.25
            (b, perfect(b, deductible_amount="10.00")),  # -40.00
            (c, perfect(c, tax_relevant=True, category="pflege", deductible_amount="7.50")),
        )
    )
    over, under = Decimal(m["overclaim_eur_sum"]), Decimal(m["underclaim_eur_sum"])
    assert over == Decimal("37.75")
    assert under == Decimal("40.00")
    assert over + under == Decimal(m["deductible_abs_error_eur_sum"])


def test_tax_relevant_false_prediction_counts_as_zero_deductible() -> None:
    e = exp(tax_relevant=False, category="irrelevant")
    m = compute_metrics(scored((e, perfect(e, deductible_amount=None))))
    assert m["deductible_exact_match"] == 1.0


def test_nearest_rank_percentiles_and_null_cost_without_calls() -> None:
    values = list(range(100, 2001, 100))  # 20 values
    assert nearest_rank(values, 50) == 1000  # rank ceil(10) = 10
    assert nearest_rank(values, 95) == 1900  # rank ceil(19) = 19
    e = exp()
    rows = [Scored(f"c{i}", (), e, perfect(e), ms) for i, ms in enumerate(reversed(values))]
    m = compute_metrics(rows)
    assert m["latency_ms_p50"] == 1000
    assert m["latency_ms_p95"] == 1900
    for key in ("cost_eur_total", "cost_eur_per_doc_mean", "cost_eur_per_doc_p95"):
        assert m[key] is None
    assert m["input_tokens_total"] is None


def test_latency_and_cost_come_from_calls() -> None:
    e = exp()
    call = CallUsage(
        provider="openai",
        model="m",
        step="extract",
        input_tokens=10,
        output_tokens=2,
        cost_eur=Decimal("0.0030"),
        latency_ms=700,
    )
    m = compute_metrics(scored((e, perfect(e, calls=[call.model_dump(mode="json")]))))
    assert m["latency_ms_p50"] == 700
    assert m["cost_eur_total"] == "0.0030"
    assert m["input_tokens_total"] == 10


def test_category_group_accuracy_same_group_wrong_category() -> None:
    e = exp(category="wk_arbeitsmittel")
    m = compute_metrics(scored((e, perfect(e, category="wk_fortbildung"))))
    assert m["category_accuracy"] == 0.0
    assert m["category_group_accuracy"] == 1.0
    assert m["per_group"]["werbungskosten"] == {"n": 1, "category_accuracy": 0.0}
    assert m["top_confusions"] == [
        {"expected": "wk_arbeitsmittel", "predicted": "wk_fortbildung", "count": 1}
    ]
