from datetime import date

from app.estimate.deadlines import easter_sunday, national_holidays, next_working_day


def test_easter_dates() -> None:
    assert easter_sunday(2025) == date(2025, 4, 20)
    assert easter_sunday(2026) == date(2026, 4, 5)
    assert easter_sunday(2027) == date(2027, 3, 28)


def test_working_day_is_unchanged() -> None:
    assert next_working_day(date(2026, 7, 31)) == date(2026, 7, 31)  # Friday


def test_weekend_moves_to_monday() -> None:
    assert next_working_day(date(2027, 7, 31)) == date(2027, 8, 2)  # Saturday
    assert next_working_day(date(2027, 2, 28)) == date(2027, 3, 1)  # Sunday


def test_holidays_are_skipped() -> None:
    assert date(2026, 4, 3) in national_holidays(2026)  # Karfreitag
    assert next_working_day(date(2026, 4, 3)) == date(2026, 4, 7)  # + weekend + Ostermontag
    assert next_working_day(date(2027, 1, 1)) == date(2027, 1, 4)  # New Year (Friday)
