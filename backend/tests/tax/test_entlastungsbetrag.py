"""Entlastungsbetrag (#81, § 24b EStG): reference cases, params, properties. Deterministic."""

from __future__ import annotations

import random
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.domain.enums import DeductionNote, FilingStatus
from app.tax.deductions import ChildInput, ReturnContext, entlastungsbetrag
from app.tax_params import load_params, supported_years

D = Decimal
HERE = Path(__file__).parent / "reference"


def kid(n: int, months: int = 12, in_household: bool = True) -> ChildInput:
    return ChildInput(f"k{n}", date(2018, 1, 1), None, months, False, in_household)


def _ctx(year: int, joint: bool = False) -> ReturnContext:
    return ReturnContext(
        year, FilingStatus.JOINT if joint else FilingStatus.SINGLE, "p-A", "p-B" if joint else None
    )


def _cases() -> list[Any]:
    out = []
    for year in supported_years():
        path = HERE / f"entlastungsbetrag_{year}.yaml"
        assert path.is_file(), f"reference file missing: {path.name}"
        for case in yaml.safe_load(path.read_text(encoding="utf-8")):
            assert case["source"] and case["arithmetic"] and case["expected"]
            out.append(pytest.param(year, case, id=f"{year}-{case['id']}"))
    return out


@pytest.mark.parametrize(("year", "case"), _cases())
def test_reference(year: int, case: dict[str, Any]) -> None:
    i = case["input"]
    kids = [kid(n, c["months"], c["in_household"]) for n, c in enumerate(i["children"])]
    r = entlastungsbetrag(
        _ctx(year, i["filing"] == "joint"), kids, i["shares"], load_params(year).entlastungsbetrag
    )
    assert r.total == D(case["expected"])
    assert r.total.as_tuple().exponent == -2


@pytest.mark.parametrize("year", [2025, 2026])
def test_params(year: int) -> None:
    e = load_params(year).entlastungsbetrag
    assert (e.base, e.per_further_child) == (D(4260), D(240))
    assert "§ 24b Abs. 2" in e.source and "BGBl" in e.source
    assert not e.provisional  # read in the law text (buzer.de), see the PR table
    assert "entlastungsbetrag" not in load_params(year).provisional_sections


def test_shares_household_note() -> None:
    p = load_params(2025).entlastungsbetrag
    r = entlastungsbetrag(_ctx(2025), [kid(1)], True, p)
    assert [(n.code, n.person_id) for n in r.notes] == [
        (DeductionNote.ENTLASTUNG_HOUSEHOLD_SHARED, "p-A")
    ]
    # no child, nothing to explain
    assert entlastungsbetrag(_ctx(2025), [], True, p).notes == ()


def test_inputs_are_checked() -> None:
    p = load_params(2025).entlastungsbetrag
    with pytest.raises(TypeError):
        entlastungsbetrag(_ctx(2025), [kid(1)], 1, p)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="twice"):
        entlastungsbetrag(_ctx(2025), [kid(1), kid(1)], False, p)
    with pytest.raises(TypeError):
        entlastungsbetrag(_ctx(2025), ["k1"], False, p)  # type: ignore[list-item]


def test_properties() -> None:
    """Order never matters; more children or months never lower it; at most base + n x 240."""
    p = load_params(2026).entlastungsbetrag
    rng = random.Random(81)
    for _ in range(200):
        kids = [kid(n, rng.randint(0, 12), rng.random() < 0.8) for n in range(rng.randint(0, 4))]
        shuffled = rng.sample(kids, len(kids))
        r = entlastungsbetrag(_ctx(2026), kids, False, p)
        assert r.total == entlastungsbetrag(_ctx(2026), shuffled, False, p).total
        counted = [c for c in kids if c.in_household and c.months > 0]
        assert 0 <= r.total <= (p.base + p.per_further_child * max(len(counted) - 1, 0))
        assert (r.total > 0) == bool(counted)
        more = entlastungsbetrag(_ctx(2026), [*kids, kid(9)], False, p)
        assert more.total >= r.total
