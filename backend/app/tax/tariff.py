"""§ 32a EStG tariff (incl. Splitting), Solidaritätszuschlag and Kirchensteuer. Pure, Decimal only.

Rounding only where the law says: zvE and tax down to full euros (§ 32a Abs. 1 Sätze 1, 6
EStG), Soli down to cents (§ 4 Satz 3 SolzG 1995), Kirchensteuer down to cents. Inputs are
the Bemessungsgrundlagen (zvE, tariff ESt), not the household.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal

from app.domain.enums import FilingStatus
from app.tax.models import ChurchTaxParams, SoliParams, TariffParams, TaxParams

_ZERO = Decimal(0)
_CENT = Decimal("0.01")
_EURO = Decimal(1)
_TEN_THOUSAND = Decimal(10000)


@dataclass(frozen=True, slots=True)
class TariffResult:
    est: Decimal  # full euros
    soli: Decimal  # cents
    kist: Decimal  # cents


def _decimal(value: Decimal | int, name: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, Decimal | int):
        raise TypeError(f"{name} must be Decimal or int, not {type(value).__name__}")
    result = Decimal(value)
    if not result.is_finite():
        raise ValueError(f"{name} must be finite")
    return result


def _floor(value: Decimal, step: Decimal) -> Decimal:
    return value.quantize(step, rounding=ROUND_FLOOR)


def zone_formula(zone: int, x: Decimal, p: TariffParams) -> Decimal:
    """The unrounded formula of § 32a Abs. 1 Nr. `zone` for the full-euro zvE `x`."""
    if zone == 1:
        return _ZERO
    if zone == 2:
        y = (x - p.grundfreibetrag) / _TEN_THOUSAND
        return (p.zone2.a * y + p.zone2.b) * y
    if zone == 3:
        z = (x - p.zone2.upper) / _TEN_THOUSAND
        return (p.zone3.a * z + p.zone3.b) * z + p.zone3.c
    if zone == 4:
        return p.zone4.rate * x - p.zone4.minus
    if zone == 5:
        return p.zone5.rate * x - p.zone5.minus
    raise ValueError(f"no zone {zone}")


def _basic_tariff(x: Decimal, p: TariffParams) -> Decimal:
    """§ 32a Abs. 1 for a zvE already floored to full euros."""
    uppers = (p.grundfreibetrag, p.zone2.upper, p.zone3.upper, p.zone4.upper)
    zone = next((i for i, upper in enumerate(uppers, start=1) if x <= upper), 5)
    return max(_floor(zone_formula(zone, x, p), _EURO), _ZERO)


def income_tax(zve: Decimal | int, filing: FilingStatus, p: TariffParams) -> Decimal:
    """Tarifliche Einkommensteuer in full euros; Splitting (§ 32a Abs. 5) for `joint`."""
    x = _floor(_decimal(zve, "zve"), _EURO)
    if filing is FilingStatus.JOINT:
        return 2 * _basic_tariff(_floor(x / 2, _EURO), p)
    return _basic_tariff(x, p)


def solidarity_surcharge(bmg: Decimal, filing: FilingStatus, p: SoliParams) -> Decimal:
    """§§ 3 Abs. 3, 4 SolzG 1995: 0 up to the Freigrenze, then capped by the Milderungszone."""
    base = _decimal(bmg, "bmg")
    freigrenze = p.freigrenze_joint if filing is FilingStatus.JOINT else p.freigrenze_single
    if base <= freigrenze:
        return _floor(_ZERO, _CENT)
    return _floor(min(p.rate * base, p.milderung_rate * (base - freigrenze)), _CENT)


def church_tax(bmg: Decimal, state: str | None, p: ChurchTaxParams) -> Decimal:
    """Rate of the state × Bemessungsgrundlage, down to cents; `None` = not a member."""
    base = _decimal(bmg, "bmg")
    if state is None:
        return _floor(_ZERO, _CENT)
    if state not in p.rate_by_state:
        raise ValueError(f"unknown state code: {state!r}")
    return _floor(max(p.rate_by_state[state] * base, _ZERO), _CENT)


def tariff_result(
    zve: Decimal | int, filing: FilingStatus, state: str | None, params: TaxParams
) -> TariffResult:
    est = income_tax(zve, filing, params.tariff)
    return TariffResult(
        est=est,
        soli=solidarity_surcharge(est, filing, params.soli),
        kist=church_tax(est, state, params.church_tax),
    )
