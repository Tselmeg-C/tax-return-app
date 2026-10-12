"""Außergewöhnliche Belastungen (#76): §§ 33, 33b EStG. Pure, Decimal only.

A1 stufenweise zumutbare Belastung (BFH 19.01.2017 VI R 75/14), rounded half-up to cents.
A2 Krankheits-, Pflege- and behinderungsbedingte Kosten minus ZB, at least 0.
A3 Behinderten-Pauschbetrag per person; it is not reduced by the ZB (§ 33b Abs. 1 replaces
the typical costs). A child's amount goes to the parents (§ 33b Abs. 5): full in a joint
return, `allowance_half` halves it in a single one.

`persons` carries the Merkzeichen of the taxpayer, the spouse and the children (a missing
person has none); a child's grade comes from its `ChildInput`.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from decimal import ROUND_HALF_UP, Decimal

from app.domain.enums import Category, DeductionNote, FilingStatus
from app.tax.deductions.models import (
    AgbResult,
    ChildInput,
    ItemInput,
    Note,
    PersonInput,
    ReturnContext,
    check_count,
    check_decimal,
)
from app.tax.deductions.select import finish_notes, net_by_key, select_items
from app.tax.models import AgbParams

_ZERO = Decimal(0)
_CENT = Decimal("0.01")
_CATEGORIES = frozenset({Category.KRANKHEITSKOSTEN, Category.PFLEGE, Category.BEHINDERUNG})


def _cents(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def zumutbare_belastung(
    gde: Decimal, filing: FilingStatus, child_count: int, p: AgbParams
) -> Decimal:
    """A1. `gde <= 0` gives 0. The rate of a bracket applies to the part of the GdE inside it."""
    gde = check_decimal(gde, "gde")
    check_count(child_count, "child_count")
    if not isinstance(filing, FilingStatus):
        raise TypeError("filing must be a FilingStatus member")
    z = p.zumutbare_belastung
    if child_count >= 3:
        row = "three_plus_children"
    elif child_count >= 1:
        row = "one_or_two_children"
    else:
        row = "no_children_joint" if filing is FilingStatus.JOINT else "no_children_single"
    r1, r2, r3 = z.rates[row]
    b1, b2 = z.bracket1_upper, z.bracket2_upper
    part1 = max(min(gde, b1), _ZERO)
    part2 = max(min(gde, b2) - b1, _ZERO)
    part3 = max(gde - b2, _ZERO)
    return _cents(r1 * part1 + r2 * part2 + r3 * part3)


def behinderten_pauschbetrag(grade: int | None, hilflos_blind: bool, p: AgbParams) -> Decimal:
    """A3, full year (no Zwölftelung). The Merkzeichen amount replaces the grade amount."""
    if not isinstance(hilflos_blind, bool):
        raise TypeError("hilflos_blind must be bool")
    if hilflos_blind:
        return p.hilflos_blind
    if grade is None:
        return _ZERO
    check_count(grade, "grade")
    # Below 20 no Pauschbetrag (§ 33b Abs. 2); between steps the lower step (Satz 2 "mindestens").
    steps = [g for g in range(20, 101, 10) if g <= grade]
    return p.behinderten_pauschbetrag[str(steps[-1])] if steps else _ZERO


def agb(
    ctx: ReturnContext,
    items: Iterable[ItemInput],
    persons: Sequence[PersonInput],
    children: Sequence[ChildInput],
    gde: Decimal,
    p: AgbParams,
) -> AgbResult:
    gde = check_decimal(gde, "gde")
    people: dict[str, PersonInput] = {}
    for person in persons:
        if not isinstance(person, PersonInput):
            raise TypeError("persons must be PersonInput")
        if person.person_id in people:
            raise ValueError("a person is given twice")
        people[person.person_id] = person
    kids: dict[str, ChildInput] = {}
    for child in children:
        if not isinstance(child, ChildInput):
            raise TypeError("children must be ChildInput")
        if child.person_id in kids or child.person_id in ctx.person_ids:
            raise ValueError("a child is given twice or is also an adult of the return")
        kids[child.person_id] = child
    notes: list[Note] = []

    child_count = sum(1 for c in kids.values() if c.months > 0)  # § 33 Abs. 3 Satz 2
    zb = zumutbare_belastung(gde, ctx.filing, child_count, p)

    selected = select_items(ctx, items, _CATEGORIES, notes, child_ids=frozenset(kids))
    net = net_by_key(
        [(s.person_id, s.item.category, s.item.deductible_amount) for s in selected], notes
    )
    gross = sum(net.values(), _ZERO)
    after_zb = max(_ZERO, gross - zb)

    by_person: dict[str, Decimal] = {}
    for pid in (ctx.taxpayer_id, ctx.spouse_id):
        if pid is None:
            continue
        who = people.get(pid)
        by_person[pid] = (
            behinderten_pauschbetrag(who.disability_grade, who.merkzeichen_h_bl_tbl, p)
            if who
            else _ZERO
        )
    for kid in kids.values():
        who = people.get(kid.person_id)
        amount = behinderten_pauschbetrag(
            kid.disability_grade, bool(who and who.merkzeichen_h_bl_tbl), p
        )
        if amount == 0:
            continue
        if kid.months == 0:
            notes.append(Note(DeductionNote.CHILD_NOT_ELIGIBLE, None, kid.person_id))
            continue
        if not ctx.joint and kid.allowance_half:
            amount = _cents(amount / 2)
        by_person[kid.person_id] = amount
    pausch = sum(by_person.values(), _ZERO)
    return AgbResult(
        zumutbare_belastung=zb,
        belastung_gross=gross,
        belastung_after_zb=after_zb,
        pauschbetrag_by_person=by_person,
        pauschbetrag_total=pausch,
        total=after_zb + pausch,
        notes=finish_notes(notes),
    )
