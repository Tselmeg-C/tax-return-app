"""DTOs (the contract with #17) and result types of the deduction engine. Pure, frozen.

Ids are `str`. Nothing here holds a vendor, a name, a filename or a date of birth beyond the
child's `dob`, which is used for the age rule only and never copied into a result or a note.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.domain.enums import (
    AttentionReason,
    Category,
    DeductionNote,
    FilingStatus,
    PaymentMethod,
)

UNASSIGNED = ""  # key of the bucket of items that belong to no (known) child


def check_decimal(value: object, name: str) -> Decimal:
    """`Decimal` or `int` in, finite `Decimal` out. A float (or bool) is a caller bug."""
    if isinstance(value, bool) or not isinstance(value, Decimal | int):
        raise TypeError(f"{name} must be Decimal, not {type(value).__name__}")
    result = Decimal(value)
    if not result.is_finite():
        raise ValueError(f"{name} must be finite")
    return result


def check_count(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be int, not {type(value).__name__}")
    if value < 0:
        raise ValueError(f"{name} must not be negative")
    return value


def _check_id(value: object, name: str) -> None:
    if not isinstance(value, str) or not value:
        raise TypeError(f"{name} must be a non-empty str")


@dataclass(frozen=True, slots=True)
class ReturnContext:
    year: int
    filing: FilingStatus
    taxpayer_id: str
    spouse_id: str | None = None

    def __post_init__(self) -> None:
        check_count(self.year, "year")
        if not isinstance(self.filing, FilingStatus):
            raise TypeError("filing must be a FilingStatus member")
        _check_id(self.taxpayer_id, "taxpayer_id")
        if self.spouse_id is not None:
            _check_id(self.spouse_id, "spouse_id")
            if self.spouse_id == self.taxpayer_id:
                raise ValueError("spouse_id must differ from taxpayer_id")

    @property
    def joint(self) -> bool:
        return self.spouse_id is not None

    @property
    def person_ids(self) -> frozenset[str]:
        return frozenset({self.taxpayer_id} | ({self.spouse_id} if self.spouse_id else set()))


@dataclass(frozen=True, slots=True)
class ItemInput:
    id: str
    person_id: str | None
    category: Category
    year: int
    deductible_amount: Decimal
    labour_share_35a: Decimal | None
    payment_method: PaymentMethod
    is_relevant: bool
    overridden_by_user: bool
    attention_reason: AttentionReason | None  # the document's reason while `needs_attention`

    def __post_init__(self) -> None:
        _check_id(self.id, "id")
        check_count(self.year, "year")
        check_decimal(self.deductible_amount, "deductible_amount")
        if self.labour_share_35a is not None:
            check_decimal(self.labour_share_35a, "labour_share_35a")


@dataclass(frozen=True, slots=True)
class EmploymentInput:
    person_id: str
    commute_km: int | None
    office_days: int
    homeoffice_days: int

    def __post_init__(self) -> None:
        _check_id(self.person_id, "person_id")
        if self.commute_km is not None:
            check_count(self.commute_km, "commute_km")
        check_count(self.office_days, "office_days")
        check_count(self.homeoffice_days, "homeoffice_days")


@dataclass(frozen=True, slots=True)
class ChildInput:
    person_id: str
    dob: date
    disability_grade: int | None
    months: int
    allowance_half: bool
    in_household: bool

    def __post_init__(self) -> None:
        _check_id(self.person_id, "person_id")
        if not isinstance(self.dob, date):
            raise TypeError("dob must be a date")
        if self.disability_grade is not None:
            check_count(self.disability_grade, "disability_grade")
        if check_count(self.months, "months") > 12:
            raise ValueError("months must be at most 12")


@dataclass(frozen=True, slots=True)
class Note:
    """Codes and ids only, never an amount or a name."""

    code: DeductionNote
    item_id: str | None = None
    person_id: str | None = None


@dataclass(frozen=True, slots=True)
class PersonWk:
    entfernungspauschale: Decimal
    homeoffice_pauschale: Decimal
    arbeitszimmer: Decimal
    items_by_category: dict[Category, Decimal]
    actual: Decimal
    pauschbetrag: Decimal
    applied: Decimal
    used_pauschbetrag: bool
    # Gewerkschaftsbeiträge counted on top of the Pauschbetrag (§ 9a Satz 3, from VZ 2026).
    beside_pauschbetrag: Decimal = Decimal(0)


@dataclass(frozen=True, slots=True)
class WerbungskostenResult:
    by_person: dict[str, PersonWk]
    total_applied: Decimal
    notes: tuple[Note, ...] = field(default=())


@dataclass(frozen=True, slots=True)
class ChildLine:
    costs: Decimal
    deductible: Decimal
    cap_reached: bool


@dataclass(frozen=True, slots=True)
class SonderausgabenResult:
    kirchensteuer: Decimal
    spenden_net: Decimal
    spenden_limit: Decimal
    spenden_deductible: Decimal
    spenden_excess: Decimal
    kinderbetreuung_by_child: dict[str, ChildLine]
    schulgeld_by_child: dict[str, ChildLine]
    actual_total: Decimal
    pauschbetrag: Decimal
    applied: Decimal
    used_pauschbetrag: bool
    notes: tuple[Note, ...] = field(default=())


@dataclass(frozen=True, slots=True)
class VorsorgeInput:
    """Final Vorsorge amounts of one person (#16); their source is not this DTO's business."""

    person_id: str
    employed: bool  # selects 1.900 EUR, the 4 % cut and the assumed employer share
    rv_employee: Decimal  # Nr. 2 Buchst. a
    rv_employer: Decimal | None  # steuerfreier Arbeitgeberanteil (§ 3 Nr. 62); None = assume
    basisrente: Decimal  # Nr. 2 Buchst. b
    kv: Decimal  # Nr. 3 Buchst. a, as certified (before the 4 % cut)
    pv: Decimal  # Nr. 3 Buchst. b
    sonstige: Decimal  # Nr. 3a

    def __post_init__(self) -> None:
        _check_id(self.person_id, "person_id")
        if not isinstance(self.employed, bool):
            raise TypeError("employed must be bool")
        amounts = {
            "rv_employee": self.rv_employee,
            "basisrente": self.basisrente,
            "kv": self.kv,
            "pv": self.pv,
            "sonstige": self.sonstige,
        }
        if self.rv_employer is not None:
            amounts["rv_employer"] = self.rv_employer
        for name, value in amounts.items():
            if check_decimal(value, name) < 0:
                raise ValueError(f"{name} must not be negative")


@dataclass(frozen=True, slots=True)
class AltersvorsorgeLine:
    beitraege: Decimal
    hoechstbetrag: Decimal
    angesetzt: Decimal
    arbeitgeberanteil: Decimal
    abzug: Decimal


@dataclass(frozen=True, slots=True)
class KrankenLine:
    nr3: Decimal
    nr3a: Decimal
    cap: Decimal
    abzug: Decimal


@dataclass(frozen=True, slots=True)
class VorsorgeResult:
    altersvorsorge: AltersvorsorgeLine
    kranken: KrankenLine
    total: Decimal
    notes: tuple[Note, ...] = field(default=())
