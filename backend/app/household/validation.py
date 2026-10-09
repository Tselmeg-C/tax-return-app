"""Pure validation rules for the household profile (#13). No I/O, no DB, no clock.

Every refusal is a `Rejected` carrying a code and the field name, never the sent value (the
api answers `422 {"detail": "<code>", "field": "<name>"}`).
"""

from __future__ import annotations

import calendar
from collections import Counter
from datetime import date
from typing import Any

MIN_DOB = date(1900, 1, 1)
NAME_MAX = 100
EMPLOYER_MAX = 200
DISABILITY_GRADES = frozenset(range(20, 101, 10))
COMMUTE_MAX = 999
CHILD_AGE_LIMIT = 25


class Rejected(Exception):
    """A refused write: `status` + `code` (+ `field`, + extra body keys such as `count`).
    Never carries a submitted value."""

    def __init__(self, status: int, code: str, field: str | None = None, **extra: Any) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.field = field
        self.extra = extra


def invalid(code: str, field: str | None = None) -> Rejected:
    return Rejected(422, code, field)


# --- Steuer-ID -----------------------------------------------------------------------------


def steuer_id_check_digit(first_ten: str) -> int:
    """ISO/IEC 7064 MOD 11,10 check digit of the first ten digits.

    Source: § 139b AO with the Steueridentifikationsnummerverordnung (StIdV, BGBl. I 2007,
    S. 1101), which prescribes the check digit by ISO/IEC 7064 MOD 11,10; algorithm as in the
    BZSt's published description of the Steuer-ID ("Prüfziffernberechnung").
    """
    product = 10
    for char in first_ten:
        total = (int(char) + product) % 10
        if total == 0:
            total = 10
        product = (2 * total) % 11
    check = 11 - product
    return 0 if check == 10 else check


def _digit_pattern_ok(first_ten: str) -> bool:
    """Exactly one digit occurs twice or three times, every other at most once; a digit
    occurring three times does not stand in three consecutive positions (StIdV, since 2016)."""
    counts = Counter(first_ten)
    repeated = [d for d, n in counts.items() if n > 1]
    if len(repeated) != 1 or counts[repeated[0]] > 3:
        return False
    digit = repeated[0]
    return not (counts[digit] == 3 and digit * 3 in first_ten)


def normalise_steuer_id(raw: str) -> str:
    """The 11 digits without spaces, or `invalid_steuer_id`."""
    value = raw.replace(" ", "")
    if (
        len(value) != 11
        or not value.isascii()
        or not value.isdigit()
        or value[0] == "0"
        or not _digit_pattern_ok(value[:10])
        or steuer_id_check_digit(value[:10]) != int(value[10])
    ):
        raise invalid("invalid_steuer_id", "steuer_id")
    return value


def mask_steuer_id(value: str | None) -> str | None:
    """`"XX XXX XXX 901"` (last three digits) or `None`. The only way a Steuer-ID is shown."""
    return None if value is None else f"XX XXX XXX {value[-3:]}"


# --- person fields -------------------------------------------------------------------------


def clean_first_name(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > NAME_MAX:
        raise invalid("invalid_name", "first_name")
    return value.strip()


def clean_last_name(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value.strip()) > NAME_MAX:
        raise invalid("invalid_name", "last_name")
    return value.strip() or None


def check_dob(dob: date | None, *, is_child: bool, today: date) -> None:
    if dob is None:
        if is_child:
            raise invalid("invalid_dob", "dob")
        return
    if dob > today or dob < MIN_DOB:
        raise invalid("invalid_dob", "dob")


def check_disability_grade(value: int | None) -> None:
    if value is not None and value not in DISABILITY_GRADES:
        raise invalid("invalid_disability_grade", "disability_grade")


# --- employment ----------------------------------------------------------------------------


def days_in_year(year: int) -> int:
    return 366 if calendar.isleap(year) else 365


def clean_employer_name(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > EMPLOYER_MAX:
        raise invalid("invalid_request", "employer_name")
    return value.strip()


def check_employment(
    *,
    year: int,
    steuerklasse: str,
    has_factor: bool,
    commute_km: int | None,
    office_days: int,
    homeoffice_days: int,
    other_days: int,
) -> None:
    """`other_days`: office + homeoffice days of the person's other employments that year."""
    if has_factor and steuerklasse != "4":
        raise invalid("factor_requires_iv", "has_factor")
    if commute_km is not None and not 0 <= commute_km <= COMMUTE_MAX:
        raise invalid("invalid_commute", "commute_km")
    limit = days_in_year(year)
    for field, days in (("office_days", office_days), ("homeoffice_days", homeoffice_days)):
        if not 0 <= days <= limit:
            raise invalid("invalid_days", field)
    if other_days + office_days + homeoffice_days > limit:
        raise invalid("days_exceed_year", "office_days")


# --- children ------------------------------------------------------------------------------


def max_months(dob: date, year: int, *, has_disability: bool) -> int | None:
    """Months child C can count in `year` (Monatsprinzip, § 32 Abs. 3–4, § 66 Abs. 2 EStG).

    `None` = not born by 31.12. of `year`; `0` = turned 25 before `year` (no limit with a
    disability, § 32 Abs. 4 Nr. 3). No cut at 18: those months are entered by the user.
    """
    if dob.year > year:
        return None
    start = dob.month if dob.year == year else 1
    end = 12
    if not has_disability:
        limit_year = dob.year + CHILD_AGE_LIMIT
        if limit_year < year:
            return 0
        if limit_year == year:
            end = dob.month
    return end - start + 1
