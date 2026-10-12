"""agB (#76): reference cases, end-to-end cases, edge cases, properties. Deterministic."""

from __future__ import annotations

import random
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.domain.enums import AttentionReason, Category, DeductionNote, FilingStatus
from app.tax.deductions import (
    ChildInput,
    PersonInput,
    agb,
    behinderten_pauschbetrag,
    zumutbare_belastung,
)
from app.tax_params import load_params, supported_years

from .deductions.conftest import ctx, item

D = Decimal
HERE = Path(__file__).parent / "reference"
SINGLE, JOINT = FilingStatus.SINGLE, FilingStatus.JOINT


def _cases() -> list[Any]:
    out = []
    for year in supported_years():
        path = HERE / f"agb_{year}.yaml"
        assert path.is_file(), f"reference file missing: {path.name}"
        for case in yaml.safe_load(path.read_text(encoding="utf-8")):
            assert case["source"] and case["arithmetic"] and case["expected"]
            out.append(pytest.param(year, case, id=f"{year}-{case['id']}"))
    return out


@pytest.mark.parametrize(("year", "case"), _cases())
def test_reference(year: int, case: dict[str, Any]) -> None:
    i = case["input"]
    got = zumutbare_belastung(
        D(i["gde"]), FilingStatus(i["filing"]), i["children"], load_params(year).agb
    )
    assert got == D(case["expected"])
    assert got.as_tuple().exponent == -2


@pytest.fixture(params=[2025, 2026])
def p(request: pytest.FixtureRequest):  # noqa: ANN201
    return load_params(request.param).agb


def kid(id: str = "K1", *, grade: int | None = None, months: int = 12, half: bool = False):  # noqa: ANN201
    return ChildInput(id, date(2020, 5, 5), grade, months, half, True)


def adult(id: str = "A", grade: int | None = None, flag: bool = False) -> PersonInput:
    return PersonInput(id, grade, flag)


# --- zumutbare Belastung ---


def test_mutation_whole_gde_method_would_fail(p) -> None:  # noqa: ANN001
    # The pre-2017 method gives 60000 x 7% = 4200,00; the stufenweise value is 3535,30.
    assert zumutbare_belastung(D(60000), SINGLE, 0, p) != D("4200.00")


def test_rows(p) -> None:  # noqa: ANN001
    g = D(60000)
    assert zumutbare_belastung(g, JOINT, 0, p) == D("2935.30")  # 613,60 + 1789,50 + 532,20
    one = zumutbare_belastung(g, SINGLE, 1, p)
    assert one == zumutbare_belastung(g, JOINT, 2, p)  # no Splitting row once children exist
    assert zumutbare_belastung(g, SINGLE, 3, p) == zumutbare_belastung(g, JOINT, 9, p)


def test_float_rejected(p) -> None:  # noqa: ANN001
    with pytest.raises(TypeError):
        zumutbare_belastung(1.5, SINGLE, 0, p)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        zumutbare_belastung(D(1), SINGLE, True, p)  # type: ignore[arg-type]


def test_wrong_bracket_limit_changes_result(p) -> None:  # noqa: ANN001
    # Pins 15 340 (mutation 15 430 changes 767,00 at the limit).
    assert zumutbare_belastung(D(15340), SINGLE, 0, p) == D("767.00")
    assert zumutbare_belastung(D(15430), SINGLE, 0, p) == D("772.40")


# --- Pauschbetrag ---


@pytest.mark.parametrize(
    ("grade", "flag", "expected"),
    [
        (20, False, 384), (30, False, 620), (40, False, 860), (50, False, 1140), (60, False, 1440),
        (70, False, 1780), (80, False, 2120), (90, False, 2460), (100, False, 2840),
        (100, True, 7400), (None, True, 7400), (None, False, 0), (10, False, 0), (25, False, 384),
    ],
)  # fmt: skip
def test_pauschbetrag(p, grade: int | None, flag: bool, expected: int) -> None:  # noqa: ANN001
    assert behinderten_pauschbetrag(grade, flag, p) == D(expected)


# --- agb() end to end ---


def run(items, *, persons=(), kids=(), gde="60000", joint=False, year=2025):  # noqa: ANN001, ANN201
    c = ctx(year, joint)
    return agb(c, items, list(persons), list(kids), D(gde), load_params(year).agb)


@pytest.mark.parametrize(
    ("costs", "after"), [("5000.00", "1464.70"), ("3535.30", "0.00"), ("3535.31", "0.01")]
)
def test_end_to_end(costs: str, after: str) -> None:
    r = run([item(Category.KRANKHEITSKOSTEN, costs)])
    assert r.zumutbare_belastung == D("3535.30")
    assert r.belastung_after_zb == D(after)
    assert r.total == D(after)


def test_pauschbetrag_and_belastung_total() -> None:
    r = run([item(Category.KRANKHEITSKOSTEN, "5000.00")], persons=[adult("A", 50)])
    assert r.pauschbetrag_by_person == {"A": D(1140)}
    assert r.belastung_after_zb == D("1464.70")
    assert r.total == D("2604.70")  # the Pauschbetrag is not reduced by the ZB


def test_pauschbetrag_not_reduced_by_zb_without_costs() -> None:
    r = run([], persons=[adult("A", 100, True)], gde="1000000")
    assert (r.belastung_after_zb, r.total) == (D(0), D(7400))


def test_joint_both_persons_and_no_zwoelftelung() -> None:
    r = run([], persons=[adult("A", 20), adult("B", 60)], joint=True)
    assert r.pauschbetrag_by_person == {"A": D(384), "B": D(1440)}
    assert r.pauschbetrag_total == D(1824)


def test_person_missing_from_persons_has_none() -> None:
    assert run([], joint=True).pauschbetrag_by_person == {"A": D(0), "B": D(0)}


@pytest.mark.parametrize(
    ("joint", "half", "expected"),
    [(True, False, 1140), (True, True, 1140), (False, True, 570), (False, False, 1140)],
)
def test_child_pauschbetrag_transfer(joint: bool, half: bool, expected: int) -> None:
    r = run([], kids=[kid("K1", grade=50, half=half)], joint=joint)
    assert r.pauschbetrag_by_person["K1"] == D(expected)
    assert r.total == D(expected)


def test_child_half_rounding() -> None:
    r = run([], kids=[kid(grade=20, half=True)], persons=[adult("K1", None, True)])
    assert r.pauschbetrag_by_person["K1"] == D("3700.00")  # 7400 / 2, Merkzeichen on the child
    r = run([], kids=[kid(grade=20, half=True)])
    assert r.pauschbetrag_by_person["K1"] == D("192.00")  # 384 / 2


def test_child_without_months_gets_nothing_and_does_not_count_for_zb() -> None:
    r = run([], kids=[kid(grade=50, months=0)])
    assert r.pauschbetrag_by_person == {"A": D(0)}
    assert Note_codes(r) == [DeductionNote.CHILD_NOT_ELIGIBLE]
    # ZB: a child with months = 0 is not counted (single, 0 kids row)
    assert r.zumutbare_belastung == zumutbare_belastung(D(60000), SINGLE, 0, load_params(2025).agb)
    r2 = run([], kids=[kid(months=1)])
    assert r2.zumutbare_belastung == zumutbare_belastung(D(60000), SINGLE, 1, load_params(2025).agb)


def Note_codes(r) -> list[DeductionNote]:  # noqa: ANN001, N802
    return [n.code for n in r.notes]


# --- items ---


def test_costs_zero_and_exactly_zb() -> None:
    assert run([]).belastung_after_zb == D(0)
    assert run([item(Category.PFLEGE, "3535.30")]).belastung_after_zb == D(0)


def test_refund_larger_than_costs_clipped_with_note() -> None:
    r = run(
        [
            item(Category.KRANKHEITSKOSTEN, "100"),
            item(Category.KRANKHEITSKOSTEN, "-300"),
            item(Category.PFLEGE, "4000"),
        ]
    )
    assert r.belastung_gross == D(4000)
    assert DeductionNote.NET_NEGATIVE_CLIPPED in Note_codes(r)


def test_pflege_for_a_child_counts_and_outsider_ignored() -> None:
    r = run(
        [item(Category.PFLEGE, "4000", "K1"), item(Category.PFLEGE, "999", "X")],
        kids=[kid()],
    )
    assert r.belastung_gross == D(4000)
    assert DeductionNote.PERSON_NOT_IN_RETURN in Note_codes(r)


def test_other_categories_and_irrelevant_ignored() -> None:
    r = run([item(Category.SPENDEN, "500"), item(Category.PFLEGE, "500", relevant=False)])
    assert r.belastung_gross == D(0)


def test_integrity_excluded_and_override_counts() -> None:
    bad = item(Category.BEHINDERUNG, "700", reason=AttentionReason.SUM_MISMATCH)
    forced = item(Category.BEHINDERUNG, "800", reason=AttentionReason.SUM_MISMATCH, override=True)
    r = run([bad, forced])
    assert r.belastung_gross == D(800)
    assert DeductionNote.EXCLUDED_ATTENTION in Note_codes(r)


def test_item_year_mismatch_and_bad_input() -> None:
    with pytest.raises(ValueError):
        run([item(Category.PFLEGE, "1", year=2024)])
    with pytest.raises(ValueError):
        run([], persons=[adult("A"), adult("A")])
    with pytest.raises(ValueError):
        run([], kids=[kid("K1"), kid("K1")])
    with pytest.raises(TypeError):
        agb(ctx(), [], [], [], 1.5, load_params(2025).agb)  # type: ignore[arg-type]


def test_notes_carry_no_amount_or_name() -> None:
    r = run(
        [item(Category.PFLEGE, "-5"), item(Category.PFLEGE, "5", "X")],
        kids=[kid(grade=50, months=0)],
    )
    for n in r.notes:
        assert set(vars(n) if hasattr(n, "__dict__") else ()) <= {"code", "item_id", "person_id"}


# --- properties ---

GRID = [
    D(x) for x in (-1, 0, 1, 1000, 15339, 15340, 15341, 30000, 51129, 51130, 51131, 80000, 250000)
]


def test_monotone_continuous_and_bounded(p) -> None:  # noqa: ANN001
    rng = random.Random(76)
    xs = sorted(GRID + [D(rng.randint(0, 20_000_000)) / 100 for _ in range(200)])
    for filing in (SINGLE, JOINT):
        for kids in (0, 1, 3):
            row = p.zumutbare_belastung.rates[
                "three_plus_children"
                if kids >= 3
                else "one_or_two_children"
                if kids
                else "no_children_joint"
                if filing is JOINT
                else "no_children_single"
            ]
            prev = D(0)
            for g in xs:
                z = zumutbare_belastung(g, filing, kids, p)
                assert z >= prev
                prev = z
                if g > 0:
                    assert row[0] * g - D("0.01") <= z <= row[2] * g + D("0.01")
            for limit in (D(15340), D(51130)):  # continuity: +-1 EUR moves it by less than 1 x rate
                for lo, hi in ((limit - 1, limit), (limit, limit + 1)):
                    diff = zumutbare_belastung(hi, filing, kids, p) - zumutbare_belastung(
                        lo, filing, kids, p
                    )
                    assert 0 <= diff <= max(row)


def test_more_children_never_increase_and_joint_le_single(p) -> None:  # noqa: ANN001
    for g in GRID:
        z0, z1, z3 = (zumutbare_belastung(g, SINGLE, k, p) for k in (0, 1, 3))
        assert z0 >= z1 >= z3
        assert zumutbare_belastung(g, JOINT, 0, p) <= z0


def test_agb_after_zb_monotone_bounded_and_order_free() -> None:
    rng = random.Random(7)
    prev = D(-1)
    for cost in range(0, 10001, 250):
        r = run([item(Category.KRANKHEITSKOSTEN, str(cost))])
        assert prev <= r.belastung_after_zb <= r.belastung_gross
        assert r.belastung_after_zb >= 0
        prev = r.belastung_after_zb
    items = [
        item(c, str(rng.randint(-50, 3000)))
        for c in (Category.PFLEGE, Category.KRANKHEITSKOSTEN, Category.BEHINDERUNG)
        for _ in range(4)
    ]
    base = run(items)
    for _ in range(10):
        rng.shuffle(items)
        assert run(items) == base
