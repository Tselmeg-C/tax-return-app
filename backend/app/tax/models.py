"""Typed, frozen tax parameters of one year (pure; the YAML is read by `app.tax_params`).

Money and rates are `Decimal`, given in the YAML as quoted strings: an unquoted number
(which YAML would turn into a float or int) is rejected, naming the key.
"""

from __future__ import annotations

from datetime import date
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


class KirchgeldStufe(_Frozen):
    """One Stufe of the besonderes Kirchgeld: from `lower` (zvE, full euros) the `amount` a year."""

    lower: Dec
    amount: Dec


class KirchgeldParams(_Frozen):
    """Besonderes Kirchgeld in glaubensverschiedener Ehe (#53).

    `compare_states`: the Land levies the table amount and the higher of it and the member's
    share of the KiSt is charged. `no_kirchgeld_states`: the Land has no Kirchgeld, only the
    member's share (BY). Any other Land is not modelled: member share + a note.
    """

    stufen: tuple[KirchgeldStufe, ...]
    compare_states: frozenset[str]
    no_kirchgeld_states: frozenset[str] = frozenset()

    @model_validator(mode="after")
    def _check(self) -> Self:
        lowers = [s.lower for s in self.stufen]
        if not self.stufen or lowers != sorted(set(lowers)):
            raise ValueError("kirchgeld.stufen must be non-empty and strictly ascending")
        if any(s.lower < 0 or s.amount <= 0 for s in self.stufen):
            raise ValueError("kirchgeld.stufen: lower >= 0 and amount > 0")
        if any(a.amount >= b.amount for a, b in zip(self.stufen, self.stufen[1:], strict=False)):
            raise ValueError("kirchgeld.stufen: amounts must ascend")
        both = self.compare_states | self.no_kirchgeld_states
        if both - STATES:
            raise ValueError(f"kirchgeld: unknown state {', '.join(sorted(both - STATES))}")
        if self.compare_states & self.no_kirchgeld_states:
            raise ValueError("kirchgeld: a state cannot be in both lists")
        return self


class ChurchTaxParams(_Section):
    rate_by_state: dict[str, Dec]
    kirchgeld: KirchgeldParams

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


AGB_GRADES: tuple[str, ...] = tuple(str(g) for g in range(20, 101, 10))


class ZumutbareBelastungParams(_Frozen):
    """§ 33 Abs. 3 Satz 1 EStG: rates (share of the GdE part in bracket 1 / 2 / 3)."""

    bracket1_upper: Dec
    bracket2_upper: Dec
    rates: dict[str, tuple[Dec, Dec, Dec]]

    @model_validator(mode="after")
    def _check(self) -> Self:
        if not 0 < self.bracket1_upper < self.bracket2_upper:
            raise ValueError("bracket1_upper must be positive and below bracket2_upper")
        if set(self.rates) != set(AGB_RATE_ROWS):
            raise ValueError(f"rates: keys must be exactly {', '.join(AGB_RATE_ROWS)}")
        for row, triple in self.rates.items():
            for i, rate in enumerate(triple, 1):
                if not 0 < rate < 1:
                    raise ValueError(f"rates.{row}[{i}] must be in (0, 1)")
        return self


AGB_RATE_ROWS: tuple[str, ...] = (
    "no_children_single",
    "no_children_joint",
    "one_or_two_children",
    "three_plus_children",
)


class AgbParams(_Section):
    """agB (#76): § 33 Abs. 3 (zumutbare Belastung), § 33b Abs. 3 (Behinderten-Pauschbetrag)."""

    zumutbare_belastung: ZumutbareBelastungParams
    behinderten_pauschbetrag: dict[str, Dec]
    hilflos_blind: Dec

    @model_validator(mode="after")
    def _check(self) -> Self:
        if set(self.behinderten_pauschbetrag) != set(AGB_GRADES):
            raise ValueError("behinderten_pauschbetrag: grades must be 20 to 100 in steps of 10")
        amounts = [self.behinderten_pauschbetrag[g] for g in AGB_GRADES] + [self.hilflos_blind]
        if amounts[0] <= 0 or not all(a < b for a, b in zip(amounts, amounts[1:], strict=False)):
            raise ValueError("behinderten_pauschbetrag must be positive and ascending")
        return self


class ProgressionParams(_Section):
    """§ 32b Abs. 2 EStG (#86). `rate_decimals`: "exact" or a digit string "0".."8"."""

    rate_decimals: str

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.rate_decimals != "exact" and self.rate_decimals not in tuple("012345678"):
            raise ValueError('rate_decimals must be "exact" or a digit string "0" to "8"')
        return self


class KinderParams(_Section):
    """§ 32 Abs. 6 Satz 1, § 66 Abs. 1, § 31 Satz 4 EStG (#87). Freibeträge are per parent."""

    kinderfreibetrag: Dec
    bea_freibetrag: Dec
    kindergeld_per_month: Dec

    @model_validator(mode="after")
    def _check(self) -> Self:
        for name in ("kinderfreibetrag", "bea_freibetrag", "kindergeld_per_month"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        return self


def _quoted_date(value: object) -> date:
    if not isinstance(value, str):
        raise ValueError('must be a quoted ISO date (e.g. "2026-07-31")')
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"not an ISO date: {value!r}") from None


IsoDate = Annotated[date, BeforeValidator(_quoted_date)]


class FilingParams(_Section):
    """Abgabefristen (#92): § 149 Abs. 2 (self) and Abs. 3 (advised) AO for this year."""

    deadline_self: IsoDate
    deadline_advised: IsoDate

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.deadline_advised <= self.deadline_self:
            raise ValueError("deadline_advised must be after deadline_self")
        return self


class HaushaltsnaheParams(_Section):
    """§ 35a Abs. 2 Satz 1 and Abs. 3 EStG (#77): rate of the Arbeitskosten and the two caps."""

    rate: Dec
    max_dienstleistung: Dec
    max_handwerker: Dec

    @model_validator(mode="after")
    def _check(self) -> Self:
        if not 0 < self.rate < 1:
            raise ValueError("rate must be in (0, 1)")
        if self.max_dienstleistung <= 0 or self.max_handwerker <= 0:
            raise ValueError("max_dienstleistung and max_handwerker must be positive")
        return self


class MappingEntry(_Frozen):
    """Where a category goes on the forms (#9). `zeile` null = not mapped / computed elsewhere."""

    anlage: Anlage
    zeile: str | None
    source: str
    provisional: bool = False


MAPPED_CATEGORIES: frozenset[Category] = frozenset(Category) - {Category.IRRELEVANT}


class EntlastungsbetragParams(_Section):
    """§ 24b Abs. 2 EStG (#81): Entlastungsbetrag für Alleinerziehende, per year."""

    base: Dec
    per_further_child: Dec

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.base <= 0 or self.per_further_child <= 0:
            raise ValueError("base and per_further_child must be positive")
        return self


class SozialversicherungParams(_Section):
    """Employee social-insurance rates and limits for the Vorsorge estimate (#85)."""

    bbg_rv: Dec  # Beitragsbemessungsgrenze allgemeine RV and ALV (§ 4 SVBezGrV), per year
    bbg_kv: Dec  # Beitragsbemessungsgrenze KV / PV (§ 2 Abs. 2 SVBezGrV = § 6 Abs. 7 SGB V)
    rv_rate: Dec  # total rate allgemeine RV (§ 158 SGB VI, Bekanntmachung); employee pays half
    alv_rate: Dec  # total rate ALV (§ 341 Abs. 2 SGB III); employee pays half
    kv_rate: Dec  # allgemeiner Beitragssatz (§ 241 SGB V); employee pays half
    kv_zusatz_average: (
        Dec  # durchschnittlicher Zusatzbeitragssatz (§ 242a SGB V); employee pays half
    )
    pv_rate: Dec  # total rate (§ 55 Abs. 1 SGB XI); employee pays half
    pv_childless_surcharge: Dec  # § 55 Abs. 3 Satz 1, employee only
    pv_child_abschlag: Dec  # § 55 Abs. 3 Satz 3, per child from the 2nd to the 5th under 25
    pv_abschlag_max_steps: int  # 4 = children 2 to 5
    uebergangsbereich_upper_monthly: Dec  # § 20 Abs. 2 SGB IV; at or below x 12: no estimate

    @model_validator(mode="after")
    def _check(self) -> Self:
        for name in ("bbg_rv", "bbg_kv", "uebergangsbereich_upper_monthly"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        for name in (
            "rv_rate",
            "alv_rate",
            "kv_rate",
            "kv_zusatz_average",
            "pv_rate",
            "pv_childless_surcharge",
            "pv_child_abschlag",
        ):
            _rate(name, getattr(self, name))
        if self.pv_abschlag_max_steps < 0:
            raise ValueError("pv_abschlag_max_steps must not be negative")
        return self


class TaxParams(_Frozen):
    year: int
    tariff: TariffParams
    soli: SoliParams
    church_tax: ChurchTaxParams
    werbungskosten: WerbungskostenParams
    sonderausgaben: SonderausgabenParams
    vorsorge: VorsorgeParams
    progressionsvorbehalt: ProgressionParams
    agb: AgbParams
    kinder: KinderParams
    filing: FilingParams
    haushaltsnahe: HaushaltsnaheParams
    entlastungsbetrag: EntlastungsbetragParams
    sozialversicherung: SozialversicherungParams
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
