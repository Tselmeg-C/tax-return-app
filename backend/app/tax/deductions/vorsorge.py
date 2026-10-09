"""Sonderausgaben-Vorsorge (#16, § 10 Abs. 1 Nr. 2, 3, 3a, Abs. 3, 4 EStG). Pure, Decimal only.

No Günstigerprüfung against the old law: § 10 Abs. 4a EStG is written for the Kalenderjahre 2013
to 2019 only, and the Vorsorgepauschale (§ 39b) is Lohnsteuer withholding, not Veranlagung.
Only the 4 % Krankengeld cut is rounded (down to cents); the law is silent on it.
v1 simplifications (`employed`): Höchstbetrag 1.900 EUR, 4 % cut, employer share assumed equal
to the employee share. Not applied: Abs. 3 Satz 3 Kürzung (#84), Abs. 4b Erstattungsüberhang (#84).
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal

from app.domain.enums import Category, DeductionNote
from app.tax.deductions.models import (
    AltersvorsorgeLine,
    ItemInput,
    KrankenLine,
    Note,
    ReturnContext,
    VorsorgeInput,
    VorsorgeResult,
)
from app.tax.deductions.select import finish_notes, net_by_key, select_items
from app.tax.deductions.sonderausgaben import round_down_cents
from app.tax.models import VorsorgeParams

_ZERO = Decimal(0)
_CATEGORIES = frozenset(
    {
        Category.VORSORGE_RV,
        Category.VORSORGE_RUERUP,
        Category.VORSORGE_KV_PV,
        Category.VORSORGE_SONSTIGE,
        Category.VORSORGE_RIESTER,
    }
)


def vorsorge(
    ctx: ReturnContext, persons: Iterable[VorsorgeInput], p: VorsorgeParams
) -> VorsorgeResult:
    people = list(persons)
    for person in people:
        if not isinstance(person, VorsorgeInput):
            raise TypeError("persons must be VorsorgeInput")
    ids = [person.person_id for person in people]
    if len(ids) != len(set(ids)) or set(ids) != ctx.person_ids:
        raise ValueError("persons must be exactly the persons of the return, once each")
    notes: list[Note] = []

    # A1 Altersvorsorge: the employer share is added before the cap (Nr. 2 Satz 6) and
    # subtracted after it (Abs. 3 Satz 5); 100 % since 2023 (Abs. 3 Satz 6).
    beitraege = arbeitgeber = _ZERO
    for person in people:
        if person.rv_employer is not None:
            ag = person.rv_employer
        elif person.employed:
            ag = person.rv_employee
            if ag > 0:
                notes.append(Note(DeductionNote.EMPLOYER_SHARE_ASSUMED, None, person.person_id))
        else:
            ag = _ZERO
        arbeitgeber += ag
        beitraege += person.rv_employee + ag + person.basisrente
    hoechstbetrag = p.altersvorsorge_hoechstbetrag * (2 if ctx.joint else 1)
    angesetzt = min(beitraege, hoechstbetrag)
    alter = AltersvorsorgeLine(
        beitraege, hoechstbetrag, angesetzt, arbeitgeber, max(_ZERO, angesetzt - arbeitgeber)
    )

    # A2 Kranken- und Pflegeversicherung: Nr. 3 is deducted in full even above the cap and then
    # excludes Nr. 3a (Abs. 4 Satz 4); below the cap Nr. 3a fills up to it.
    nr3 = nr3a = cap = _ZERO
    for person in people:
        kv = round_down_cents(person.kv * (1 - p.krankengeld_cut)) if person.employed else person.kv
        nr3 += kv + person.pv
        nr3a += person.sonstige
        cap += p.basis_cap_reduced if person.employed else p.basis_cap
    kranken = KrankenLine(nr3, nr3a, cap, max(nr3, min(nr3 + nr3a, cap)))

    return VorsorgeResult(alter, kranken, alter.abzug + kranken.abzug, finish_notes(notes))


def vorsorge_from_items(
    ctx: ReturnContext, items: Iterable[ItemInput], employed: frozenset[str]
) -> tuple[list[VorsorgeInput], tuple[Note, ...]]:
    """Sum the Vorsorge categories per person with #15's item rules (I1 to I7)."""
    notes: list[Note] = []
    rows: list[tuple[str | None, Category, Decimal]] = []
    for sel in select_items(ctx, items, _CATEGORIES, notes):
        item = sel.item
        if item.category is Category.VORSORGE_RIESTER:
            notes.append(Note(DeductionNote.RIESTER_NOT_SUPPORTED, item.id, sel.person_id))
            continue
        who = sel.person_id
        if who is None:  # household-level: the person decides cut and cap
            if ctx.joint:
                notes.append(Note(DeductionNote.PERSON_UNASSIGNED, item.id, None))
                continue
            who = ctx.taxpayer_id
        rows.append((who, item.category, item.deductible_amount))
    net = net_by_key(rows, notes)

    def amount(person: str, category: Category) -> Decimal:
        return net.get((person, category), _ZERO)

    result: list[VorsorgeInput] = []
    for person in sorted(ctx.person_ids):
        kv = amount(person, Category.VORSORGE_KV_PV)
        if kv > 0:
            notes.append(Note(DeductionNote.KV_PV_UNSPLIT, None, person))
        result.append(
            VorsorgeInput(
                person_id=person,
                employed=person in employed,
                rv_employee=amount(person, Category.VORSORGE_RV),
                rv_employer=None,
                basisrente=amount(person, Category.VORSORGE_RUERUP),
                kv=kv,
                pv=_ZERO,
                sonstige=amount(person, Category.VORSORGE_SONSTIGE),
            )
        )
    return result, finish_notes(notes)
