"""Property tests of the Kinder rules and `festsetzung` (#87). Stdlib `random`, fixed seed."""

from __future__ import annotations

import random
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.domain.enums import FilingStatus
from app.tax.assessment import festsetzung
from app.tax.deductions import ChildInput, ReturnContext
from app.tax.kinder import guenstigerpruefung, kinder_amounts
from app.tax.progression import tariff_with_progression
from app.tax.tariff import church_tax, solidarity_surcharge
from app.tax_params import load_params

D = Decimal
YEARS = (2025, 2026)


def _ctx(year: int, joint: bool) -> ReturnContext:
    return ReturnContext(
        year, FilingStatus.JOINT if joint else FilingStatus.SINGLE, "p-A", "p-B" if joint else None
    )


def _household(rng: random.Random, n: int) -> list[ChildInput]:
    base = date(2012, 1, 1)
    return [
        ChildInput(
            f"p-K{i}",
            base + timedelta(days=rng.randrange(0, 3000)),
            None,
            rng.choice([0, 1, 5, 7, 12, 12, 12]),
            rng.random() < 0.5,
            rng.random() < 0.5,
        )
        for i in range(n)
    ]


def _cases(count: int = 300) -> list[tuple[int, bool, Decimal, Decimal, Decimal, list[ChildInput]]]:
    rng = random.Random(87)
    return [
        (
            rng.choice(YEARS),
            rng.random() < 0.5,
            D(rng.randrange(0, 300000)),
            D(rng.choice([0, 0, rng.randrange(1, 30000)])),
            D(rng.choice([0, 0, rng.randrange(1, 5000)])),
            _household(rng, rng.randrange(0, 4)),
        )
        for _ in range(count)
    ]


CASES = _cases()


@pytest.mark.parametrize("year", YEARS)
def test_amounts_are_linear_in_months_and_halve(year: int) -> None:
    k = load_params(year).kinder
    for half in (True, False):
        full_year = kinder_amounts(ChildInput("p-K", date(2015, 1, 1), None, 12, half, True), k)
        assert full_year[0] <= (k.kinderfreibetrag + k.bea_freibetrag) * 2
        for m in range(13):
            f, g = kinder_amounts(ChildInput("p-K", date(2015, 1, 1), None, m, half, True), k)
            assert (f, g) == (full_year[0] * m / 12, full_year[1] * m / 12)
    half_y = kinder_amounts(ChildInput("p-K", date(2015, 1, 1), None, 12, True, True), k)
    full_y = kinder_amounts(ChildInput("p-K", date(2015, 1, 1), None, 12, False, True), k)
    assert (half_y[0] * 2, half_y[1] * 2) == full_y


def test_festsetzung_invariants() -> None:
    for year, joint, zve, lohn, erm, kids in CASES:
        p = load_params(year)
        ctx = _ctx(year, joint)
        r = festsetzung(zve, ctx, "NW", lohn, kids, erm, p)

        def est(z: Decimal, p=p, ctx=ctx, lohn=lohn) -> Decimal:
            return tariff_with_progression(z, ctx.filing, lohn, p.tariff, p.progressionsvorbehalt)

        k = r.kinder
        assert k.zve_bmg <= k.zve_tariff <= zve
        assert r.bmg <= est(zve)
        assert r.tarifliche_est - r.kindergeld_added <= est(zve)
        assert r.tarifliche_est <= est(zve) + r.kindergeld_added
        assert r.festzusetzende_est >= 0
        assert r.ermaessigung_applied <= r.tarifliche_est
        assert r.kindergeld_added >= 0
        assert D(0) <= k.freibetrag_used <= k.freibetrag_all
        # the Günstigerprüfung never makes the tax higher than Kindergeld only (each used
        # Freibetrag saved strictly more than its Kindergeld)
        assert r.tarifliche_est <= est(zve)
        assert r.soli == solidarity_surcharge(r.bmg, ctx.filing, p.soli)
        assert r.kist == church_tax(r.bmg, "NW", p.church_tax)
        # permuting the children changes nothing
        assert festsetzung(zve, ctx, "NW", lohn, list(reversed(kids)), erm, p) == r


def test_adding_a_child_never_raises_tax_or_bmg() -> None:
    rng = random.Random(7)
    for year, joint, zve, lohn, erm, kids in CASES:
        p = load_params(year)
        ctx = _ctx(year, joint)
        extra = ChildInput(
            "p-X", date(2013, 1, 1), None, rng.choice([1, 6, 12]), rng.random() < 0.5, True
        )
        before = festsetzung(zve, ctx, None, lohn, kids, erm, p)
        after = festsetzung(zve, ctx, None, lohn, [*kids, extra], erm, p)
        assert after.bmg <= before.bmg
        assert after.festzusetzende_est <= before.festzusetzende_est


def test_non_decreasing_in_zve_up_to_the_euro_step() -> None:
    p = load_params(2025)
    kids = [ChildInput("p-K1", date(2015, 5, 5), None, 12, True, True)]
    ctx = _ctx(2025, False)
    prev = D(0)
    for zve in range(0, 120000, 997):
        tax = festsetzung(D(zve), ctx, None, D(0), kids, D(0), p).festzusetzende_est
        # Freibetrag / Kindergeld switch can cost up to the Kindergeld claim in one step
        assert tax >= prev - 1530
        prev = tax


def test_two_identical_children_second_benefit_not_better_than_first() -> None:
    for year in YEARS:
        p = load_params(year)
        for joint in (True, False):
            ctx = _ctx(year, joint)
            kids = [
                ChildInput(f"p-K{i}", date(2015, 5, 5), None, 12, not joint, True) for i in range(2)
            ]
            for zve in range(20000, 260000, 7919):
                r = guenstigerpruefung(
                    D(zve),
                    kids,
                    ctx.filing,
                    lambda z, p=p, ctx=ctx: tariff_with_progression(
                        z, ctx.filing, D(0), p.tariff, p.progressionsvorbehalt
                    ),
                    p.kinder,
                    year=year,
                )
                first, second = r.lines
                assert second.benefit <= first.benefit + 2  # the tariff's floors, at most 2 euros
                assert not (second.used_freibetrag and not first.used_freibetrag)
