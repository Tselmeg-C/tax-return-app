"""Item selection rules I1 to I7 (shared with #76 / #77). Pure.

I1 year mismatch -> ValueError. I2 not relevant -> ignored. I5 only the categories asked for.
I3 person: taxpayer / spouse -> that person; `None`, a child of `child_ids` -> household-level;
anyone else -> ignored with `unknown_note`. I4 overridden items always count; unreviewed items
count with `included_unreviewed`, except the three amount-integrity reasons (`excluded_attention`).
I6 net per (person, category) is `net_by_key`. I7 holds because nothing depends on the order and
`finish_notes` sorts.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from app.domain.enums import AttentionReason, Category, DeductionNote
from app.tax.deductions.models import ItemInput, Note, ReturnContext

EXCLUDED_REASONS: frozenset[AttentionReason] = frozenset(
    {
        AttentionReason.IMPLAUSIBLE_AMOUNT,
        AttentionReason.SUM_MISMATCH,
        AttentionReason.SIGN_MISMATCH,
    }
)


@dataclass(frozen=True, slots=True)
class Selected:
    item: ItemInput
    person_id: str | None  # taxpayer / spouse / a child of `child_ids`, else None (household)
    adult: bool  # person_id is the taxpayer or the spouse


def select_items(
    ctx: ReturnContext,
    items: Iterable[ItemInput],
    categories: frozenset[Category],
    notes: list[Note],
    *,
    child_ids: frozenset[str] = frozenset(),
    unknown_note: DeductionNote = DeductionNote.PERSON_NOT_IN_RETURN,
) -> list[Selected]:
    items = list(items)
    for item in items:
        if not isinstance(item, ItemInput):
            raise TypeError("items must be ItemInput")
        if item.year != ctx.year:
            raise ValueError("item year differs from the return year")  # I1
    selected: list[Selected] = []
    for item in items:
        if not item.is_relevant or item.category not in categories:  # I2, I5
            continue
        pid = item.person_id
        if pid is None:
            who, adult = None, False
        elif pid in ctx.person_ids:
            who, adult = pid, True
        elif pid in child_ids:
            who, adult = pid, False
        else:
            notes.append(Note(unknown_note, item.id, None))  # I3
            continue
        if not item.overridden_by_user and item.attention_reason is not None:  # I4
            if item.attention_reason in EXCLUDED_REASONS:
                notes.append(Note(DeductionNote.EXCLUDED_ATTENTION, item.id, who))
                continue
            notes.append(Note(DeductionNote.INCLUDED_UNREVIEWED, item.id, who))
        selected.append(Selected(item, who, adult))
    return selected


def net_by_key(
    rows: Iterable[tuple[str | None, Category, Decimal]], notes: list[Note]
) -> dict[tuple[str | None, Category], Decimal]:
    """I6: signed sum per (person, category); a negative net becomes 0 with a note."""
    totals: dict[tuple[str | None, Category], Decimal] = {}
    for person, category, amount in rows:
        totals[(person, category)] = totals.get((person, category), Decimal(0)) + amount
    for (person, category), total in list(totals.items()):
        if total < 0:
            totals[(person, category)] = Decimal(0)
            notes.append(Note(DeductionNote.NET_NEGATIVE_CLIPPED, None, person))
    return totals


def finish_notes(notes: Iterable[Note]) -> tuple[Note, ...]:
    """I7: a deterministic order that does not depend on the order of the items."""
    return tuple(
        sorted(set(notes), key=lambda n: (n.code.value, n.item_id or "", n.person_id or ""))
    )
