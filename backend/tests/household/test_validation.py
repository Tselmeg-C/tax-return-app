"""Pure household rules (#13): Steuer-ID, person fields, employment days, maximum months."""

from __future__ import annotations

from collections import Counter
from datetime import date

import pytest

from app.household.validation import (
    Rejected,
    check_disability_grade,
    check_dob,
    check_employment,
    days_in_year,
    mask_steuer_id,
    max_months,
    normalise_steuer_id,
    steuer_id_check_digit,
)
from tests.household.steuer_ids import generate_steuer_id, spaced


def _code(exc: pytest.ExceptionInfo[Rejected]) -> str:
    return exc.value.code


def test_generator_follows_the_rules() -> None:
    for _ in range(200):
        value = generate_steuer_id()
        assert len(value) == 11 and value[0] != "0"
        counts = Counter(value[:10])
        assert sorted(counts.values())[-1] in (2, 3)
        assert sorted(counts.values())[-2] == 1


def test_100_generated_ids_are_accepted_also_with_spaces() -> None:
    for _ in range(100):
        value = generate_steuer_id()
        assert normalise_steuer_id(value) == value
        assert normalise_steuer_id(spaced(value)) == value


def _bad_ids() -> list[tuple[str, str]]:
    good = generate_steuer_id()
    wrong_check = good[:10] + str((int(good[10]) + 1) % 10)
    distinct = "1023456789"
    four_times = "1121314567"  # digit 1 four times
    three_in_row = "1112345678"
    # The pattern cases get a correct check digit, so only the pattern rule rejects them.
    with_check = [p + str(steuer_id_check_digit(p)) for p in (distinct, four_times, three_in_row)]
    zero = "0" + good[1:]
    return [
        ("wrong check digit", wrong_check),
        ("10 digits", good[:10]),
        ("12 digits", good + "1"),
        ("letter", good[:5] + "A" + good[6:]),
        ("leading zero", zero),
        ("ten distinct digits", with_check[0]),
        ("one digit four times", with_check[1]),
        ("three in a row", with_check[2]),
    ]


@pytest.mark.parametrize("case", range(8))
def test_invalid_ids_rejected_without_echo(case: int) -> None:
    label, value = _bad_ids()[case]
    with pytest.raises(Rejected) as exc:
        normalise_steuer_id(value)
    assert _code(exc) == "invalid_steuer_id", label
    assert exc.value.field == "steuer_id"
    assert value not in str(exc.value) and value not in repr(exc.value)


def test_three_times_not_consecutive_is_valid() -> None:
    first_ten = "1213456789"[:9] + "1"  # 1 three times, never three in a row
    assert Counter(first_ten)["1"] == 3
    value = first_ten + str(steuer_id_check_digit(first_ten))
    assert normalise_steuer_id(value) == value


def test_mask() -> None:
    value = generate_steuer_id()
    assert mask_steuer_id(value) == f"XX XXX XXX {value[-3:]}"
    assert mask_steuer_id(None) is None


def test_dob_and_disability() -> None:
    today = date(2026, 1, 15)
    for dob, child in ((date(2026, 1, 16), False), (date(1899, 12, 31), False), (None, True)):
        with pytest.raises(Rejected) as exc:
            check_dob(dob, is_child=child, today=today)
        assert _code(exc) == "invalid_dob"
    check_dob(None, is_child=False, today=today)
    check_dob(today, is_child=True, today=today)
    for grade in (15, 110, 0):
        with pytest.raises(Rejected) as exc:
            check_disability_grade(grade)
        assert _code(exc) == "invalid_disability_grade"
    for grade in (None, 20, 50, 100):
        check_disability_grade(grade)


def _job(year: int, office: int, home: int, other: int = 0, **kw: object) -> None:
    values: dict[str, object] = {
        "year": year,
        "steuerklasse": "1",
        "has_factor": False,
        "commute_km": None,
        "office_days": office,
        "homeoffice_days": home,
        "other_days": other,
    }
    values.update(kw)
    check_employment(**values)  # type: ignore[arg-type]


def test_days_limits_including_leap_year() -> None:
    assert days_in_year(2025) == 365 and days_in_year(2028) == 366
    _job(2025, 200, 65, 100)
    with pytest.raises(Rejected) as exc:
        _job(2025, 200, 66, 100)
    assert (_code(exc), exc.value.field) == ("days_exceed_year", "office_days")
    _job(2028, 200, 66, 100)
    with pytest.raises(Rejected) as exc:
        _job(2028, 200, 67, 100)
    assert _code(exc) == "days_exceed_year"
    with pytest.raises(Rejected) as exc:
        _job(2025, 366, 0)
    assert (_code(exc), exc.value.field) == ("invalid_days", "office_days")
    with pytest.raises(Rejected) as exc:
        _job(2025, 0, 0, commute_km=1000)
    assert _code(exc) == "invalid_commute"
    with pytest.raises(Rejected) as exc:
        _job(2025, 0, 0, steuerklasse="3", has_factor=True)
    assert _code(exc) == "factor_requires_iv"
    _job(2025, 0, 0, steuerklasse="4", has_factor=True)


@pytest.mark.parametrize(
    ("dob", "year", "disabled", "expected"),
    [
        (date(2025, 3, 10), 2025, False, 10),  # born in the year
        (date(2000, 6, 15), 2025, False, 6),  # turns 25 in June
        (date(1999, 6, 15), 2025, False, 0),  # turned 25 before -> child_too_old
        (date(1999, 6, 15), 2025, True, 12),  # no age limit with a disability
        (date(2007, 4, 1), 2025, False, 12),  # turns 18: no automatic cut
        (date(2026, 1, 5), 2025, False, None),  # not born yet
        (date(2026, 1, 5), 2026, False, 12),
        (date(2001, 3, 20), 2026, False, 3),  # turns 25 in March 2026
    ],
)
def test_max_months(dob: date, year: int, disabled: bool, expected: int | None) -> None:
    assert max_months(dob, year, has_disability=disabled) == expected
