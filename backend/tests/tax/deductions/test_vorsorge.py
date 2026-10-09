"""Vorsorge (#16): DTO checks, item mapping, notes. Reference cases: tests/tax/reference."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.enums import AttentionReason, Category, DeductionNote
from app.tax.deductions import VorsorgeInput, vorsorge, vorsorge_from_items
from app.tax.models import VorsorgeParams
from app.tax_params import load_params

from .conftest import ctx, item

D = Decimal
V = Category


def vin(pid: str = "A", employed: bool = True, **kw: Decimal | int | None) -> VorsorgeInput:
    base: dict[str, object] = dict(
        rv_employee=D(0), rv_employer=None, basisrente=D(0), kv=D(0), pv=D(0), sonstige=D(0)
    )
    base.update(kw)
    return VorsorgeInput(pid, employed, **base)  # type: ignore[arg-type]


@pytest.fixture
def vp(year: int) -> VorsorgeParams:
    return load_params(year).vorsorge


def test_dto_rejects_float_nan_negative() -> None:
    with pytest.raises(TypeError):
        vin(kv=0.5)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        vin(kv=D("NaN"))
    with pytest.raises(ValueError):
        vin(sonstige=D("Infinity"))
    with pytest.raises(ValueError):
        vin(rv_employer=D(-1))
    with pytest.raises(ValueError):
        vin(pv=D("-0.01"))
    with pytest.raises(TypeError):
        VorsorgeInput("A", 1, D(0), None, D(0), D(0), D(0), D(0))  # type: ignore[arg-type]


def test_persons_must_match_the_return(vp: VorsorgeParams) -> None:
    with pytest.raises(ValueError):
        vorsorge(ctx(), [], vp)
    with pytest.raises(ValueError):
        vorsorge(ctx(), [vin("A"), vin("B")], vp)
    with pytest.raises(ValueError):
        vorsorge(ctx(joint=True), [vin("A")], vp)
    with pytest.raises(ValueError):
        vorsorge(ctx(joint=True), [vin("A"), vin("A")], vp)
    with pytest.raises(TypeError):
        vorsorge(ctx(), [object()], vp)  # type: ignore[list-item]


def test_given_employer_share_has_no_note(vp: VorsorgeParams) -> None:
    r = vorsorge(ctx(), [vin(rv_employee=D(1000), rv_employer=D(1000))], vp)
    assert r.notes == ()
    assert r.altersvorsorge.abzug == D(1000)


def test_assumed_share_only_when_employed_and_amount(vp: VorsorgeParams) -> None:
    r = vorsorge(ctx(), [vin(rv_employee=D(1000))], vp)
    assert [n.code for n in r.notes] == [DeductionNote.EMPLOYER_SHARE_ASSUMED]
    assert vorsorge(ctx(), [vin(employed=False, rv_employee=D(1000))], vp).notes == ()
    assert vorsorge(ctx(), [vin()], vp).notes == ()
    assert vorsorge(ctx(), [vin(employed=False, rv_employee=D(1000))], vp).altersvorsorge.abzug == (
        D(1000)
    )


def test_not_employed_no_cut_cap_2800(vp: VorsorgeParams) -> None:
    r = vorsorge(ctx(), [vin(employed=False, kv=D(1000), sonstige=D(5000))], vp)
    assert (r.kranken.nr3, r.kranken.cap, r.kranken.abzug) == (D(1000), D(2800), D(2800))


def test_joint_hoechstbetrag_doubles(vp: VorsorgeParams) -> None:
    r = vorsorge(
        ctx(joint=True),
        [vin("A", employed=False, basisrente=D(100000)), vin("B", employed=False)],
        vp,
    )
    assert r.altersvorsorge.hoechstbetrag == 2 * vp.altersvorsorge_hoechstbetrag


def test_notes_carry_codes_and_ids_only(vp: VorsorgeParams) -> None:
    r = vorsorge(ctx(), [vin(rv_employee=D("1234.56"))], vp)
    for note in r.notes:
        assert "1234" not in repr(note)


# --- vorsorge_from_items -------------------------------------------------------------------


def run(items, joint=False, employed=frozenset({"A", "B"})):  # noqa: ANN001, ANN201
    return vorsorge_from_items(ctx(joint=joint), items, employed)


def codes(notes) -> list[DeductionNote]:  # noqa: ANN001
    return [n.code for n in notes]


def test_items_map_to_fields() -> None:
    persons, notes = run(
        [
            item(V.VORSORGE_RV, "100"),
            item(V.VORSORGE_RUERUP, "200"),
            item(V.VORSORGE_KV_PV, "300"),
            item(V.VORSORGE_SONSTIGE, "400"),
            item(V.WK_ARBEITSMITTEL, "999"),
        ]
    )
    (a,) = persons
    assert (a.rv_employee, a.basisrente, a.kv, a.pv, a.sonstige) == (
        D(100),
        D(200),
        D(300),
        0,
        D(400),
    )
    assert a.rv_employer is None and a.employed
    assert codes(notes) == [DeductionNote.KV_PV_UNSPLIT]


def test_refund_nets_and_clips() -> None:
    (a,), notes = run([item(V.VORSORGE_KV_PV, "800"), item(V.VORSORGE_KV_PV, "-200")])
    assert a.kv == D(600)
    (a,), notes = run([item(V.VORSORGE_KV_PV, "800"), item(V.VORSORGE_KV_PV, "-1200")])
    assert a.kv == D(0)
    assert codes(notes) == [DeductionNote.NET_NEGATIVE_CLIPPED]


def test_riester_skipped_with_note() -> None:
    (a,), notes = run([item(V.VORSORGE_RIESTER, "500", id="r1")])
    assert a == vin(rv_employee=D(0))
    assert codes(notes) == [DeductionNote.RIESTER_NOT_SUPPORTED]
    assert notes[0].item_id == "r1"


def test_household_item_single_goes_to_taxpayer_joint_excluded() -> None:
    (a,), _ = run([item(V.VORSORGE_RV, "100", None)])
    assert a.rv_employee == D(100)
    persons, notes = run([item(V.VORSORGE_RV, "100", None, id="h")], joint=True)
    assert all(p.rv_employee == 0 for p in persons)
    assert [(n.code, n.item_id) for n in notes] == [(DeductionNote.PERSON_UNASSIGNED, "h")]


def test_person_outside_return() -> None:
    (a,), notes = run([item(V.VORSORGE_RV, "100", "X", id="x")])
    assert a.rv_employee == 0
    assert codes(notes) == [DeductionNote.PERSON_NOT_IN_RETURN]


def test_attention_and_override() -> None:
    (a,), notes = run(
        [
            item(V.VORSORGE_RV, "100", reason=AttentionReason.IMPLAUSIBLE_AMOUNT),
            item(V.VORSORGE_RV, "10", reason=AttentionReason.LOW_CONFIDENCE),
            item(V.VORSORGE_RV, "1", reason=AttentionReason.LOW_CONFIDENCE, override=True),
        ]
    )
    assert a.rv_employee == D(11)
    assert sorted(codes(notes)) == sorted(
        [DeductionNote.EXCLUDED_ATTENTION, DeductionNote.INCLUDED_UNREVIEWED]
    )


def test_every_person_gets_an_input_and_employed_flag() -> None:
    persons, _ = run([item(V.VORSORGE_RV, "100", "B")], joint=True, employed=frozenset({"B"}))
    assert [(p.person_id, p.employed, p.rv_employee) for p in persons] == [
        ("A", False, D(0)),
        ("B", True, D(100)),
    ]


def test_item_order_does_not_matter() -> None:
    items = [
        item(V.VORSORGE_KV_PV, "800", id="1"),
        item(V.VORSORGE_KV_PV, "-200", id="2"),
        item(V.VORSORGE_RIESTER, "5", id="3"),
        item(V.VORSORGE_RV, "7", "X", id="4"),
    ]
    assert run(items) == run(list(reversed(items)))


def test_wrong_year_raises() -> None:
    with pytest.raises(ValueError):
        run([item(V.VORSORGE_RV, "1", year=2024)])


def test_total_adds_both_parts(vp: VorsorgeParams) -> None:
    persons, _ = run([item(V.VORSORGE_RV, "6000"), item(V.VORSORGE_KV_PV, "4000")])
    r = vorsorge(ctx(), persons, vp)
    assert (r.altersvorsorge.abzug, r.kranken.abzug, r.total) == (D(6000), D(3840), D(9840))
