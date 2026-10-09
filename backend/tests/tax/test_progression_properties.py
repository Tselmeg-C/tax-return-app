"""Property tests of `tariff_with_progression` (#86), stdlib only (a fixed grid, no randomness)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.enums import FilingStatus
from app.tax.models import ProgressionParams
from app.tax.progression import tariff_with_progression
from app.tax.tariff import income_tax
from app.tax_params import load_params, supported_years

D = Decimal
XS = [0, 1, 5000, 12096, 12097, 17443, 30001, 40000, 55555, 68480, 90000, 100000, 277825, 400000]
LS = [0, 1, 999, 5000, 15000, 20000, 61234]
FILINGS = [FilingStatus.SINGLE, FilingStatus.JOINT]
_FOUR = ProgressionParams(source="t", rate_decimals="4")


@pytest.mark.parametrize("year", supported_years())
@pytest.mark.parametrize("filing", FILINGS)
def test_zero_lohnersatz_equals_income_tax_on_a_grid(year: int, filing: FilingStatus) -> None:
    p = load_params(year)
    for i in range(200):
        zve = i * 1777 + (i % 7)
        assert tariff_with_progression(
            zve, filing, D(0), p.tariff, p.progressionsvorbehalt
        ) == income_tax(zve, filing, p.tariff)


@pytest.mark.parametrize("year", supported_years())
@pytest.mark.parametrize("filing", FILINGS)
def test_bounds_and_monotonicity(year: int, filing: FilingStatus) -> None:
    p = load_params(year)

    def f(x: int, ls: int) -> Decimal:
        return tariff_with_progression(x, filing, D(ls), p.tariff, p.progressionsvorbehalt)

    assert all(f(0, ls) == 0 for ls in LS)
    for x in XS:
        plain = income_tax(x, filing, p.tariff)
        prev = None
        for ls in LS:
            v = f(x, ls)
            assert plain - 1 <= v <= max(x, 0) or v == plain  # never below plain by more than 1
            assert v >= plain - 1
            assert v <= x
            if prev is not None:
                assert v >= prev - 1  # non-decreasing in lohnersatz (floor: 1 euro slack)
            prev = v
    for ls in LS:
        vals = [f(x, ls) for x in XS]
        assert all(b >= a - 1 for a, b in zip(vals, vals[1:], strict=False))


@pytest.mark.parametrize("year", supported_years())
def test_joint_is_splitting_of_halves(year: int) -> None:
    p = load_params(year)
    for x in (40000, 100000, 123456, 250000):
        for ls in (0, 10000, 20000, 60000):
            joint = tariff_with_progression(
                x, FilingStatus.JOINT, D(ls), p.tariff, p.progressionsvorbehalt
            )
            half = tariff_with_progression(
                x // 2, FilingStatus.SINGLE, D(ls // 2), p.tariff, p.progressionsvorbehalt
            )
            if x % 2 == 0:
                # floor(2y) vs 2 floor(y): the joint result is at most 1 euro above
                assert 2 * half <= joint <= 2 * half + 1


@pytest.mark.parametrize("year", supported_years())
def test_four_decimals_never_above_exact_and_close(year: int) -> None:
    p = load_params(year)
    for x in XS:
        for ls in LS:
            for filing in FILINGS:
                exact = tariff_with_progression(x, filing, D(ls), p.tariff, p.progressionsvorbehalt)
                four = tariff_with_progression(x, filing, D(ls), p.tariff, _FOUR)
                assert four <= exact
                assert exact - four <= D(x) / 10000 + 1
