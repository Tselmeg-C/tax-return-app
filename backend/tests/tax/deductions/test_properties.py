"""Stdlib-only property tests (deterministic grids and seeded `random.Random`), #15."""

from __future__ import annotations

import random
from decimal import ROUND_FLOOR, Decimal

from app.domain.enums import Category as C
from app.tax.deductions import (
    entfernungspauschale,
    homeoffice_pauschale,
    sonderausgaben,
    werbungskosten,
)
from tests.tax.deductions.conftest import child, ctx, emp, item

D = Decimal
CENT = D("0.01")


def money(rng: random.Random, hi: int = 20000) -> Decimal:
    return D(rng.randint(0, hi * 100)) / 100


def floor_c(v: Decimal) -> Decimal:
    return v.quantize(CENT, rounding=ROUND_FLOOR)


def test_entfernungspauschale(wp) -> None:  # noqa: ANN001
    r2 = wp.entfernungspauschale.rate_from_km_21
    prev = D(0)
    for km in range(0, 201):
        v = entfernungspauschale(km, 100, wp)
        assert v >= prev
        prev = v
        if wp.entfernungspauschale.rate_km_1_to_20 == D("0.38"):  # 2026: flat
            assert v == D("0.38") * km * 100
    assert entfernungspauschale(21, 7, wp) - entfernungspauschale(20, 7, wp) == r2 * 7
    for days in range(0, 367, 3):
        assert entfernungspauschale(33, days, wp) <= entfernungspauschale(33, days + 3, wp)
    rng = random.Random(1)
    for _ in range(100):
        a, b = (rng.randint(0, 80), rng.randint(0, 150)), (rng.randint(0, 80), rng.randint(0, 150))
        assert entfernungspauschale(a[0], a[1], wp) + entfernungspauschale(b[0], b[1], wp) == sum(
            (entfernungspauschale(k, d, wp) for k, d in (a, b)), D(0)
        )


def test_homeoffice(wp) -> None:  # noqa: ANN001
    prev = D(0)
    for days in range(0, 367):
        v = homeoffice_pauschale(days, wp)
        assert D(0) <= v <= 1260 and v >= prev
        prev = v


def test_werbungskosten_bounds_and_monotonic(wp) -> None:  # noqa: ANN001
    rng = random.Random(2)
    cats = [C.WK_ARBEITSMITTEL, C.WK_FORTBILDUNG, C.WK_BERUFSVERBAND, C.WK_BEWERBUNG]
    for _ in range(150):
        items = [item(rng.choice(cats), str(money(rng, 2500))) for _ in range(rng.randint(0, 4))]
        e = [emp(km=rng.randint(0, 60), office=rng.randint(0, 220), ho=rng.randint(0, 100))]
        base = werbungskosten(ctx(), items, e, wp).by_person["A"]
        assert base.applied >= 1230 and base.applied >= base.actual
        more = werbungskosten(ctx(), [*items, item(rng.choice(cats), "10.00")], e, wp)
        assert more.by_person["A"].applied >= base.applied
        e2 = [emp(km=e[0].commute_km, office=(e[0].office_days or 0) + 1, ho=e[0].homeoffice_days)]
        assert werbungskosten(ctx(), items, e2, wp).by_person["A"].applied >= base.applied
        # an item for the other person does not change this person's amount
        joint = werbungskosten(ctx(joint=True), items, [*e, emp("B")], wp)
        other = werbungskosten(
            ctx(joint=True), [*items, item(C.WK_ARBEITSMITTEL, "777.00", "B")], [*e, emp("B")], wp
        )
        assert joint.by_person["A"] == other.by_person["A"]


def test_per_child_caps(sp) -> None:  # noqa: ANN001
    rng = random.Random(3)
    for cat, rule, key in (
        (C.KINDERBETREUUNG, sp.kinderbetreuung, "kinderbetreuung_by_child"),
        (C.SCHULGELD, sp.schulgeld, "schulgeld_by_child"),
    ):
        prev = D(0)
        for cents in range(0, 2_500_000, 9_973):
            costs = D(cents) / 100
            r = sonderausgaben(ctx(), [item(cat, str(costs), "K1")], [child()], D(0), D(0), sp)
            line = getattr(r, key)["K1"]
            assert 0 <= line.deductible <= rule.max_per_child
            assert line.deductible <= rule.share * costs and line.deductible >= prev
            prev = line.deductible
            if rule.share * costs < rule.max_per_child:
                assert line.deductible == floor_c(rule.share * costs)
        for _ in range(50):  # splitting never gives less than lumping, up to 1 cent of rounding
            a, b = money(rng, 12000), money(rng, 12000)
            kids = [child("K1"), child("K2")]
            split = sonderausgaben(
                ctx(), [item(cat, str(a), "K1"), item(cat, str(b), "K2")], kids, D(0), D(0), sp
            )
            lump = sonderausgaben(ctx(), [item(cat, str(a + b), "K1")], kids, D(0), D(0), sp)
            assert (
                sum(c.deductible for c in getattr(split, key).values())
                >= getattr(lump, key)["K1"].deductible - CENT
            )


def test_spenden(sp) -> None:  # noqa: ANN001
    rng = random.Random(4)
    for _ in range(200):
        net, gde = money(rng, 30000), D(rng.randint(-5000, 150000))
        r = sonderausgaben(ctx(), [item(C.SPENDEN, str(net))], [], gde, D(0), sp)
        assert r.spenden_deductible <= min(net, D("0.20") * max(gde, D(0)))
        assert r.spenden_deductible + r.spenden_excess == net
        bigger_gde = sonderausgaben(ctx(), [item(C.SPENDEN, str(net))], [], gde + 100, D(0), sp)
        assert bigger_gde.spenden_deductible >= r.spenden_deductible
        bigger_net = sonderausgaben(ctx(), [item(C.SPENDEN, str(net + 1))], [], gde, D(0), sp)
        assert bigger_net.spenden_deductible >= r.spenden_deductible


def test_sonderausgaben_pauschbetrag(sp) -> None:  # noqa: ANN001
    rng = random.Random(5)
    for _ in range(100):
        items = [item(C.KIRCHENSTEUER, str(money(rng, 200)))]
        single = sonderausgaben(ctx(), items, [], D(1000), D(0), sp)
        joint = sonderausgaben(ctx(joint=True), items, [], D(1000), D(0), sp)
        assert single.applied >= 36 and single.applied >= single.actual_total
        assert joint.applied >= 72 and joint.applied >= single.applied
