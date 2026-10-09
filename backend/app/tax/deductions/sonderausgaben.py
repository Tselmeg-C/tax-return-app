"""Sonderausgaben without Vorsorge (#15 S1 to S5). Pure, Decimal only.

A percentage of an amount in cents is rounded down to cents (Decision 5). Assumptions: the
child's 14th birthday is `dob` + `age_limit` years (29 February -> 1 March); a child turning
14 during the year counts in full (items carry no period) with `child_age_review`.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from decimal import ROUND_FLOOR, Decimal

from app.domain.enums import Category, DeductionNote, PaymentMethod
from app.tax.deductions.models import (
    UNASSIGNED,
    ChildInput,
    ChildLine,
    ItemInput,
    Note,
    ReturnContext,
    SonderausgabenResult,
    check_decimal,
)
from app.tax.deductions.select import finish_notes, net_by_key, select_items
from app.tax.models import SonderausgabenParams

_ZERO = Decimal(0)
_CENT = Decimal("0.01")
_CATEGORIES = frozenset(
    {Category.KIRCHENSTEUER, Category.SPENDEN, Category.KINDERBETREUUNG, Category.SCHULGELD}
)


def round_down_cents(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_FLOOR)


def _birthday(dob: date, years: int) -> date:
    try:
        return dob.replace(year=dob.year + years)
    except ValueError:  # 29 February
        return date(dob.year + years, 3, 1)


def _lines(
    rows: list[tuple[str, Decimal]], share: Decimal, cap: Decimal, notes: list[Note]
) -> dict[str, ChildLine]:
    """Net per child (I6), then `share` of it rounded down to cents, at most `cap`."""
    net = net_by_key([(k or None, Category.KINDERBETREUUNG, a) for k, a in rows], notes)
    result: dict[str, ChildLine] = {}
    for (key, _), costs in sorted(net.items(), key=lambda kv: kv[0][0] or ""):
        part = round_down_cents(share * costs)
        result[key or UNASSIGNED] = ChildLine(costs, min(part, cap), part >= cap)
    return result


def sonderausgaben(
    ctx: ReturnContext,
    items: Iterable[ItemInput],
    children: Iterable[ChildInput],
    gde: Decimal,
    kirchensteuer_official: Decimal,
    p: SonderausgabenParams,
) -> SonderausgabenResult:
    gde = check_decimal(gde, "gde")
    official = check_decimal(kirchensteuer_official, "kirchensteuer_official")
    if official < 0:
        raise ValueError("kirchensteuer_official must not be negative")
    kids: dict[str, ChildInput] = {}
    for child in children:
        if not isinstance(child, ChildInput):
            raise TypeError("children must be ChildInput")
        if child.person_id in kids:
            raise ValueError("a child is given twice")
        kids[child.person_id] = child
    kid_ids = frozenset(kids)
    notes: list[Note] = []

    # S1, S2: summed over the return; net per (person, category) as in I6.
    all_items = list(items)
    plain = select_items(
        ctx,
        all_items,
        frozenset({Category.KIRCHENSTEUER, Category.SPENDEN}),
        notes,
        child_ids=kid_ids,
    )
    net = net_by_key(
        [(s.person_id, s.item.category, s.item.deductible_amount) for s in plain], notes
    )
    kist = official + sum((v for (_, c), v in net.items() if c is Category.KIRCHENSTEUER), _ZERO)
    spenden_net = sum((v for (_, c), v in net.items() if c is Category.SPENDEN), _ZERO)
    limit = round_down_cents(p.spenden_max_share_of_gde * max(gde, _ZERO))
    spenden = min(spenden_net, limit)
    excess = spenden_net - spenden
    if excess > 0:
        notes.append(Note(DeductionNote.SPENDEN_OVER_LIMIT))

    # S3, S4: per kid; an item of no known kid goes to one bucket.
    care_rows: list[tuple[str, Decimal]] = []
    school_rows: list[tuple[str, Decimal]] = []
    for sel in select_items(
        ctx,
        all_items,
        frozenset({Category.KINDERBETREUUNG, Category.SCHULGELD}),
        notes,
        child_ids=kid_ids,
        unknown_note=DeductionNote.CHILD_NOT_ELIGIBLE,
    ):
        item = sel.item
        kid = kids.get(sel.person_id) if sel.person_id and not sel.adult else None
        if kid is None:
            notes.append(Note(DeductionNote.CHILD_UNASSIGNED, item.id, None))
        key = kid.person_id if kid else UNASSIGNED
        if item.category is Category.SCHULGELD:
            if kid is not None and kid.months == 0:
                notes.append(Note(DeductionNote.CHILD_NOT_ELIGIBLE, item.id, key))
                continue
            school_rows.append((key, item.deductible_amount))
            continue
        if kid is not None and not _care_eligible(kid, ctx.year, p, item, notes):
            continue
        if item.payment_method is PaymentMethod.CASH:
            notes.append(Note(DeductionNote.CASH_EXCLUDED, item.id, key or None))
            continue
        if item.payment_method in (PaymentMethod.UNKNOWN, PaymentMethod.OTHER):
            notes.append(Note(DeductionNote.PAYMENT_UNVERIFIED, item.id, key or None))
        care_rows.append((key, item.deductible_amount))
    care = _lines(care_rows, p.kinderbetreuung.share, p.kinderbetreuung.max_per_child, notes)
    school = _lines(school_rows, p.schulgeld.share, p.schulgeld.max_per_child, notes)

    actual = (
        kist
        + spenden
        + sum((c.deductible for c in care.values()), _ZERO)
        + sum((c.deductible for c in school.values()), _ZERO)
    )
    pausch = p.pauschbetrag_joint if ctx.joint else p.pauschbetrag_single
    return SonderausgabenResult(
        kirchensteuer=kist,
        spenden_net=spenden_net,
        spenden_limit=limit,
        spenden_deductible=spenden,
        spenden_excess=excess,
        kinderbetreuung_by_child=care,
        schulgeld_by_child=school,
        actual_total=actual,
        pauschbetrag=pausch,
        applied=max(actual, pausch),
        used_pauschbetrag=actual <= pausch,
        notes=finish_notes(notes),
    )


def _care_eligible(
    child: ChildInput, year: int, p: SonderausgabenParams, item: ItemInput, notes: list[Note]
) -> bool:
    """§ 10 Abs. 1 Nr. 5: child in the household and under 14 for at least one day of the year."""
    pid = child.person_id
    if not child.in_household:
        notes.append(Note(DeductionNote.CHILD_NOT_ELIGIBLE, item.id, pid))
        return False
    turns = _birthday(child.dob, p.kinderbetreuung.age_limit)
    if turns > date(year, 12, 31):
        return True
    if turns <= date(year, 1, 1) and child.disability_grade is None:
        notes.append(Note(DeductionNote.CHILD_NOT_ELIGIBLE, item.id, pid))
        return False
    notes.append(Note(DeductionNote.CHILD_AGE_REVIEW, item.id, pid))
    return True
