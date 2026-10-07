"""Tariff core vs. the BMF Einkommensteuerrechner, tolerance 0 (#14 Decision 1).

Fails (never skips) when a supported year has no golden file, a case has no `expected`
or the source block is incomplete. The calculator shows ESt and Soli only, so `kist` is
compared only where a golden case carries it (unit tests cover Kirchensteuer).
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.domain.enums import FilingStatus
from app.tax.tariff import tariff_result
from app.tax_params import load_params, supported_years

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
# The minimum case set of #14 (none was moved to unit tests: the calculator accepts all).
CASE_IDS = """
single_zero single_gfb single_gfb_plus1 single_first_tax single_zone2_end single_zone3_start
single_zone3_end single_zone4_start single_zone4_end single_top_rate single_huge
single_soli_last_free single_soli_first single_soli_milderung single_soli_full joint_2gfb
joint_odd joint_100k joint_soli_last_free joint_soli_first joint_top_rate single_church_8
single_church_9 single_church_no_soli joint_church_8 joint_church_9
""".split()


def golden_file(year: int) -> dict[str, Any]:
    path = GOLDEN_DIR / f"tariff_{year}.yaml"
    assert path.is_file(), f"no golden file for supported year {year}: {path.name}"
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data


def _cases() -> list[Any]:
    params = []
    for year in supported_years():
        path = GOLDEN_DIR / f"tariff_{year}.yaml"
        if not path.is_file():
            params.append(pytest.param(year, None, id=f"{year}-missing-golden-file"))
            continue
        for case in golden_file(year)["cases"]:
            params.append(pytest.param(year, case, id=f"{year}-{case['id']}"))
    return params


@pytest.mark.parametrize("year", supported_years())
def test_golden_source_block(year: int) -> None:
    data = golden_file(year)
    assert data["year"] == year
    source = data["source"]
    assert source.get("url"), "source.url missing"
    assert source.get("fetched_at"), "source.fetched_at missing"
    assert source.get("method") in ("script", "manual")
    assert [case["id"] for case in data["cases"]] == CASE_IDS


@pytest.mark.parametrize(("year", "case"), _cases())
def test_golden_case(year: int, case: dict[str, Any] | None) -> None:
    assert case is not None, f"no golden file for {year}"
    expected = case.get("expected")
    assert expected, f"{case['id']}: no expected values (collect them, see golden/README.md)"
    inp = case["input"]
    result = tariff_result(
        Decimal(inp["zve"]), FilingStatus(inp["filing"]), inp["church_state"], load_params(year)
    )
    assert result.est == Decimal(expected["est"])
    assert result.soli == Decimal(expected["soli"])
    if "kist" in expected:
        assert result.kist == Decimal(expected["kist"])
