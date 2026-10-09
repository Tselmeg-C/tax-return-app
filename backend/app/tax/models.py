"""Typed, frozen tax parameters of one year (pure; the YAML is read by `app.tax_params`).

Money and rates are `Decimal`, given in the YAML as quoted strings: an unquoted number
(which YAML would turn into a float or int) is rejected, naming the key.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Annotated, Self

from pydantic import BaseModel, BeforeValidator, ConfigDict, model_validator

from app.domain.enums import Anlage, Category

# ISO 3166-2:DE codes without "DE-".
STATES: frozenset[str] = frozenset("BW BY BE BB HB HH HE MV NI NW RP SL SN ST SH TH".split())


def _quoted_decimal(value: object) -> Decimal:
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, str):
        try:
            result = Decimal(value)
        except InvalidOperation:
            raise ValueError(f"not a decimal number: {value!r}") from None
    else:
        raise ValueError('must be a quoted string (e.g. "0.42"), not an unquoted number')
    if not result.is_finite():
        raise ValueError("must be finite")
    return result


Dec = Annotated[Decimal, BeforeValidator(_quoted_decimal)]


class _Section(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str
    provisional: bool = False


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Zone2(_Frozen):
    """§ 32a Abs. 1 Nr. 2: `(a·y + b)·y` up to `upper`."""

    upper: Dec
    a: Dec
    b: Dec


class Zone3(Zone2):
    """§ 32a Abs. 1 Nr. 3: `(a·z + b)·z + c` up to `upper`."""

    c: Dec


class LinearZone(_Frozen):
    """Zones 4 and 5 of § 32a Abs. 1: `rate·x − minus` (zone 5 has no `upper`)."""

    rate: Dec
    minus: Dec


class BoundedLinearZone(LinearZone):
    upper: Dec


class TariffParams(_Section):
    grundfreibetrag: Dec
    zone2: Zone2
    zone3: Zone3
    zone4: BoundedLinearZone
    zone5: LinearZone

    @model_validator(mode="after")
    def _check(self) -> Self:
        bounds = (self.grundfreibetrag, self.zone2.upper, self.zone3.upper, self.zone4.upper)
        if not all(lo < hi for lo, hi in zip(bounds, bounds[1:], strict=False)):
            raise ValueError(
                "zone bounds must ascend: grundfreibetrag < zone2.upper < zone3.upper < zone4.upper"
            )
        for name, rate in (("zone4.rate", self.zone4.rate), ("zone5.rate", self.zone5.rate)):
            if not 0 < rate < 1:
                raise ValueError(f"{name} must be in (0, 1)")
        return self


class SoliParams(_Section):
    rate: Dec
    freigrenze_single: Dec
    freigrenze_joint: Dec
    milderung_rate: Dec

    @model_validator(mode="after")
    def _check(self) -> Self:
        for name in ("rate", "milderung_rate"):
            if not 0 < getattr(self, name) < 1:
                raise ValueError(f"{name} must be in (0, 1)")
        if self.freigrenze_joint != 2 * self.freigrenze_single:
            raise ValueError("freigrenze_joint must be 2 × freigrenze_single")
        return self


class ChurchTaxParams(_Section):
    rate_by_state: dict[str, Dec]

    @model_validator(mode="after")
    def _check(self) -> Self:
        given = set(self.rate_by_state)
        if missing := sorted(STATES - given):
            raise ValueError(f"rate_by_state: missing {', '.join(missing)}")
        if extra := sorted(given - STATES):
            raise ValueError(f"rate_by_state: unknown {', '.join(extra)}")
        for state, rate in self.rate_by_state.items():
            if not 0 < rate < 1:
                raise ValueError(f"rate_by_state.{state} must be in (0, 1)")
        return self


def _rate(name: str, value: Decimal) -> None:
    if not 0 < value <= 1:
        raise ValueError(f"{name} must be in (0, 1]")


HOMEOFFICE_MAX_DAYS = 210  # § 4 Abs. 5 Satz 1 Nr. 6c: 6 Euro, höchstens 1.260 Euro = 210 Tage


class EntfernungspauschaleParams(_Frozen):
    rate_km_1_to_20: Dec
    rate_from_km_21: Dec
    max_without_car: Dec  # stored, not applied in v1 (#74)


class HomeofficeParams(_Frozen):
    per_day: Dec
    max_amount: Dec


class WerbungskostenParams(_Section):
    arbeitnehmer_pauschbetrag: Dec
    entfernungspauschale: EntfernungspauschaleParams
    homeoffice: HomeofficeParams
    # § 9a Satz 3 EStG (from VZ 2026): Gewerkschaftsbeiträge count on top of the Pauschbetrag.
    union_dues_beside_pauschbetrag: bool

    @model_validator(mode="after")
    def _check(self) -> Self:
        e = self.entfernungspauschale
        _rate("entfernungspauschale.rate_km_1_to_20", e.rate_km_1_to_20)
        _rate("entfernungspauschale.rate_from_km_21", e.rate_from_km_21)
        if self.homeoffice.max_amount != self.homeoffice.per_day * HOMEOFFICE_MAX_DAYS:
            raise ValueError(f"homeoffice.max_amount must be per_day × {HOMEOFFICE_MAX_DAYS}")
        return self


class KinderbetreuungParams(_Frozen):
    share: Dec
    max_per_child: Dec
    age_limit: int


class SchulgeldParams(_Frozen):
    share: Dec
    max_per_child: Dec


class SonderausgabenParams(_Section):
    pauschbetrag_single: Dec
    pauschbetrag_joint: Dec
    spenden_max_share_of_gde: Dec
    kinderbetreuung: KinderbetreuungParams
    schulgeld: SchulgeldParams

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.pauschbetrag_joint != 2 * self.pauschbetrag_single:
            raise ValueError("pauschbetrag_joint must be 2 × pauschbetrag_single")
        _rate("spenden_max_share_of_gde", self.spenden_max_share_of_gde)
        _rate("kinderbetreuung.share", self.kinderbetreuung.share)
        _rate("schulgeld.share", self.schulgeld.share)
        return self


class VorsorgeParams(_Section):
    altersvorsorge_hoechstbetrag: (
        Dec  # § 10 Abs. 3 Satz 1: Höchstbeitrag knappschaftl. RV, aufgerundet
    )
    krankengeld_cut: Dec  # § 10 Abs. 1 Nr. 3 Buchst. a Satz 4
    basis_cap: Dec  # § 10 Abs. 4 Satz 1
    basis_cap_reduced: Dec  # § 10 Abs. 4 Satz 2

    @model_validator(mode="after")
    def _check(self) -> Self:
        if not 0 < self.krankengeld_cut < 1:
            raise ValueError("krankengeld_cut must be in (0, 1)")
        if not 0 < self.basis_cap_reduced < self.basis_cap:
            raise ValueError("basis_cap_reduced must be in (0, basis_cap)")
        if self.altersvorsorge_hoechstbetrag <= 0:
            raise ValueError("altersvorsorge_hoechstbetrag must be positive")
        return self


class MappingEntry(_Frozen):
    """Where a category goes on the forms (#9). `zeile` null = not mapped / computed elsewhere."""

    anlage: Anlage
    zeile: str | None
    source: str
    provisional: bool = False


MAPPED_CATEGORIES: frozenset[Category] = frozenset(Category) - {Category.IRRELEVANT}


class TaxParams(_Frozen):
    year: int
    tariff: TariffParams
    soli: SoliParams
    church_tax: ChurchTaxParams
    werbungskosten: WerbungskostenParams
    sonderausgaben: SonderausgabenParams
    vorsorge: VorsorgeParams
    mapping: dict[Category, MappingEntry]

    @model_validator(mode="after")
    def _check_mapping(self) -> Self:
        given = set(self.mapping)
        if Category.IRRELEVANT in given:
            raise ValueError("mapping: irrelevant must not be mapped")
        if missing := sorted(c.value for c in MAPPED_CATEGORIES - given):
            raise ValueError(f"mapping: missing {', '.join(missing)}")
        return self

    @property
    def provisional_sections(self) -> tuple[str, ...]:
        """Names of the sections marked `provisional: true` (shown as "vorläufig")."""
        return tuple(
            name
            for name in type(self).model_fields
            if isinstance(section := getattr(self, name), _Section) and section.provisional
        )
