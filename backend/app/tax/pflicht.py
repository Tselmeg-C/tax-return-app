"""Pflichtveranlagung (§ 46 Abs. 2 EStG) and filing deadlines (§ 149 AO), #92. Pure.

Answers "must this return be filed?" for the persons of the return. Rules the data cannot
answer are listed in `not_checked`, never treated as "no". Per person: a rule that applies
to one person of a joint return makes the whole return mandatory. Persons outside the return
are ignored. `today` is injected; no Verspätungszuschlag is calculated.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.domain.enums import (
    FilingStatus,
    PflichtCheck,
    PflichtDeadlineKind,
    PflichtReason,
    PflichtState,
    Steuerklasse,
)
from app.tax.deductions.models import ReturnContext, check_decimal
from app.tax.models import TaxParams

# § 46 Abs. 2 Nr. 1 EStG. A fixed constant of the law, unchanged for VZ 2025 and 2026.
SIDE_INCOME_LIMIT = Decimal(410)

# Always unanswerable from our data (not_checked); the first is added per person when unknown.
_ALWAYS_NOT_CHECKED = (
    PflichtCheck.KV_REFUND,
    PflichtCheck.FREIBETRAG_39A,
    PflichtCheck.PARENT_ALLOCATION,
    PflichtCheck.SONSTIGER_BEZUG_39C,
    PflichtCheck.MARRIAGE_DISSOLVED_REMARRIED,
    PflichtCheck.FOREIGN_SPOUSE_OR_SPECIAL_STATUS,
    PflichtCheck.INCOME_LIMIT_56_ESTDV,
    PflichtCheck.FINANZAMT_REQUEST,
)


def _check_id(value: object, name: str) -> None:
    if not isinstance(value, str) or not value:
        raise TypeError(f"{name} must be a non-empty str")


@dataclass(frozen=True, slots=True)
class PflichtEmployment:
    person_id: str
    steuerklasse: Steuerklasse
    has_factor: bool
    # False only when the dates show the employments did not overlap (§ 46 Abs. 2 Nr. 2
    # says "nebeneinander"). True is the safe default: it can only add a (correct) reason.
    concurrent: bool = True

    def __post_init__(self) -> None:
        _check_id(self.person_id, "person_id")
        if not isinstance(self.steuerklasse, Steuerklasse):
            raise TypeError("steuerklasse must be a Steuerklasse member")
        if not isinstance(self.has_factor, bool) or not isinstance(self.concurrent, bool):
            raise TypeError("has_factor and concurrent must be bool")


@dataclass(frozen=True, slots=True)
class PflichtPerson:
    person_id: str
    lohnersatz: Decimal  # § 32b Abs. 1 benefits of the year, >= 0
    other_income: Decimal | None  # Einkünfte without Lohnsteuerabzug; None = not known

    def __post_init__(self) -> None:
        _check_id(self.person_id, "person_id")
        if check_decimal(self.lohnersatz, "lohnersatz") < 0:
            raise ValueError("lohnersatz must not be negative")
        if self.other_income is not None:
            check_decimal(self.other_income, "other_income")


@dataclass(frozen=True, slots=True)
class PflichtResult:
    state: PflichtState
    reasons: tuple[PflichtReason, ...]
    not_checked: tuple[PflichtCheck, ...]
    deadline: date
    deadline_kind: PflichtDeadlineKind
    overdue: bool


def pflichtveranlagung(
    ctx: ReturnContext,
    employments: Iterable[PflichtEmployment],
    persons: Iterable[PflichtPerson],
    today: date,
    p: TaxParams,
    *,
    advised: bool = False,
) -> PflichtResult:
    """`advised`: a Steuerberater prepares the return (§ 149 Abs. 3 AO, mandatory only)."""
    if not isinstance(today, date):
        raise TypeError("today must be a date")
    if ctx.year != p.year:
        raise ValueError("params are for another year than the return")
    employments = tuple(employments)
    in_return = [ctx.taxpayer_id]
    if ctx.filing is FilingStatus.JOINT and ctx.spouse_id is not None:
        in_return.append(ctx.spouse_id)
    emps = {pid: [e for e in employments if e.person_id == pid] for pid in in_return}
    by_id = {x.person_id: x for x in persons}

    reasons: set[PflichtReason] = set()
    checks: set[PflichtCheck] = set(_ALWAYS_NOT_CHECKED)
    for pid in in_return:
        person = by_id.get(pid)
        # Nr. 1: the two sums are tested each against 410 ("jeweils"), not added up.
        if person is not None and person.lohnersatz > SIDE_INCOME_LIMIT:
            reasons.add(PflichtReason.SIDE_INCOME_OVER_LIMIT)
        if person is None or person.other_income is None:
            checks.add(PflichtCheck.SIDE_INCOME_NOT_CHECKED)
        elif person.other_income > SIDE_INCOME_LIMIT:
            reasons.add(PflichtReason.SIDE_INCOME_OVER_LIMIT)
        # Nr. 2: Steuerklasse VI means a second, simultaneous employment.
        mine = emps[pid]
        if any(e.steuerklasse is Steuerklasse.VI for e in mine) or (
            len(mine) >= 2 and any(e.concurrent for e in mine)
        ):
            reasons.add(PflichtReason.MULTIPLE_EMPLOYERS)
    # Nr. 3a: joint, both with wages, one V / VI (or IV with Faktor).
    if len(in_return) == 2 and all(emps[pid] for pid in in_return):
        if any(
            e.steuerklasse in (Steuerklasse.V, Steuerklasse.VI)
            or (e.steuerklasse is Steuerklasse.IV and e.has_factor)
            for pid in in_return
            for e in emps[pid]
        ):
            reasons.add(PflichtReason.STEUERKLASSE_COMBINATION)

    mandatory = bool(reasons)
    if mandatory:
        kind = PflichtDeadlineKind.ADVISED if advised else PflichtDeadlineKind.SELF
        deadline = p.filing.deadline_advised if advised else p.filing.deadline_self
    else:
        # Nr. 8: Festsetzungsfrist of 4 years (§ 169 Abs. 2 Nr. 2), starting at the end of the
        # year the tax arose (§ 170 Abs. 1; Abs. 2 Nr. 1 needs a filing duty).
        kind = PflichtDeadlineKind.VOLUNTARY
        deadline = date(ctx.year + 4, 12, 31)
    return PflichtResult(
        state=PflichtState.MANDATORY if mandatory else PflichtState.VOLUNTARY,
        reasons=tuple(r for r in PflichtReason if r in reasons),
        not_checked=tuple(c for c in PflichtCheck if c in checks),
        deadline=deadline,
        deadline_kind=kind,
        overdue=today > deadline,
    )
