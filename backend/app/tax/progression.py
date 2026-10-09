"""§ 32b EStG Progressionsvorbehalt (#86). Pure, Decimal only.

`tariff_with_progression` is the tarifliche Einkommensteuer (full euros) with the besonderer
Steuersatz of § 32b Abs. 2 Nr. 1: the average rate on (zvE + Lohnersatz), applied to the zvE.
The tariff itself is `app.tax.tariff.income_tax`. The law text has no rounding rule for the
rate and no minimum rate; `ProgressionParams.rate_decimals` holds the (unconfirmed) rounding.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import ROUND_FLOOR, Decimal

from app.domain.enums import DeductionNote, FilingStatus
from app.tax.deductions.models import Note, ReturnContext, check_decimal
from app.tax.models import ProgressionParams, TariffParams
from app.tax.tariff import income_tax

_ZERO = Decimal(0)
_EURO = Decimal(1)


@dataclass(frozen=True, slots=True)
class LohnersatzInput:
    person_id: str
    amount: Decimal  # sum of the year's benefits of § 32b Abs. 1 Nr. 1, >= 0
    # Arbeitnehmer-Pauschbetrag / Werbungskosten already deducted from this person's wages
    # (PersonWk.applied - beside_pauschbetrag, limited by the wage, 0 without wages).
    wk_deducted_from_wages: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.person_id, str) or not self.person_id:
            raise TypeError("person_id must be a non-empty str")
        if check_decimal(self.amount, "amount") < 0:
            raise ValueError("amount must not be negative")
        if check_decimal(self.wk_deducted_from_wages, "wk_deducted_from_wages") < 0:
            raise ValueError("wk_deducted_from_wages must not be negative")


@dataclass(frozen=True, slots=True)
class ProgressionAmount:
    total: Decimal
    by_person: dict[str, Decimal]
    notes: tuple[Note, ...] = field(default=())


def progression_amount(
    ctx: ReturnContext, benefits: Iterable[LohnersatzInput], pauschbetrag: Decimal
) -> ProgressionAmount:
    """Per person max(0, benefits - unused Pauschbetrag) (§ 32b Abs. 2 Nr. 1), summed."""
    pb = check_decimal(pauschbetrag, "pauschbetrag")
    amounts: dict[str, Decimal] = {}
    deducted: dict[str, Decimal] = {}
    notes: list[Note] = []
    for b in benefits:
        if b.person_id not in ctx.person_ids:
            notes.append(Note(DeductionNote.PERSON_NOT_IN_RETURN, person_id=b.person_id))
            continue
        amounts[b.person_id] = amounts.get(b.person_id, _ZERO) + b.amount
        deducted[b.person_id] = max(deducted.get(b.person_id, _ZERO), b.wk_deducted_from_wages)
    by_person = {
        pid: max(_ZERO, amount - max(_ZERO, pb - deducted[pid])) for pid, amount in amounts.items()
    }
    return ProgressionAmount(sum(by_person.values(), _ZERO), by_person, tuple(notes))


def tariff_with_progression(
    zve: Decimal | int,
    filing: FilingStatus,
    lohnersatz: Decimal | int,
    tariff: TariffParams,
    p: ProgressionParams,
) -> Decimal:
    """Tarifliche ESt in full euros with the besonderer Steuersatz; `lohnersatz = 0` = plain."""
    if not isinstance(filing, FilingStatus):
        raise TypeError("filing must be a FilingStatus member")
    x = max(check_decimal(zve, "zve").quantize(_EURO, rounding=ROUND_FLOOR), _ZERO)
    extra = check_decimal(lohnersatz, "lohnersatz")
    if extra < 0:
        raise ValueError("lohnersatz must not be negative")
    if x == 0 or extra == 0:
        return income_tax(x, filing, tariff)
    b = (x + extra).quantize(_EURO, rounding=ROUND_FLOOR)  # § 32a Abs. 1 Satz 1
    e = income_tax(b, filing, tariff)
    xi, ei, bi = int(x), int(e), int(b)
    if p.rate_decimals == "exact":
        return Decimal(xi * ei // bi)  # multiply before dividing: no rounded quotient
    scale = 10 ** int(p.rate_decimals)
    return Decimal(xi * (ei * scale // bi) // scale)
