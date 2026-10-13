"""§ 108 Abs. 3 AO: a deadline ending on a Saturday, Sunday or public holiday ends on the next
working day. Applied at display time only (the stored deadlines stay statutory, #92).

ponytail: only holidays that apply in every Land (New Year, Karfreitag, Ostermontag, 1 May,
Himmelfahrt, Pfingstmontag, 3 Oct, 25 / 26 Dec); Land-specific ones (e.g. 31 Oct, Fronleichnam)
are ignored. Add a Land argument when a deadline is ever shown for one of them.
"""

from __future__ import annotations

from datetime import date, timedelta


def easter_sunday(year: int) -> date:
    """Anonymous Gregorian algorithm (Meeus / Jones / Butcher)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month, day = divmod(h + ell - 7 * m + 114, 31)
    return date(year, month, day + 1)


def national_holidays(year: int) -> frozenset[date]:
    easter = easter_sunday(year)
    return frozenset(
        {
            date(year, 1, 1),
            easter - timedelta(days=2),
            easter + timedelta(days=1),
            date(year, 5, 1),
            easter + timedelta(days=39),
            easter + timedelta(days=50),
            date(year, 10, 3),
            date(year, 12, 25),
            date(year, 12, 26),
        }
    )


def next_working_day(day: date) -> date:
    """`day` itself if it is a working day, else the next one."""
    while day.weekday() >= 5 or day in national_holidays(day.year):
        day += timedelta(days=1)
    return day
