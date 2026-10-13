"""Kirchensteuer of a joint return with exactly one church member (glaubensverschiedene Ehe, #53).

Pure. The member's share of the Bemessungsgrundlage is `member_income / total_income` (the
Summe der Einkünfte ratio of the Länder rules; the caller approximates it with the Gesamtbetrag
per person). Per Land (params `church_tax.kirchgeld`): `compare_states` charge the higher of the
member-share KiSt and the besonderes Kirchgeld of the Stufe of the zvE; `no_kirchgeld_states` (BY)
only the member share; any other Land is charged the member share with a note.
Never reuse the tariff here: the caller passes the Bemessungsgrundlage computed with `income_tax`.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from enum import StrEnum

from app.domain.enums import DeductionNote
from app.tax.deductions.models import Note, check_decimal
from app.tax.models import ChurchTaxParams

_ZERO = Decimal(0)
_CENT = Decimal("0.01")


class KistMethod(StrEnum):
    FULL = "full"  # both spouses members (or single): rate x the whole Bemessungsgrundlage
    MEMBER_SHARE = "member_share"
    KIRCHGELD = "kirchgeld"


@dataclass(frozen=True, slots=True)
class MemberShare:
    """One member in a joint return: Summe der Einkünfte of the member and of both spouses."""

    member_income: Decimal
    total_income: Decimal


@dataclass(frozen=True, slots=True)
class ChurchResult:
    kist: Decimal
    method: KistMethod
    notes: tuple[Note, ...] = ()


def kirchgeld(zve_bmg: Decimal, state: str, p: ChurchTaxParams) -> Decimal | None:
    """Annual besonderes Kirchgeld, 0 below the first Stufe; `None` if the Land has no table."""
    if state not in p.rate_by_state:
        raise ValueError(f"unknown state code: {state!r}")
    if state not in p.kirchgeld.compare_states:
        return None
    base = check_decimal(zve_bmg, "zve_bmg")
    amount = _ZERO
    for stufe in p.kirchgeld.stufen:
        if base >= stufe.lower:
            amount = stufe.amount
    return amount


def church_tax_joint(
    bmg: Decimal, zve_bmg: Decimal, share: MemberShare, state: str, p: ChurchTaxParams
) -> ChurchResult:
    """KiSt in cents (rounded down) of a joint return with one member."""
    base = check_decimal(bmg, "bmg")
    income = check_decimal(share.member_income, "member_income")
    total = check_decimal(share.total_income, "total_income")
    if state not in p.rate_by_state:
        raise ValueError(f"unknown state code: {state!r}")
    # Losses of the non-member count as 0 in the ratio; no positive income at all: no share.
    income, total = max(income, _ZERO), max(total, _ZERO)
    total = max(total, income)
    fraction = _ZERO if total == 0 else income / total
    notes: list[Note] = []
    if _ZERO < income < total:
        notes.append(Note(DeductionNote.KIST_SHARE_ESTIMATED))
    kist = (max(base, _ZERO) * p.rate_by_state[state] * fraction).quantize(_CENT, ROUND_DOWN)
    table = kirchgeld(zve_bmg, state, p)
    if table is None:
        if state not in p.kirchgeld.no_kirchgeld_states:
            notes.append(Note(DeductionNote.KIRCHGELD_NOT_MODELLED))
        return ChurchResult(kist, KistMethod.MEMBER_SHARE, tuple(notes))
    if table > kist:
        return ChurchResult(table.quantize(_CENT), KistMethod.KIRCHGELD, tuple(notes))
    return ChurchResult(kist, KistMethod.MEMBER_SHARE, tuple(notes))
