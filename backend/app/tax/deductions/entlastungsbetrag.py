"""Entlastungsbetrag für Alleinerziehende (#81): § 24b EStG. Pure, Decimal only.

E1 only `single` filers (Abs. 3 Satz 1: no Splitting); a joint return gets 0.
E2 a child counts when it is `in_household` and has `months > 0` (Freibetrag / Kindergeld, Abs. 1).
E3 a Haushaltsgemeinschaft with another adult (`shares_household`) gives 0 + a note (Abs. 3).
E4 first child 4.260, each further 240 (Abs. 2); Abs. 4 cuts by 1/12 per full month without the
conditions. Reading (OPEN): the child with the most months carries the base amount, every further
child adds `240 x months / 12` (the base is then reduced exactly by the months in which no child
belongs to the household). All amounts are whole euros, so nothing is rounded.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import ROUND_DOWN, Decimal

from app.domain.enums import DeductionNote, FilingStatus
from app.tax.deductions.models import ChildInput, EntlastungsbetragResult, Note, ReturnContext
from app.tax.models import EntlastungsbetragParams

_CENT = Decimal("0.01")
_ZERO = Decimal("0.00")


def entlastungsbetrag(
    ctx: ReturnContext,
    children: Sequence[ChildInput],
    shares_household: bool,
    p: EntlastungsbetragParams,
) -> EntlastungsbetragResult:
    if not isinstance(shares_household, bool):
        raise TypeError("shares_household must be bool")
    seen: set[str] = set()
    for child in children:
        if not isinstance(child, ChildInput):
            raise TypeError("children must be ChildInput")
        if child.person_id in seen or child.person_id in ctx.person_ids:
            raise ValueError("a child is given twice or is also an adult of the return")
        seen.add(child.person_id)
    if ctx.joint or ctx.filing is not FilingStatus.SINGLE:
        return EntlastungsbetragResult(_ZERO, ())
    counted = sorted(
        (c for c in children if c.in_household and c.months > 0),
        key=lambda c: (-c.months, c.person_id),
    )
    if not counted:
        return EntlastungsbetragResult(_ZERO, ())
    if shares_household:
        note = Note(DeductionNote.ENTLASTUNG_HOUSEHOLD_SHARED, None, ctx.taxpayer_id)
        return EntlastungsbetragResult(_ZERO, (), (note,))
    first, *further = counted
    total = p.base * first.months / 12 + sum(
        (p.per_further_child * c.months / 12 for c in further), Decimal(0)
    )
    return EntlastungsbetragResult(
        total.quantize(_CENT, rounding=ROUND_DOWN), tuple(c.person_id for c in counted)
    )
