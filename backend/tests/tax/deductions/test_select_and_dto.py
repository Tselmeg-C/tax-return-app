"""Item rules I1 to I7, DTO validation and privacy of the notes (#15)."""

from __future__ import annotations

import random
from datetime import date
from decimal import Decimal

import pytest

from app.domain.enums import AttentionReason as R
from app.domain.enums import Category as C
from app.domain.enums import DeductionNote as N
from app.domain.enums import FilingStatus
from app.tax.deductions import (
    ChildInput,
    EmploymentInput,
    ItemInput,
    ReturnContext,
    entfernungspauschale,
    homeoffice_pauschale,
    sonderausgaben,
    werbungskosten,
)
from tests.tax.deductions.conftest import child, ctx, emp, item

D = Decimal


def wk(items, wp, joint=False, employments=None, year=2025):  # noqa: ANN001, ANN201
    return werbungskosten(
        ctx(year, joint),
        items,
        [emp("A"), emp("B")][: 2 if joint else 1] if employments is None else employments,
        wp,
    )


def codes(result) -> list[str]:  # noqa: ANN001
    return sorted(n.code.value for n in result.notes)


def test_i1_year_mismatch(wp) -> None:  # noqa: ANN001
    with pytest.raises(ValueError, match="year"):
        wk([item(C.WK_ARBEITSMITTEL, "1", year=2024)], wp)


def test_i2_not_relevant_is_ignored_silently(wp) -> None:  # noqa: ANN001
    r = wk([item(C.WK_ARBEITSMITTEL, "5000", relevant=False)], wp)
    assert r.by_person["A"].applied == D(1230) and r.notes == ()


def test_i3_person_outside_return(wp) -> None:  # noqa: ANN001
    r = wk([item(C.WK_ARBEITSMITTEL, "5000", "X", id="out")], wp)
    assert r.by_person["A"].applied == D(1230)
    assert [(n.code, n.item_id) for n in r.notes] == [(N.PERSON_NOT_IN_RETURN, "out")]


def test_i3_household_level_single_vs_joint(wp) -> None:  # noqa: ANN001
    single = wk([item(C.WK_ARBEITSMITTEL, "2000", None)], wp)
    assert single.by_person["A"].applied == D(2000) and single.notes == ()
    joint = wk([item(C.WK_ARBEITSMITTEL, "2000", None)], wp, joint=True)
    assert codes(joint) == ["person_unassigned"]
    assert joint.total_applied == D(2460)


@pytest.mark.parametrize("reason", [R.IMPLAUSIBLE_AMOUNT, R.SUM_MISMATCH, R.SIGN_MISMATCH])
def test_i4_integrity_reasons_are_excluded(wp, reason: R) -> None:  # noqa: ANN001
    r = wk([item(C.WK_ARBEITSMITTEL, "5000", reason=reason)], wp)
    assert r.by_person["A"].applied == D(1230) and codes(r) == ["excluded_attention"]


@pytest.mark.parametrize(
    "reason",
    [r for r in R if r not in (R.IMPLAUSIBLE_AMOUNT, R.SUM_MISMATCH, R.SIGN_MISMATCH)],
)
def test_i4_other_reasons_are_counted_and_noted(wp, reason: R) -> None:  # noqa: ANN001
    r = wk([item(C.WK_ARBEITSMITTEL, "5000", reason=reason)], wp)
    assert r.by_person["A"].applied == D(5000) and codes(r) == ["included_unreviewed"]


@pytest.mark.parametrize("reason", list(R))
def test_i4_override_counts_without_note(wp, reason: R) -> None:  # noqa: ANN001
    r = wk([item(C.WK_ARBEITSMITTEL, "5000", override=True, reason=reason)], wp)
    assert r.by_person["A"].applied == D(5000) and r.notes == ()


def test_i5_other_groups_are_skipped_silently(wp) -> None:  # noqa: ANN001
    r = wk([item(C.VORSORGE_RV, "9999"), item(C.V_AFA, "9999"), item(C.IRRELEVANT, "9")], wp)
    assert r.by_person["A"].applied == D(1230) and r.notes == ()


def test_i6_net_per_person_and_category(wp) -> None:  # noqa: ANN001
    items = [item(C.WK_ARBEITSMITTEL, "300"), item(C.WK_ARBEITSMITTEL, "-350"),
             item(C.WK_FORTBILDUNG, "2000")]  # fmt: skip
    r = wk(items, wp)
    assert r.by_person["A"].actual == D(2000) and codes(r) == ["net_negative_clipped"]


def test_i7_order_does_not_matter(wp) -> None:  # noqa: ANN001
    rng = random.Random(7)
    cats = [C.WK_ARBEITSMITTEL, C.WK_FORTBILDUNG, C.WK_BERUFSVERBAND, C.WK_HOMEOFFICE]
    items = [
        item(
            rng.choice(cats),
            str(rng.randint(-300, 900)),
            rng.choice(["A", "B", None, "X"]),
            reason=rng.choice([None, R.LOW_CONFIDENCE, R.SUM_MISMATCH]),
        )
        for _ in range(12)
    ]
    base = wk(items, wp, joint=True)
    for _ in range(20):
        rng.shuffle(items)
        assert wk(items, wp, joint=True) == base


def test_zero_item_and_irrelevant_item_change_nothing(wp) -> None:  # noqa: ANN001
    items = [item(C.WK_ARBEITSMITTEL, "1500"), item(C.WK_BERUFSVERBAND, "50")]
    base = wk(items, wp)
    with_zero = wk([*items, item(C.WK_FORTBILDUNG, "0")], wp)
    assert with_zero.total_applied == base.total_applied and with_zero.notes == base.notes
    assert wk([*items, item(C.WK_FORTBILDUNG, "800", relevant=False)], wp) == base


def test_w_person_without_employment(wp) -> None:  # noqa: ANN001
    r = wk([item(C.WK_ARBEITSMITTEL, "500", "B")], wp, joint=True, employments=[emp("A")])
    assert set(r.by_person) == {"A"} and codes(r) == ["no_employment"]


def test_employment_of_person_outside_return_is_ignored(wp) -> None:  # noqa: ANN001
    r = wk([], wp, employments=[emp("A"), emp("X", 50, 200)])
    assert set(r.by_person) == {"A"}


def test_person_without_any_employment_is_absent(wp) -> None:  # noqa: ANN001
    assert wk([], wp, employments=[]).by_person == {}


def test_sa_attention_and_override(sp) -> None:  # noqa: ANN001
    c = ctx()
    bad = item(C.SPENDEN, "100", reason=R.IMPLAUSIBLE_AMOUNT)
    ok = item(C.KIRCHENSTEUER, "500", reason=R.LOW_CONFIDENCE)
    r = sonderausgaben(c, [bad, ok], [], D(50000), D(0), sp)
    assert r.spenden_net == 0 and r.kirchensteuer == 500
    assert {n.code for n in r.notes} == {N.EXCLUDED_ATTENTION, N.INCLUDED_UNREVIEWED}
    kept = item(C.SPENDEN, "100", override=True, reason=R.IMPLAUSIBLE_AMOUNT)
    assert sonderausgaben(c, [kept], [], D(50000), D(0), sp).spenden_net == 100


def test_sa_person_outside_return_and_child_level(sp) -> None:  # noqa: ANN001
    items = [item(C.SPENDEN, "100", "X"), item(C.SPENDEN, "50", "K1")]
    r = sonderausgaben(ctx(), items, [child()], D(50000), D(0), sp)
    assert r.spenden_net == 50 and codes(r) == ["person_not_in_return"]


def test_sa_spouse_items_count_in_joint(sp) -> None:  # noqa: ANN001
    items = [item(C.KIRCHENSTEUER, "400", "A"), item(C.KIRCHENSTEUER, "300", "B")]
    r = sonderausgaben(ctx(joint=True), items, [], D(50000), D(0), sp)
    assert r.kirchensteuer == 700 and r.pauschbetrag == 72


def test_notes_carry_codes_and_ids_only(wp, sp) -> None:  # noqa: ANN001
    r = wk([item(C.WK_ARBEITSMITTEL, "-1234.56", id="item-1")], wp)
    s = sonderausgaben(ctx(), [item(C.SPENDEN, "987.65", "X", id="item-2")], [], D(0), D(0), sp)
    for n in (*r.notes, *s.notes):
        assert set(vars(n) if hasattr(n, "__dict__") else ("code", "item_id", "person_id")) <= {
            "code", "item_id", "person_id"}  # fmt: skip
        assert "1234" not in repr(n) and "987" not in repr(n)


# --- DTO / argument validation -------------------------------------------------------------


def test_float_rejected(wp, sp) -> None:  # noqa: ANN001
    with pytest.raises(TypeError):
        ItemInput("i", "A", C.SPENDEN, 2025, 1.5, None, item(C.SPENDEN, "1").payment_method,  # type: ignore[arg-type]
                  True, False, None)  # fmt: skip
    with pytest.raises(TypeError):
        sonderausgaben(ctx(), [], [], 1.5, D(0), sp)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        sonderausgaben(ctx(), [], [], D(1), 0.5, sp)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        entfernungspauschale(1.5, 10, wp)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        homeoffice_pauschale(2.0, wp)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        EmploymentInput("A", 10.0, 1, 1)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", [D("NaN"), D("Infinity"), D("-Infinity")])
def test_non_finite_decimal_rejected(sp, bad: Decimal) -> None:  # noqa: ANN001
    with pytest.raises(ValueError, match="finite"):
        item(C.SPENDEN, bad)
    with pytest.raises(ValueError, match="finite"):
        sonderausgaben(ctx(), [], [], bad, D(0), sp)


def test_negative_counts_and_bad_dtos() -> None:
    with pytest.raises(ValueError):
        EmploymentInput("A", -1, 0, 0)
    with pytest.raises(ValueError):
        EmploymentInput("A", 10, -1, 0)
    with pytest.raises(ValueError):
        EmploymentInput("A", 10, 0, -1)
    with pytest.raises(ValueError):
        ChildInput("K", date(2020, 1, 1), None, -1, False, True)
    with pytest.raises(ValueError):
        ChildInput("K", date(2020, 1, 1), None, 13, False, True)
    with pytest.raises(TypeError):
        ReturnContext(2025, "single", "A")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ReturnContext(2025, FilingStatus.JOINT, "A", "A")


def test_negative_arguments(wp, sp) -> None:  # noqa: ANN001
    with pytest.raises(ValueError):
        entfernungspauschale(10, -1, wp)
    with pytest.raises(ValueError):
        homeoffice_pauschale(-1, wp)
    with pytest.raises(ValueError, match="negative"):
        sonderausgaben(ctx(), [], [], D(1), D(-1), sp)
    with pytest.raises(ValueError, match="twice"):
        sonderausgaben(ctx(), [], [child(), child()], D(1), D(0), sp)


def test_item_year_vs_context_in_sonderausgaben(sp) -> None:  # noqa: ANN001
    with pytest.raises(ValueError, match="year"):
        sonderausgaben(ctx(2025), [item(C.SPENDEN, "1", year=2026)], [], D(1), D(0), sp)


def test_feb_29_child_does_not_crash(sp) -> None:  # noqa: ANN001
    items = [item(C.KINDERBETREUUNG, "1000", "K1")]
    r = sonderausgaben(ctx(), items, [child(dob=date(2012, 2, 29))], D(1), D(0), sp)
    assert r.kinderbetreuung_by_child["K1"].deductible == D(800)
