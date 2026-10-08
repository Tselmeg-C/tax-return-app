"""Hand-computed reference cases for the deduction rules (#15). See README.md.

The runner fails (never skips) for a case without `source`, `arithmetic` or `expected`, and for
a supported year without a file. It only reads the YAML and calls `app.tax.deductions`.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.domain.enums import (
    AttentionReason,
    Category,
    FilingStatus,
    PaymentMethod,
)
from app.tax.deductions import (
    UNASSIGNED,
    ChildInput,
    EmploymentInput,
    ItemInput,
    ReturnContext,
    entfernungspauschale,
    homeoffice_pauschale,
    sonderausgaben,
    werbungskosten,
)
from app.tax_params import load_params, supported_years

HERE = Path(__file__).parent
GROUPS = ("werbungskosten", "sonderausgaben")
REQUIRED = ("id", "rule", "source", "arithmetic", "input", "expected")


def _load(group: str, year: int) -> list[dict[str, Any]]:
    path = HERE / f"{group}_{year}.yaml"
    assert path.is_file(), f"reference file missing: {path.name}"
    cases = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(cases, list) and cases, path.name
    return cases


def _all_cases() -> list[Any]:
    params = []
    for year in supported_years():
        for group in GROUPS:
            for case in _load(group, year):
                params.append(pytest.param(year, case, id=f"{year}-{case.get('id')}"))
    return params


def _pid(name: str | None) -> str | None:
    return None if name is None else f"p-{name}"


def _ctx(inp: dict[str, Any], year: int) -> ReturnContext:
    joint = inp["filing"] == "joint"
    return ReturnContext(
        year, FilingStatus.JOINT if joint else FilingStatus.SINGLE, "p-A", "p-B" if joint else None
    )


def _items(inp: dict[str, Any], year: int) -> list[ItemInput]:
    return [
        ItemInput(
            id=f"i{n}",
            person_id=_pid(raw["person"]),
            category=Category(raw["category"]),
            year=year,
            deductible_amount=Decimal(raw["amount"]),
            labour_share_35a=None,
            payment_method=PaymentMethod(raw.get("payment", "bank_transfer")),
            is_relevant=True,
            overridden_by_user=False,
            attention_reason=AttentionReason(raw["attention"]) if raw.get("attention") else None,
        )
        for n, raw in enumerate(inp["items"])
    ]


def _eq(actual: object, expected: object) -> bool:
    if isinstance(expected, bool):
        return actual is expected
    return actual == Decimal(str(expected))


def _check_notes(case: dict[str, Any], result: Any) -> None:
    if "notes" in case["expected"]:
        got = sorted({n.code.value for n in result.notes})
        assert got == sorted(case["expected"]["notes"]), case["id"]


def _run_werbungskosten(year: int, case: dict[str, Any]) -> None:
    inp, exp = case["input"], case["expected"]
    emps = [
        EmploymentInput(_pid(e["person"]) or "", e["km"], e["office_days"], e["homeoffice_days"])
        for e in inp["employments"]
    ]
    result = werbungskosten(
        _ctx(inp, year), _items(inp, year), emps, load_params(year).werbungskosten
    )
    for name, fields in exp.get("persons", {}).items():
        person = result.by_person[f"p-{name}"]
        for field, want in fields.items():
            assert _eq(getattr(person, field), want), (case["id"], name, field)
    if "persons" in exp:
        assert set(result.by_person) >= {f"p-{n}" for n in exp["persons"]}
    if "total_applied" in exp:
        assert _eq(result.total_applied, exp["total_applied"]), case["id"]
    _check_notes(case, result)


def _run_sonderausgaben(year: int, case: dict[str, Any]) -> None:
    inp, exp = case["input"], case["expected"]
    kids = [
        ChildInput(
            f"p-{c['id']}",
            date.fromisoformat(c["dob"]),
            c["disability_grade"],
            c["months"],
            c["allowance_half"],
            c["in_household"],
        )
        for c in inp["children"]
    ]
    result = sonderausgaben(
        _ctx(inp, year),
        _items(inp, year),
        kids,
        Decimal(inp["gde"]),
        Decimal(inp["kirchensteuer_official"]),
        load_params(year).sonderausgaben,
    )
    plain = (
        "kirchensteuer",
        "spenden_net",
        "spenden_limit",
        "spenden_deductible",
        "spenden_excess",
        "actual_total",
        "pauschbetrag",
        "applied",
        "used_pauschbetrag",
    )
    for field in plain:
        if field in exp:
            assert _eq(getattr(result, field), exp[field]), (case["id"], field)
    for attr, key in (
        ("kinderbetreuung_by_child", "kinderbetreuung"),
        ("schulgeld_by_child", "schulgeld"),
    ):
        if key in exp:
            lines = getattr(result, attr)
            want = {(UNASSIGNED if k == "-" else f"p-{k}"): v for k, v in exp[key].items()}
            assert {k: v.deductible for k, v in lines.items()} == {
                k: Decimal(v) for k, v in want.items()
            }, (case["id"], key)
        if f"{key}_cap_reached" in exp:
            lines = getattr(result, attr)
            for k, v in exp[f"{key}_cap_reached"].items():
                assert lines[f"p-{k}"].cap_reached is v, (case["id"], key)
    _check_notes(case, result)


@pytest.mark.parametrize(("year", "case"), _all_cases())
def test_reference_case(year: int, case: dict[str, Any]) -> None:
    for key in REQUIRED:
        assert case.get(key), f"{case.get('id')}: missing {key}"
    assert "§" in case["source"] or "I6" in case["source"], case["id"]
    rule = case["rule"]
    p = load_params(year)
    if rule == "W1":
        total = sum(
            (
                entfernungspauschale(e["km"], e["days"], p.werbungskosten)
                for e in case["input"]["employments"]
            ),
            Decimal(0),
        )
        assert total == Decimal(case["expected"]), case["id"]
    elif rule == "W2":
        days = sum(case["input"]["homeoffice_days"])
        assert homeoffice_pauschale(days, p.werbungskosten) == Decimal(case["expected"])
    elif rule.startswith("W"):
        _run_werbungskosten(year, case)
    else:
        _run_sonderausgaben(year, case)


def test_every_year_and_group_has_a_file() -> None:
    assert supported_years() == (2025, 2026)
    for year in supported_years():
        for group in GROUPS:
            assert len(_load(group, year)) >= 20


def test_case_ids_are_unique_per_file() -> None:
    for year in supported_years():
        for group in GROUPS:
            ids = [c["id"] for c in _load(group, year)]
            assert len(ids) == len(set(ids))


def test_at_least_45_cases_per_year() -> None:
    for year in supported_years():
        assert sum(len(_load(g, year)) for g in GROUPS) >= 45
