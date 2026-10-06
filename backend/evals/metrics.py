"""Metrics over scored cases (definitions: issue #7 / `evals/README.md`).

A failed case (`error_kind` set) or a `None` field counts as wrong in every metric that uses
that field. Ratios have 4 decimals; € amounts are decimal strings with 2 decimals (cost
metrics 4, since a document costs fractions of a cent); a metric whose denominator is 0 is
`None` (shown as `n/a`). Decimal only, never float, for money.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.domain.enums import CATEGORY_GROUP, Category, CategoryGroup
from evals.schema import CaseFields, CaseResult, ExpectedFields, Prediction

CENT = Decimal("0.01")
COST_Q = Decimal("0.0001")

HIGHER_IS_BETTER = frozenset(
    {
        "relevance_precision",
        "relevance_recall",
        "relevance_f1",
        "doc_type_accuracy",
        "category_accuracy",
        "category_group_accuracy",
        "gross_exact_match",
        "deductible_exact_match",
        "tax_year_accuracy",
        "payment_method_accuracy",
        "invoice_date_exact_match",
        "vendor_match",
        "person_hint_match",
    }
)
LOWER_IS_BETTER = frozenset(
    {
        "n_errors",
        "error_rate",
        "deductible_abs_error_eur_mean",
        "deductible_abs_error_eur_max",
        "deductible_abs_error_eur_sum",
        "overclaim_eur_sum",
        "underclaim_eur_sum",
        "labour_share_35a_abs_error_eur_mean",
        "cost_eur_total",
        "cost_eur_per_doc_mean",
        "cost_eur_per_doc_p95",
        "input_tokens_total",
        "output_tokens_total",
        "latency_ms_p50",
        "latency_ms_p95",
    }
)
SCALAR_METRICS = HIGHER_IS_BETTER | LOWER_IS_BETTER | {"n_cases"}
"""Metric names usable in `thresholds.yaml` (`per_group` / `top_confusions` are tables)."""

MONEY_METRICS = frozenset(
    {
        "deductible_abs_error_eur_mean",
        "deductible_abs_error_eur_max",
        "deductible_abs_error_eur_sum",
        "overclaim_eur_sum",
        "underclaim_eur_sum",
        "labour_share_35a_abs_error_eur_mean",
        "cost_eur_total",
        "cost_eur_per_doc_mean",
        "cost_eur_per_doc_p95",
    }
)


@dataclass(frozen=True)
class Scored:
    case_id: str
    tags: tuple[str, ...]
    expected: ExpectedFields
    predicted: Prediction
    wall_ms: int


# --- helpers ----------------------------------------------------------------------------


def ratio(num: int, den: int) -> float | None:
    return None if den == 0 else round(num / den, 4)


def money(value: Decimal, q: Decimal = CENT) -> str:
    return format(value.quantize(q, rounding=ROUND_HALF_UP), "f")


def nearest_rank(values: Sequence[Any], p: float) -> Any:
    """Nearest-rank percentile: the value at rank ceil(p/100 * N) of the sorted list."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[rank - 1]


def effective(pred: Prediction) -> Prediction:
    """A failed case predicts nothing, whatever the predictor filled in."""
    if pred.error_kind is not None:
        return Prediction(error_kind=pred.error_kind, calls=pred.calls)
    return pred


def predicted_deductible(pred: Prediction) -> Decimal | None:
    if pred.tax_relevant is False:
        return Decimal("0.00")
    return pred.deductible_amount


def _norm(text: str | None) -> str | None:
    return None if text is None else " ".join(text.casefold().split())


def group_of(category: Category | None) -> CategoryGroup | None:
    return None if category is None else CATEGORY_GROUP[category]


def case_cost(pred: Prediction) -> Decimal | None:
    if not pred.calls:
        return None
    return sum((c.cost_eur for c in pred.calls), Decimal(0))


def case_latency(s: Scored) -> int:
    if s.predicted.calls:
        return sum(c.latency_ms for c in s.predicted.calls)
    return s.wall_ms


# --- per case ---------------------------------------------------------------------------


def field_matches(exp: ExpectedFields, raw: Prediction) -> dict[str, bool | None]:
    """Per-field correctness; `None` where the field has no expected value."""
    p = effective(raw)
    pd = predicted_deductible(p)

    def opt(expected: Any, ok: Callable[[], bool]) -> bool | None:
        return None if expected is None else ok()

    return {
        "doc_type": p.doc_type == exp.doc_type,
        "tax_relevant": p.tax_relevant is not None and p.tax_relevant == exp.tax_relevant,
        "category": opt(exp.category, lambda: p.category == exp.category),
        "category_group": opt(
            exp.category,
            lambda: p.category is not None and group_of(p.category) == group_of(exp.category),
        ),
        "gross_amount": opt(
            exp.gross_amount,
            lambda: p.gross_amount is not None and p.gross_amount == exp.gross_amount,
        ),
        "deductible_amount": pd is not None and pd == exp.deductible_amount,
        "labour_share_35a": opt(
            exp.labour_share_35a,
            lambda: p.labour_share_35a is not None and p.labour_share_35a == exp.labour_share_35a,
        ),
        "tax_year": opt(exp.tax_year, lambda: p.tax_year == exp.tax_year),
        "payment_method": opt(exp.payment_method, lambda: p.payment_method == exp.payment_method),
        "invoice_date": opt(exp.invoice_date, lambda: p.invoice_date == exp.invoice_date),
    }


def _fields(src: ExpectedFields | Prediction) -> CaseFields:
    return CaseFields(
        doc_type=src.doc_type,
        tax_relevant=src.tax_relevant,
        category=src.category,
        gross_amount=src.gross_amount,
        deductible_amount=src.deductible_amount,
        labour_share_35a=src.labour_share_35a,
        tax_year=src.tax_year,
        payment_method=src.payment_method,
    )


def case_result(s: Scored) -> CaseResult:
    p = effective(s.predicted)
    return CaseResult(
        id=s.case_id,
        tags=list(s.tags),
        error_kind=s.predicted.error_kind,
        expected=_fields(s.expected),
        predicted=_fields(p),
        match=field_matches(s.expected, s.predicted),
        cost_eur=case_cost(s.predicted),
        latency_ms=case_latency(s),
    )


def case_failed(result: CaseResult) -> bool:
    return result.error_kind is not None or any(v is False for v in result.match.values())


# --- aggregate --------------------------------------------------------------------------


def compute_metrics(scored: Sequence[Scored]) -> dict[str, Any]:
    n = len(scored)
    m: dict[str, Any] = {"n_cases": n}
    errors = sum(1 for s in scored if s.predicted.error_kind is not None)
    m["n_errors"] = errors
    m["error_rate"] = ratio(errors, n)

    # relevance: positive = expected.tax_relevant
    tp = fp = fn = 0
    for s in scored:
        pred = effective(s.predicted).tax_relevant
        if s.expected.tax_relevant:
            if pred is True:
                tp += 1
            else:
                fn += 1  # False or None
        elif pred is not False:
            fp += 1  # True or None
    precision = ratio(tp, tp + fp)
    recall = ratio(tp, tp + fn)
    if precision is None or recall is None:
        f1 = None
    elif precision + recall == 0:
        f1 = 0.0
    else:
        f1 = round(2 * precision * recall / (precision + recall), 4)
    m["relevance_precision"] = precision
    m["relevance_recall"] = recall
    m["relevance_f1"] = f1

    matches = [field_matches(s.expected, s.predicted) for s in scored]

    def acc(field: str) -> float | None:
        vals = [mt[field] for mt in matches if mt[field] is not None]
        return ratio(sum(1 for v in vals if v), len(vals))

    m["doc_type_accuracy"] = acc("doc_type")
    m["category_accuracy"] = acc("category")
    m["category_group_accuracy"] = acc("category_group")

    per_group: dict[str, dict[str, Any]] = {}
    for group in CategoryGroup:
        rows = [
            mt
            for s, mt in zip(scored, matches, strict=True)
            if s.expected.category is not None and CATEGORY_GROUP[s.expected.category] is group
        ]
        if rows:
            per_group[group.value] = {
                "n": len(rows),
                "category_accuracy": ratio(sum(1 for r in rows if r["category"]), len(rows)),
            }
    m["per_group"] = per_group

    confusions: Counter[tuple[str, str]] = Counter()
    for s in scored:
        if s.expected.category is None:
            continue
        pc = effective(s.predicted).category
        if pc != s.expected.category:
            confusions[(s.expected.category.value, pc.value if pc else "none")] += 1
    m["top_confusions"] = [
        {"expected": e, "predicted": p, "count": c}
        for (e, p), c in sorted(confusions.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
    ]

    m["gross_exact_match"] = acc("gross_amount")
    m["deductible_exact_match"] = acc("deductible_amount")

    diffs: list[Decimal] = []
    for s in scored:
        pd = predicted_deductible(effective(s.predicted))
        diffs.append((pd if pd is not None else Decimal(0)) - s.expected.deductible_amount)
    abs_diffs = [abs(d) for d in diffs]
    if n:
        m["deductible_abs_error_eur_mean"] = money(sum(abs_diffs, Decimal(0)) / n)
        m["deductible_abs_error_eur_max"] = money(max(abs_diffs))
    else:
        m["deductible_abs_error_eur_mean"] = None
        m["deductible_abs_error_eur_max"] = None
    m["deductible_abs_error_eur_sum"] = money(sum(abs_diffs, Decimal(0)))
    m["overclaim_eur_sum"] = money(sum((d for d in diffs if d > 0), Decimal(0)))
    m["underclaim_eur_sum"] = money(sum((-d for d in diffs if d < 0), Decimal(0)))

    labour_errors = []
    for s in scored:
        if s.expected.labour_share_35a is None:
            continue
        pl = effective(s.predicted).labour_share_35a
        labour_errors.append(
            abs((pl if pl is not None else Decimal(0)) - s.expected.labour_share_35a)
        )
    m["labour_share_35a_abs_error_eur_mean"] = (
        money(sum(labour_errors, Decimal(0)) / len(labour_errors)) if labour_errors else None
    )

    m["tax_year_accuracy"] = acc("tax_year")
    m["payment_method_accuracy"] = acc("payment_method")
    m["invoice_date_exact_match"] = acc("invoice_date")

    def text_match(get: Callable[[ExpectedFields | Prediction], str | None]) -> float | None:
        rows = [(get(s.expected), get(effective(s.predicted))) for s in scored]
        rows = [(e, p) for e, p in rows if e is not None]
        return ratio(sum(1 for e, p in rows if _norm(e) == _norm(p)), len(rows))

    m["vendor_match"] = text_match(lambda x: x.vendor)
    m["person_hint_match"] = text_match(lambda x: x.person_hint)

    if any(s.predicted.calls for s in scored):
        costs = [case_cost(s.predicted) or Decimal(0) for s in scored]
        total = sum(costs, Decimal(0))
        m["cost_eur_total"] = money(total, COST_Q)
        m["cost_eur_per_doc_mean"] = money(total / n, COST_Q)
        m["cost_eur_per_doc_p95"] = money(nearest_rank(costs, 95), COST_Q)
        m["input_tokens_total"] = sum(c.input_tokens for s in scored for c in s.predicted.calls)
        m["output_tokens_total"] = sum(c.output_tokens for s in scored for c in s.predicted.calls)
    else:
        for key in (
            "cost_eur_total",
            "cost_eur_per_doc_mean",
            "cost_eur_per_doc_p95",
            "input_tokens_total",
            "output_tokens_total",
        ):
            m[key] = None

    latencies = [case_latency(s) for s in scored]
    m["latency_ms_p50"] = nearest_rank(latencies, 50)
    m["latency_ms_p95"] = nearest_rank(latencies, 95)
    return m


def metric_number(value: Any) -> Decimal | None:
    """A scalar metric value as a Decimal for comparisons (money strings included)."""
    if value is None:
        return None
    return Decimal(str(value))
