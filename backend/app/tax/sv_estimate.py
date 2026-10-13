"""Estimate of an employee's Sozialversicherung from the gross wage (#85). Pure, Decimal only.

Assumptions (all documented in the UI as "geschätzt"): statutory health insurance, the beitrags-
pflichtige Entgelt is the gross wage over 12 months, no Entgeltumwandlung, average Zusatzbeitrag
split half and half, no Minijob / Übergangsbereich / Beamte / PKV / Rentner / Student. Each
branch is rounded half-even to cents (the law has no rule for an estimate). Employee shares only.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal

from app.tax.deductions.models import check_count, check_decimal
from app.tax.models import SozialversicherungParams

_CENT = Decimal("0.01")
_HALF = Decimal("0.5")


@dataclass(frozen=True, slots=True)
class SvEstimate:
    rv_employee: Decimal
    rv_employer: Decimal  # set equal to the employee share (§ 168 SGB VI), stated explicitly
    kv: Decimal
    pv: Decimal
    alv: Decimal  # lands in Nr. 3a (`sonstige`)


def pv_employee_rate(
    p: SozialversicherungParams, *, children_under_25: int, childless: bool
) -> Decimal:
    """Employee share: half the rate, + surcharge if childless, - 0.25 per child 2 to 5 under 25."""
    steps = min(
        max(check_count(children_under_25, "children_under_25") - 1, 0), p.pv_abschlag_max_steps
    )
    rate = p.pv_rate * _HALF + (p.pv_childless_surcharge if childless else Decimal(0))
    return rate - p.pv_child_abschlag * steps


def _cents(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_EVEN)


def estimate_sv(
    wage: Decimal,
    p: SozialversicherungParams,
    *,
    children_under_25: int,
    childless_surcharge: bool,
) -> SvEstimate | None:
    """`None` when the wage is in the Minijob / Übergangsbereich range or below (other formula)."""
    wage = check_decimal(wage, "wage")
    if wage < 0:
        raise ValueError("wage must not be negative")
    if wage <= p.uebergangsbereich_upper_monthly * 12:
        return None
    rv_base, kv_base = min(wage, p.bbg_rv), min(wage, p.bbg_kv)
    rv = _cents(rv_base * p.rv_rate * _HALF)
    pv_rate = pv_employee_rate(
        p, children_under_25=children_under_25, childless=childless_surcharge
    )
    return SvEstimate(
        rv_employee=rv,
        rv_employer=rv,
        kv=_cents(kv_base * (p.kv_rate + p.kv_zusatz_average) * _HALF),
        pv=_cents(kv_base * pv_rate),
        alv=_cents(rv_base * p.alv_rate * _HALF),
    )
