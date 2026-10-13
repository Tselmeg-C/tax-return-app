"""Vorsorge estimate from the gross wage (#85): hand-computed cases and properties."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.tax.sv_estimate import estimate_sv
from app.tax_params import load_params, supported_years

REF = Path(__file__).parent / "reference"


def _cases() -> list[Any]:
    out = []
    for year in supported_years():
        cases = yaml.safe_load((REF / f"sv_estimate_{year}.yaml").read_text(encoding="utf-8"))
        assert len(cases) >= 6
        for c in cases:
            assert c["source"] and c["arithmetic"] and "expected" in c, c["id"]
            out.append(pytest.param(year, c, id=f"{year}-{c['id']}"))
    return out


@pytest.mark.parametrize(("year", "case"), _cases())
def test_reference_case(year: int, case: dict[str, Any]) -> None:
    inp = case["input"]
    est = estimate_sv(
        Decimal(inp["wage"]),
        load_params(year).sozialversicherung,
        children_under_25=inp["children_under_25"],
        childless_surcharge=inp["childless_surcharge"],
    )
    exp = case["expected"]
    if exp is None:
        assert est is None
        return
    assert est is not None
    for field, want in exp.items():
        assert getattr(est, field) == Decimal(want), (case["id"], field)


@pytest.mark.parametrize("year", supported_years())
def test_monotone_until_bbg_then_flat(year: int) -> None:
    p = load_params(year).sozialversicherung
    wages = [Decimal(w) for w in range(25000, 140001, 5000)] + [p.bbg_kv, p.bbg_rv]
    prev = None
    for w in sorted(set(wages)):
        e = estimate_sv(w, p, children_under_25=0, childless_surcharge=True)
        assert e is not None
        if prev is not None:
            assert (e.rv_employee, e.kv, e.pv, e.alv) >= prev
        prev = (e.rv_employee, e.kv, e.pv, e.alv)
    hi = estimate_sv(p.bbg_rv + 1, p, children_under_25=0, childless_surcharge=True)
    top = estimate_sv(p.bbg_rv * 2, p, children_under_25=0, childless_surcharge=True)
    assert hi == top
    assert top is not None and top.kv <= p.bbg_kv * (p.kv_rate + p.kv_zusatz_average) / 2


def test_rejects_float_and_negative() -> None:
    p = load_params(2025).sozialversicherung
    with pytest.raises(TypeError):
        estimate_sv(1.5, p, children_under_25=0, childless_surcharge=False)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        estimate_sv(Decimal(-1), p, children_under_25=0, childless_surcharge=False)
