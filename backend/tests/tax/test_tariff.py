"""Unit tests for the tariff core. Values here come from the formula (PM cross-check table in
#14), not from the BMF calculator; the BMF values are in `golden/`."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from app.domain.enums import FilingStatus
from app.tax.models import STATES
from app.tax.tariff import (
    church_tax,
    income_tax,
    solidarity_surcharge,
    tariff_result,
    zone_formula,
)
from app.tax_params import load_params

D = Decimal
S, J = FilingStatus.SINGLE, FilingStatus.JOINT
YEARS = (2025, 2026)


@pytest.mark.parametrize(
    ("year", "zve", "filing", "est"),
    [
        (2025, 17443, S, 1015), (2026, 17799, S, 1034),
        (2025, 68480, S, 17849), (2026, 69878, S, 18213),
        (2025, 277825, S, 105774), (2026, 277825, S, 105550),
        (2025, 100000, S, 31088), (2026, 100000, S, 30864),
        (2025, 10_000_000, S, 4480753), (2026, 10_000_000, S, 4480529),
        (2025, 100000, J, 21382), (2026, 100000, J, 21096),
    ],
)  # fmt: skip
def test_cross_check_table(year: int, zve: int, filing: FilingStatus, est: int) -> None:
    assert income_tax(zve, filing, load_params(year).tariff) == D(est)


def test_cross_check_soli_2025() -> None:
    soli = load_params(2025).soli
    assert solidarity_surcharge(D(19951), S, soli) == D("0.11")
    assert solidarity_surcharge(D(39902), J, soli) == D("0.23")


@pytest.mark.parametrize("year", YEARS)
def test_continuity_at_zone_boundaries(year: int) -> None:
    t = load_params(year).tariff
    for zone, upper in enumerate(
        (t.grundfreibetrag, t.zone2.upper, t.zone3.upper, t.zone4.upper), start=1
    ):
        gap = abs(zone_formula(zone, upper, t) - zone_formula(zone + 1, upper, t))
        assert gap < 1, (zone, upper, gap)


@pytest.mark.parametrize("year", YEARS)
@pytest.mark.parametrize("filing", [S, J])
@pytest.mark.parametrize("zve", [D(0), D(-5000), D("-0.01"), 0, -5000])
def test_zero_and_losses_give_zero(year: int, filing: FilingStatus, zve: Any) -> None:
    result = tariff_result(zve, filing, "NW", load_params(year))
    assert (result.est, result.soli, result.kist) == (D(0), D(0), D(0))
    assert str(result.soli) == "0.00" and str(result.kist) == "0.00"


@pytest.mark.parametrize("year", YEARS)
def test_cents_are_floored_and_odd_joint_zve_loses_half_euro(year: int) -> None:
    p = load_params(year)
    assert tariff_result(D("50000.99"), S, "BY", p) == tariff_result(D(50000), S, "BY", p)
    assert income_tax(D("100001.99"), J, p.tariff) == income_tax(100001, J, p.tariff)
    assert income_tax(100001, J, p.tariff) == 2 * income_tax(50000, S, p.tariff)


@pytest.mark.parametrize("year", YEARS)
def test_rejects_float_and_non_finite(year: int) -> None:
    p = load_params(year)
    bad_float: Any = 1e6
    with pytest.raises(TypeError):
        income_tax(bad_float, S, p.tariff)
    with pytest.raises(TypeError):
        solidarity_surcharge(bad_float, S, p.soli)
    with pytest.raises(TypeError):
        church_tax(bad_float, "BY", p.church_tax)
    for value in (D("NaN"), D("Infinity"), D("-Infinity")):
        with pytest.raises(ValueError):
            income_tax(value, S, p.tariff)
        with pytest.raises(ValueError):
            solidarity_surcharge(value, J, p.soli)


@pytest.mark.parametrize("year", YEARS)
def test_huge_zve_is_exact_zone5(year: int) -> None:
    t = load_params(year).tariff
    expected = (t.zone5.rate * 10**9 - t.zone5.minus).to_integral_value(rounding="ROUND_FLOOR")
    assert income_tax(10**9, S, t) == expected
    assert income_tax(D(10**9), J, t) == 2 * (
        (t.zone5.rate * 5 * 10**8 - t.zone5.minus).to_integral_value(rounding="ROUND_FLOOR")
    )


@pytest.mark.parametrize("year", YEARS)
@pytest.mark.parametrize("filing", [S, J])
def test_soli_freigrenze(year: int, filing: FilingStatus) -> None:
    soli = load_params(year).soli
    fg = soli.freigrenze_joint if filing is J else soli.freigrenze_single
    assert solidarity_surcharge(fg, filing, soli) == D("0.00")
    assert solidarity_surcharge(fg + 1, filing, soli) > 0
    # Milderungszone caps, far above it the full 5.5 % applies.
    assert solidarity_surcharge(fg + 100, filing, soli) == D("11.90")
    assert solidarity_surcharge(fg * 10, filing, soli) == (fg * 10 * D("0.055")).quantize(D("0.01"))


def test_soli_floors_cents() -> None:
    soli = load_params(2025).soli
    assert solidarity_surcharge(D(100001), S, soli) == D("5500.05")  # 5500.055


@pytest.mark.parametrize("year", YEARS)
def test_church_tax(year: int) -> None:
    ct = load_params(year).church_tax
    assert church_tax(D(1000), None, ct) == D("0.00")
    for state in STATES:
        rate = D("0.08") if state in ("BW", "BY") else D("0.09")
        assert church_tax(D(1000), state, ct) == 1000 * rate
    assert church_tax(D(10691), "BW", ct) == D("855.28")
    assert church_tax(D("1.11"), "NW", ct) == D("0.09")  # 0.0999 floored
    with pytest.raises(ValueError, match="XX"):
        church_tax(D(1000), "XX", ct)


def test_tariff_result_combines_on_est() -> None:
    p = load_params(2025)
    result = tariff_result(D(100000), S, "BY", p)
    assert result.est == D(31088)
    assert result.soli == solidarity_surcharge(D(31088), S, p.soli)
    assert result.kist == D("2487.04")
