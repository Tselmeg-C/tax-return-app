"""Runtime generator of valid Steuer-IDs for tests (#13). No fixed ID is ever committed."""

from __future__ import annotations

import secrets

from app.household.validation import steuer_id_check_digit

_rng = secrets.SystemRandom()


def generate_steuer_id() -> str:
    """11 digits: no leading 0, one digit twice or three times (not three in a row), every
    other digit at most once, digit 11 = ISO 7064 MOD 11,10 check digit."""
    while True:
        digits = _rng.sample("0123456789", 10)
        repeated, times = digits[0], _rng.choice((2, 3))
        body = [repeated] * times + digits[1 : 11 - times]
        _rng.shuffle(body)
        first_ten = "".join(body)
        if first_ten[0] != "0" and repeated * 3 not in first_ten:
            return first_ten + str(steuer_id_check_digit(first_ten))


def spaced(steuer_id: str) -> str:
    """`"12 345 678 901"` form."""
    return f"{steuer_id[:2]} {steuer_id[2:5]} {steuer_id[5:8]} {steuer_id[8:]}"
