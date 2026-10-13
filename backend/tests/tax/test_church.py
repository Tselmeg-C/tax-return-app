"""Church tax of a joint return with one member (#53): reference cases, Kirchgeld Stufen, guards."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.domain.enums import FilingStatus
from app.tax.assessment import festsetzung
from app.tax.church import KistMethod, MemberShare, church_tax_joint, kirchgeld
from app.tax.deductions import ReturnContext
from app.tax.tariff import church_tax
from app.tax_params import load_params, supported_years

D = Decimal
REFERENCE = Path(__file__).parent / "reference"
YEARS = (2025, 2026)


def _cases(year: int) -> list[dict[str, Any]]:
    path = REFERENCE / f"kirchensteuer_{year}.yaml"
    assert path.is_file(), f"reference file missing: {path.name}"  # never skipped
    cases = yaml.safe_load(path.read_text(encoding="utf-8"))
    for case in cases:
        assert all(case.get(k) for k in ("source", "arithmetic", "expected")), case["id"]
    return cases


def test_every_supported_year_has_cases() -> None:
    assert set(YEARS) == set(supported_years())


@pytest.mark.parametrize("year", YEARS)
@pytest.mark.parametrize("case_no", range(8))
def test_reference_case(year: int, case_no: int) -> None:
    case = _cases(year)[case_no]
    i, e = case["input"], case["expected"]
    result = church_tax_joint(
        D(i["bmg"]),
        D(i["zve_bmg"]),
        MemberShare(D(i["member"]), D(i["total"])),
        i["state"],
        load_params(year).church_tax,
    )
    assert (str(result.kist), result.method.value) == (e["kist"], e["method"]), case["id"]
    assert sorted(n.code.value for n in result.notes) == sorted(e["notes"]), case["id"]


# Stufen from the Sachsen Bek. Nr. 3 (2025 and 2026), NRW Erlass 2025: lower bound -> annual amount.
STUFEN = [
    (50000, 96), (57500, 156), (70000, 276), (82500, 396), (95000, 540), (107500, 696),
    (120000, 840), (145000, 1200), (170000, 1560), (195000, 1860), (220000, 2220),
    (270000, 2940), (320000, 3600),
]  # fmt: skip


@pytest.mark.parametrize("year", YEARS)
@pytest.mark.parametrize("state", ["SN", "NW", "NI"])
def test_kirchgeld_at_every_stufe_boundary(year: int, state: str) -> None:
    p = load_params(year).church_tax
    assert kirchgeld(D(49999), state, p) == 0
    previous = 0
    for lower, amount in STUFEN:
        assert kirchgeld(D(lower - 1), state, p) == previous, (state, lower)
        assert kirchgeld(D(lower), state, p) == amount, (state, lower)
        previous = amount
    assert kirchgeld(D(10_000_000), state, p) == 3600


def test_land_without_table_and_unknown_land() -> None:
    p = load_params(2025).church_tax
    assert kirchgeld(D(100000), "BY", p) is None
    assert kirchgeld(D(100000), "BW", p) is None
    with pytest.raises(ValueError, match="unknown state"):
        kirchgeld(D(1), "XX", p)
    with pytest.raises(ValueError, match="unknown state"):
        church_tax_joint(D(1), D(1), MemberShare(D(1), D(2)), "XX", p)


def test_rejects_float() -> None:
    p = load_params(2025).church_tax
    with pytest.raises(TypeError):
        church_tax_joint(D(1), D(1), MemberShare(1.0, D(2)), "BY", p)  # type: ignore[arg-type]


def test_no_income_at_all_gives_no_share_and_member_income_above_total_is_capped() -> None:
    p = load_params(2025).church_tax
    zero = church_tax_joint(D(100), D(10), MemberShare(D(0), D(0)), "BY", p)
    assert zero.kist == 0 and not zero.notes
    capped = church_tax_joint(D(1000), D(10), MemberShare(D(5), D(2)), "BY", p)
    assert capped.kist == D("80.00") and capped.method is KistMethod.MEMBER_SHARE


def test_festsetzung_default_and_both_members_are_unchanged() -> None:
    params = load_params(2025)
    ctx = ReturnContext(2025, FilingStatus.JOINT, "A", "B")
    full = festsetzung(D(60000), ctx, "NW", D(0), [], D(0), params)
    assert full.kist_method is KistMethod.FULL
    assert full.kist == church_tax(full.bmg, "NW", params.church_tax)
    one = festsetzung(D(60000), ctx, "NW", D(0), [], D(0), params, MemberShare(D(1), D(2)))
    assert one.kist < full.kist
    assert one.kist_method in (KistMethod.MEMBER_SHARE, KistMethod.KIRCHGELD)
    single_ctx = ReturnContext(2025, FilingStatus.SINGLE, "A")
    single = festsetzung(
        D(60000), single_ctx, "NW", D(0), [], D(0), params, MemberShare(D(1), D(2))
    )
    assert single.kist_method is KistMethod.FULL  # only a joint return is split
