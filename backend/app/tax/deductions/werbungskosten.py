"""Werbungskosten per person (#15 W1 to W6): Pauschbetrag, Entfernungspauschale, Homeoffice, items.

Assumptions (marked in the issue, effect at most a few euros): every employment's days count
even on the same calendar day (R 9.10 LStR reading unverified); the commute is by own car, so
the 4 500 EUR cap of § 9 Abs. 1 Satz 3 Nr. 4 Satz 2 is not applied (`commute_assumes_car`); the
Pauschbetrag is not limited to the wage (§ 9a Satz 2, #17 does that).
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal

from app.domain.enums import CATEGORY_GROUP, Category, CategoryGroup, DeductionNote
from app.tax.deductions.models import (
    EmploymentInput,
    ItemInput,
    Note,
    PersonWk,
    ReturnContext,
    WerbungskostenResult,
    check_count,
)
from app.tax.deductions.select import finish_notes, net_by_key, select_items
from app.tax.models import WerbungskostenParams

_ZERO = Decimal(0)
WK_CATEGORIES: frozenset[Category] = frozenset(
    c for c, g in CATEGORY_GROUP.items() if g is CategoryGroup.WERBUNGSKOSTEN
)
# Summed per category (W4). Homeoffice items are covered by W2, Arbeitszimmer is W3.
_ITEM_CATEGORIES: tuple[Category, ...] = tuple(
    c
    for c in Category
    if c in WK_CATEGORIES and c not in (Category.WK_HOMEOFFICE, Category.WK_ARBEITSZIMMER)
)


def entfernungspauschale(km: int | None, days: int, p: WerbungskostenParams) -> Decimal:
    """§ 9 Abs. 1 Satz 3 Nr. 4: per working day, one-way full km; 4 500 EUR cap not applied."""
    check_count(days, "days")
    if km is None:
        return _ZERO
    check_count(km, "km")
    e = p.entfernungspauschale
    per_day = min(km, 20) * e.rate_km_1_to_20 + max(km - 20, 0) * e.rate_from_km_21
    return per_day * days


def homeoffice_pauschale(days: int, p: WerbungskostenParams) -> Decimal:
    """§ 4 Abs. 5 Satz 1 Nr. 6c: per day, capped per person and year."""
    check_count(days, "days")
    return min(p.homeoffice.per_day * days, p.homeoffice.max_amount)


def werbungskosten(
    ctx: ReturnContext,
    items: Iterable[ItemInput],
    employments: Iterable[EmploymentInput],
    p: WerbungskostenParams,
) -> WerbungskostenResult:
    notes: list[Note] = []
    jobs: dict[str, list[EmploymentInput]] = {}
    for emp in employments:
        if not isinstance(emp, EmploymentInput):
            raise TypeError("employments must be EmploymentInput")
        if emp.person_id in ctx.person_ids:
            jobs.setdefault(emp.person_id, []).append(emp)

    rows: list[tuple[str | None, Category, Decimal]] = []
    for sel in select_items(ctx, items, WK_CATEGORIES, notes):
        person = sel.person_id
        if not sel.adult:  # W5: household-level
            if ctx.joint:
                notes.append(Note(DeductionNote.PERSON_UNASSIGNED, sel.item.id, None))
                continue
            person = ctx.taxpayer_id
        if person not in jobs:
            notes.append(Note(DeductionNote.NO_EMPLOYMENT, sel.item.id, person))
            continue
        if sel.item.category is Category.WK_HOMEOFFICE:  # covered by W2
            notes.append(Note(DeductionNote.COVERED_BY_PAUSCHALE, sel.item.id, person))
            continue
        rows.append((person, sel.item.category, sel.item.deductible_amount))
    net = net_by_key(rows, notes)

    by_person: dict[str, PersonWk] = {}
    for person in sorted(jobs):
        emps = jobs[person]
        entf = sum((entfernungspauschale(e.commute_km, e.office_days, p) for e in emps), _ZERO)
        if entf > 0:
            notes.append(Note(DeductionNote.COMMUTE_ASSUMES_CAR, None, person))
        room = net.get((person, Category.WK_ARBEITSZIMMER), _ZERO)
        if room > 0:  # W3: replaces W2
            home = _ZERO
            notes.append(Note(DeductionNote.ARBEITSZIMMER_REPLACES_HOMEOFFICE, None, person))
        else:
            room = _ZERO
            home = homeoffice_pauschale(sum(e.homeoffice_days for e in emps), p)
        by_cat = {c: net[(person, c)] for c in _ITEM_CATEGORIES if (person, c) in net}
        if by_cat.get(Category.WK_FAHRTKOSTEN, _ZERO) > 0 and entf > 0:
            notes.append(Note(DeductionNote.FAHRTKOSTEN_WITH_ENTFERNUNGSPAUSCHALE, None, person))
        if by_cat.get(Category.WK_DOPPELTE_HAUSHALTSFUEHRUNG, _ZERO) > 0:
            notes.append(Note(DeductionNote.DHF_UNCHECKED, None, person))
        actual = entf + home + room + sum(by_cat.values(), _ZERO)  # W6
        beside = (
            by_cat.get(Category.WK_BERUFSVERBAND, _ZERO)
            if p.union_dues_beside_pauschbetrag
            else _ZERO
        )
        base = actual - beside
        pausch = p.arbeitnehmer_pauschbetrag
        by_person[person] = PersonWk(
            entfernungspauschale=entf,
            homeoffice_pauschale=home,
            arbeitszimmer=room,
            items_by_category=by_cat,
            actual=actual,
            pauschbetrag=pausch,
            applied=max(base, pausch) + beside,
            used_pauschbetrag=base <= pausch,
            beside_pauschbetrag=beside,
        )
    total = sum((w.applied for w in by_person.values()), _ZERO)
    return WerbungskostenResult(by_person, total, finish_notes(notes))
