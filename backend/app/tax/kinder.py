"""Kinderfreibetrag, BEA and the Günstigerprüfung Kindergeld vs. Freibetrag (#87). Pure.

§ 32 Abs. 6 Sätze 1, 2, 5 EStG (Freibeträge per parent, doubled for joint parents of the child,
one twelfth less per month without the conditions), § 66 Abs. 1 (Kindergeld per month), § 31
Sätze 1, 4 (Freibeträge or Kindergeld; with Freibeträge the tarifliche ESt rises by the Kindergeld
claim, for not jointly assessed parents in the extent of the Kinderfreibetrag).
Ids only in results and notes: no date of birth, amount or name.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable  # Callable: the tariff is injected (#87 Decision 4)
from dataclasses import dataclass, field
from decimal import Decimal

from app.domain.enums import DeductionNote, FilingStatus
from app.tax.deductions.models import ChildInput, Note, check_decimal
from app.tax.models import KinderParams

_ZERO = Decimal(0)
_TWELVE = Decimal(12)
CHILD_AGE_LIMIT = 25  # § 32 Abs. 4 Satz 1 Nr. 2; same constant as `app.household.validation`

EstFn = Callable[[Decimal], Decimal]  # zvE -> tarifliche ESt in full euros


@dataclass(frozen=True, slots=True)
class KinderLine:
    person_id: str
    months: int
    freibetrag: Decimal  # Kinderfreibetrag + BEA of this child, Zwölftelt, not rounded
    kindergeld: Decimal  # Kindergeld claim in the extent of the Freibetrag share (Decision 2)
    benefit: Decimal  # tax saved by the Freibetrag at the point of the order the child was tested
    used_freibetrag: bool


@dataclass(frozen=True, slots=True)
class KinderResult:
    lines: tuple[KinderLine, ...]  # in the order tested: larger Freibetrag, dob, person_id
    zve_tariff: Decimal  # zvE after the Freibeträge that are used
    zve_bmg: Decimal  # zvE after all Freibeträge (Bemessungsgrundlage of Soli / KiSt)
    freibetrag_used: Decimal
    freibetrag_all: Decimal
    kindergeld_added: Decimal
    notes: tuple[Note, ...] = field(default=())


def kinder_amounts(child: ChildInput, p: KinderParams) -> tuple[Decimal, Decimal]:
    """`(freibetrag, kindergeld)` of one child; `half` = one parent's share, `full` = both."""
    shares = Decimal(1 if child.allowance_half else 2)
    months = Decimal(child.months)
    freibetrag = (p.kinderfreibetrag + p.bea_freibetrag) * shares * months / _TWELVE
    kindergeld = p.kindergeld_per_month * months * shares / 2
    return freibetrag, kindergeld


def _children(children: Iterable[ChildInput], year: int) -> list[ChildInput]:
    kids = list(children)
    if not all(isinstance(c, ChildInput) for c in kids):
        raise TypeError("children must be ChildInput")
    ids = [c.person_id for c in kids]
    if len(ids) != len(set(ids)):
        raise ValueError("two children with the same person_id")
    for c in kids:
        if c.months > 0 and c.dob.year > year:
            raise ValueError("a child born after the year cannot count in it")
    return kids


def _notes(kids: list[ChildInput], year: int) -> tuple[Note, ...]:
    notes = []
    for c in kids:
        if c.months > 0 and c.dob.year + CHILD_AGE_LIMIT < year:
            code = (
                DeductionNote.CHILD_OVER_25_DISABLED
                if c.disability_grade
                else DeductionNote.CHILD_AGE_REVIEW
            )
            notes.append(Note(code, person_id=c.person_id))
    return tuple(notes)


def guenstigerpruefung(
    zve: Decimal,
    children: Iterable[ChildInput],
    filing: FilingStatus,
    est_fn: EstFn,
    p: KinderParams,
    *,
    year: int,
) -> KinderResult:
    """Child by child (larger Freibetrag first): the Freibetrag only if it saves strictly more
    than the child's Kindergeld; at a tie Kindergeld stays (it fully effects the Freistellung).

    Provisional reading (params `kinder.source`): the order for several children is not in the
    law text. `filing` is validated only: the Freibetrag share is `ChildInput.allowance_half`.
    """
    if not isinstance(filing, FilingStatus):
        raise TypeError("filing must be a FilingStatus member")
    if not callable(est_fn):
        raise TypeError("est_fn must be callable")
    z_in = max(check_decimal(zve, "zve"), _ZERO)  # losses count as 0
    kids = _children(children, year)
    counted = sorted(
        (
            (c, *kinder_amounts(c, p))
            for c in kids
            if c.months > 0  # months = 0 is ignored
        ),
        key=lambda t: (-t[1], t[0].dob, t[0].person_id),
    )
    z_tariff = z_in
    used_total = kg_added = all_total = _ZERO
    lines = []
    for child, freibetrag, kindergeld in counted:
        all_total += freibetrag
        after = max(z_tariff - freibetrag, _ZERO)
        benefit = est_fn(z_tariff) - est_fn(after)
        used = benefit > kindergeld
        if used:
            z_tariff, used_total, kg_added = after, used_total + freibetrag, kg_added + kindergeld
        lines.append(
            KinderLine(child.person_id, child.months, freibetrag, kindergeld, benefit, used)
        )
    return KinderResult(
        tuple(lines),
        z_tariff,
        max(z_in - all_total, _ZERO),
        used_total,
        all_total,
        kg_added,
        _notes(kids, year),
    )
