"""§ 35a (#77): reference cases, item rules, properties. Deterministic."""

from __future__ import annotations

import random
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.domain.enums import AttentionReason, Category, DeductionNote, PaymentMethod
from app.tax.deductions import ItemInput, haushaltsnahe
from app.tax_params import load_params, supported_years

from .deductions.conftest import ctx, item

D = Decimal
HERE = Path(__file__).parent / "reference"
HW, DL = Category.HANDWERKERLEISTUNG, Category.HAUSHALTSNAHE_DIENSTLEISTUNG


def it(category: Category, labour: str | None, amount: str | None = None, **kw: Any) -> ItemInput:
    """Item with a labour share; `deductible_amount` defaults to it."""
    base = item(category, amount or labour or "0", person=None, **{"year": 2025, **kw})
    return replace(base, labour_share_35a=None if labour is None else D(labour))


def _cases() -> list[Any]:
    out = []
    for year in supported_years():
        path = HERE / f"haushaltsnahe_{year}.yaml"
        assert path.is_file(), f"reference file missing: {path.name}"
        for case in yaml.safe_load(path.read_text(encoding="utf-8")):
            assert case["source"] and case["arithmetic"] and case["expected"]
            out.append(pytest.param(year, case, id=f"{year}-{case['id']}"))
    return out


@pytest.mark.parametrize(("year", "case"), _cases())
def test_reference(year: int, case: dict[str, Any]) -> None:
    i = case["input"]
    items = [it(HW, a, year=year) for a in i["hw"]] + [it(DL, a, year=year) for a in i["dl"]]
    items += [it(HW, a, year=year, pay=PaymentMethod.CASH) for a in i.get("cash_hw", [])]
    r = haushaltsnahe(ctx(year), items, load_params(year).haushaltsnahe)
    assert r.total == D(case["expected"])
    assert r.total.as_tuple().exponent == -2
    if "cash_hw" in i:
        assert [n.code for n in r.notes] == [DeductionNote.CASH_EXCLUDED]


@pytest.fixture(params=[2025, 2026])
def p(request: pytest.FixtureRequest):  # noqa: ANN201
    return load_params(request.param).haushaltsnahe


def run(items: list[ItemInput], p: Any):  # noqa: ANN202
    return haushaltsnahe(ctx(), items, p)


def codes(r: Any) -> list[DeductionNote]:
    return [n.code for n in r.notes]


def test_cap_flags_and_lines(p: Any) -> None:
    r = run([it(HW, "6000"), it(DL, "3000")], p)
    assert (r.handwerker.cap, r.handwerker.cap_reached) == (D(1200), True)
    assert (r.dienstleistung.cap, r.dienstleistung.cap_reached) == (D(4000), False)
    assert (r.handwerker.labour_sum, r.dienstleistung.ermaessigung) == (D(6000), D(600))


def test_labour_share_missing(p: Any) -> None:
    r = run([it(HW, None, "500")], p)
    assert r.total == 0 and codes(r) == [DeductionNote.LABOUR_SHARE_MISSING]


def test_min_of_amount_and_labour(p: Any) -> None:
    assert run([it(HW, "180", "100")], p).handwerker.labour_sum == D(100)
    assert run([it(HW, "100", "180")], p).handwerker.labour_sum == D(100)


def test_not_relevant_ignored_silently(p: Any) -> None:
    r = run([it(HW, "500", relevant=False)], p)
    assert r.total == 0 and r.notes == ()


def test_duplicate_item_amount_zero(p: Any) -> None:
    r = run([it(HW, "500", "0")], p)
    assert r.total == 0 and r.notes == ()


@pytest.mark.parametrize("pay", [PaymentMethod.UNKNOWN, PaymentMethod.OTHER])
def test_payment_unverified_counts(p: Any, pay: PaymentMethod) -> None:
    r = run([it(HW, "1000", pay=pay)], p)
    assert r.total == D(200) and codes(r) == [DeductionNote.PAYMENT_UNVERIFIED]


@pytest.mark.parametrize(
    "pay",
    [
        PaymentMethod.BANK_TRANSFER,
        PaymentMethod.DIRECT_DEBIT,
        PaymentMethod.CARD,
        PaymentMethod.PAYPAL,
    ],
)
def test_traceable_payments_count_silently(p: Any, pay: PaymentMethod) -> None:
    r = run([it(HW, "1000", pay=pay)], p)
    assert r.total == D(200) and r.notes == ()


def test_attention_rules(p: Any) -> None:
    ok = it(HW, "1000", reason=AttentionReason.PAYMENT_METHOD_UNKNOWN_35A)
    r = run([ok], p)
    assert r.total == D(200) and codes(r) == [DeductionNote.INCLUDED_UNREVIEWED]
    bad = it(HW, "1000", reason=AttentionReason.SUM_MISMATCH)
    r = run([bad], p)
    assert r.total == 0 and codes(r) == [DeductionNote.EXCLUDED_ATTENTION]
    r = run([replace(bad, overridden_by_user=True)], p)
    assert r.total == D(200) and r.notes == ()


def test_cash_excluded_even_when_overridden(p: Any) -> None:
    r = run([it(HW, "6000", pay=PaymentMethod.CASH, override=True)], p)
    assert r.total == 0 and codes(r) == [DeductionNote.CASH_EXCLUDED]


def test_other_categories_ignored(p: Any) -> None:
    assert run([item(Category.SPENDEN, "500", person=None)], p).notes == ()


def test_negative_net_clipped(p: Any) -> None:
    r = run([it(HW, "100"), it(HW, "-300")], p)
    assert r.total == 0 and codes(r) == [DeductionNote.NET_NEGATIVE_CLIPPED]


def test_wrong_year_raises(p: Any) -> None:
    with pytest.raises(ValueError, match="year"):
        haushaltsnahe(ctx(2026), [it(HW, "100")], p)


def test_float_raises(p: Any) -> None:
    with pytest.raises(TypeError):
        run([replace(it(HW, "100"), labour_share_35a=1.5)], p)  # type: ignore[arg-type]


def test_notes_carry_no_amounts(p: Any) -> None:
    r = run([it(HW, None, "777.77"), it(HW, "5", pay=PaymentMethod.CASH)], p)
    assert all(set(vars_(n)) <= {"code", "item_id", "person_id"} for n in r.notes)


def vars_(n: Any) -> dict[str, Any]:
    return {k: getattr(n, k) for k in n.__slots__}


def test_properties(p: Any) -> None:
    rng = random.Random(77)
    for _ in range(200):
        hw = [D(rng.randrange(0, 4_000_000)) / 100 for _ in range(rng.randrange(0, 4))]
        dl = [D(rng.randrange(0, 4_000_000)) / 100 for _ in range(rng.randrange(0, 4))]
        items = [it(HW, str(a)) for a in hw] + [it(DL, str(a)) for a in dl]
        r = run(items, p)
        for line, amounts in ((r.handwerker, hw), (r.dienstleistung, dl)):
            lab = sum(amounts, D(0))
            assert line.ermaessigung <= line.cap and line.ermaessigung <= D("0.2") * lab
            if D("0.2") * lab <= line.cap:
                assert abs(line.ermaessigung - D("0.2") * lab) < D("0.01")
        assert r.total == r.handwerker.ermaessigung + r.dienstleistung.ermaessigung
        # permutation, cash item and more labour
        shuffled = items[:]
        rng.shuffle(shuffled)
        assert run(shuffled, p) == r
        cash = it(HW, "999", pay=PaymentMethod.CASH)
        r2 = run([*items, cash], p)
        assert (r2.total, r2.handwerker, r2.dienstleistung) == (
            r.total,
            r.handwerker,
            r.dienstleistung,
        )
        assert run([*items, it(HW, "10"), it(DL, "10")], p).total >= r.total
        # sum of two items equals one item of the sum
        if len(hw) >= 2:
            merged = [it(HW, str(hw[0] + hw[1])), *[it(HW, str(a)) for a in hw[2:]]]
            assert run(merged + [it(DL, str(a)) for a in dl], p).total == r.total
