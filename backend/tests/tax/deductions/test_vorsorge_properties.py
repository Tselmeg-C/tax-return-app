"""Property tests for vorsorge (#16): deterministic grids and `random.Random(seed)` only."""

from __future__ import annotations

import random
from decimal import Decimal

from app.tax.deductions import VorsorgeInput, vorsorge
from app.tax_params import load_params

from .conftest import ctx

D = Decimal
VP = load_params(2025).vorsorge
H = VP.altersvorsorge_hoechstbetrag


def amt(rng: random.Random, top: int) -> Decimal:
    return D(rng.randint(0, top * 100)) / 100


def person(rng: random.Random, pid: str, **over: object) -> VorsorgeInput:
    kw: dict[str, object] = dict(
        employed=rng.random() < 0.5,
        rv_employee=amt(rng, 12000),
        rv_employer=None if rng.random() < 0.5 else amt(rng, 12000),
        basisrente=amt(rng, 40000),
        kv=amt(rng, 6000),
        pv=amt(rng, 1500),
        sonstige=amt(rng, 4000),
    )
    kw.update(over)
    return VorsorgeInput(pid, **kw)  # type: ignore[arg-type]


def cases(n: int = 400):  # noqa: ANN201
    rng = random.Random(16)
    for i in range(n):
        joint = i % 2 == 1
        ps = [person(rng, "A")] + ([person(rng, "B")] if joint else [])
        yield joint, ps


def test_bounds_and_total() -> None:
    for joint, ps in cases():
        r = vorsorge(ctx(joint=joint), ps, VP)
        a, k = r.altersvorsorge, r.kranken
        assert 0 <= a.abzug <= H * (2 if joint else 1)
        assert a.abzug + a.arbeitgeberanteil <= H * (2 if joint else 1)
        assert k.abzug >= k.nr3
        assert k.abzug <= max(k.nr3, k.cap)
        assert r.total == a.abzug + k.abzug


def test_monotone_in_each_amount() -> None:
    rng = random.Random(7)
    for field in ("rv_employee", "basisrente", "kv", "pv", "sonstige"):
        for _ in range(100):
            # employer share given: with the v1 assumption (share = rv_employee) the abzug is
            # NOT monotone above the Höchstbetrag, because the assumed share grows with it.
            p = person(rng, "A", rv_employer=amt(rng, 12000))
            q = VorsorgeInput(
                **{
                    **{f: getattr(p, f) for f in p.__slots__},
                    field: getattr(p, field) + D(rng.randint(1, 5000)),
                }
            )  # type: ignore[arg-type]
            r1, r2 = vorsorge(ctx(), [p], VP), vorsorge(ctx(), [q], VP)
            assert r2.altersvorsorge.abzug >= r1.altersvorsorge.abzug
            assert r2.kranken.abzug >= r1.kranken.abzug


def test_euro_more_basisrente_above_cap_changes_nothing() -> None:
    p = VorsorgeInput("A", False, D(0), None, H, D(0), D(0), D(0))
    q = VorsorgeInput("A", False, D(0), None, H + 1, D(0), D(0), D(0))
    assert vorsorge(ctx(), [p], VP).total == vorsorge(ctx(), [q], VP).total == H


def test_sonstige_never_lifts_above_cap_and_is_out_when_nr3_reaches_cap() -> None:
    rng = random.Random(3)
    for joint, ps in cases(200):
        r = vorsorge(ctx(joint=joint), ps, VP)
        if r.kranken.nr3 >= r.kranken.cap:
            zero = [
                VorsorgeInput(**{**{f: getattr(p, f) for f in p.__slots__}, "sonstige": D(0)})
                for p in ps
            ]  # type: ignore[arg-type]
            assert vorsorge(ctx(joint=joint), zero, VP).kranken.abzug == r.kranken.abzug
        else:
            assert r.kranken.abzug <= r.kranken.cap
    assert rng  # grid seeded for reproducibility


def test_person_order_does_not_matter() -> None:
    for joint, ps in cases(100):
        if joint:
            assert vorsorge(ctx(joint=True), ps, VP) == vorsorge(ctx(joint=True), ps[::-1], VP)


def test_all_zero_person_changes_only_the_cap() -> None:
    rng = random.Random(5)
    for _ in range(100):
        a = person(rng, "A")
        zero_b = VorsorgeInput("B", a.employed, D(0), D(0), D(0), D(0), D(0), D(0))
        single = vorsorge(ctx(), [a], VP)
        joint = vorsorge(ctx(joint=True), [a, zero_b], VP)
        # the Höchstbetrag doubles too (Abs. 3 Satz 2): equal only below the single Höchstbetrag
        assert joint.altersvorsorge.hoechstbetrag == 2 * single.altersvorsorge.hoechstbetrag
        if single.altersvorsorge.beitraege <= H:
            assert joint.altersvorsorge.abzug == single.altersvorsorge.abzug
        assert joint.kranken.nr3 == single.kranken.nr3
        assert joint.kranken.cap == single.kranken.cap + (
            VP.basis_cap_reduced if a.employed else VP.basis_cap
        )


def test_joint_can_be_lower_than_two_single_calculations() -> None:
    a = VorsorgeInput("A", True, D(0), None, D(0), D(3125), D(0), D(0))
    b = VorsorgeInput("B", False, D(0), None, D(0), D(0), D(0), D(3000))
    joint = vorsorge(ctx(joint=True), [a, b], VP).kranken.abzug
    ctx_a, ctx_b = ctx(), ctx()
    separate = (
        vorsorge(ctx_a, [a], VP).kranken.abzug
        + vorsorge(
            ctx_b, [VorsorgeInput("A", False, D(0), None, D(0), D(0), D(0), D(3000))], VP
        ).kranken.abzug
    )
    assert joint == D(4700) < separate == D(5800)
