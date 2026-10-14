"""`festsetzung`: from the zvE before children to festzusetzende ESt, Soli and KiSt (#87). Pure.

The chain #17 calls, in this order: werbungskosten (#15) -> Einkünfte -> minus Entlastungsbetrag
(#81) = GdE -> sonderausgaben (#15) + vorsorge (#16) -> agB (#76, a plain number until built)
-> zvE before children (>= 0) -> progression_amount (#86) -> `festsetzung` -> refund = result
minus Lohnsteuer / Soli / KiSt withheld (#17). § 35a (#77) arrives as a plain number.

Rules: tariff incl. Progressionsvorbehalt (§ 32a, § 32b) -> Günstigerprüfung (§ 31 Satz 4,
`app.tax.kinder`) -> tarifliche ESt incl. the Kindergeld claim of the children with Freibetrag
-> minus § 35a, capped by that tax (§ 35a Abs. 1 to 3, § 2 Abs. 6) -> festzusetzende ESt.
Soli and KiSt are based on the ESt with ALL Freibeträge, without the Kindergeld addition and
after § 35a (§ 51a Abs. 2 EStG, § 3 Abs. 2 SolzG 1995).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal

from app.domain.enums import DeductionNote, FilingStatus
from app.tax.church import (
    ChurchResult,
    KistMethod,
    MemberShare,
    church_tax_joint,
    kappung_unmodelled,
)
from app.tax.deductions.models import ChildInput, Note, ReturnContext, check_decimal
from app.tax.kinder import KinderResult, guenstigerpruefung
from app.tax.models import TaxParams
from app.tax.progression import tariff_with_progression
from app.tax.tariff import church_tax, solidarity_surcharge

_ZERO = Decimal(0)


@dataclass(frozen=True, slots=True)
class Festsetzung:
    kinder: KinderResult
    tarifliche_est_without_kindergeld: Decimal  # ESt(zve_tariff), full euros
    kindergeld_added: Decimal
    tarifliche_est: Decimal  # incl. the Kindergeld addition (§ 31 Satz 4); cents possible
    ermaessigung_applied: Decimal  # § 35a, min(requested, tarifliche_est)
    festzusetzende_est: Decimal
    bmg: Decimal  # Bemessungsgrundlage of Soli / KiSt
    soli: Decimal
    kist: Decimal
    notes: tuple[Note, ...] = field(default=())
    kist_method: KistMethod = KistMethod.FULL


def _non_negative(value: object, name: str) -> Decimal:
    result = check_decimal(value, name)
    if result < 0:
        raise ValueError(f"{name} must not be negative")
    return result


def festsetzung(
    zve_before_children: Decimal,
    ctx: ReturnContext,
    state: str | None,
    lohnersatz: Decimal,
    children: Iterable[ChildInput],
    ermaessigung_35a: Decimal,
    params: TaxParams,
    church_member: MemberShare | None = None,
) -> Festsetzung:
    """`state=None`: no church tax. `lohnersatz` = #86 `progression_amount.total` (0 without).
    `church_member`: joint return with ONE church member (#53); default = every adult is
    charged (KiSt of the whole Bemessungsgrundlage)."""
    if not isinstance(ctx, ReturnContext):
        raise TypeError("ctx must be a ReturnContext")
    if ctx.year != params.year:
        raise ValueError("ctx.year and params.year differ")
    zve = max(check_decimal(zve_before_children, "zve_before_children"), _ZERO)  # losses = 0
    extra = _non_negative(lohnersatz, "lohnersatz")
    erm = _non_negative(ermaessigung_35a, "ermaessigung_35a")
    filing = ctx.filing

    def est_fn(z: Decimal) -> Decimal:
        return tariff_with_progression(
            z, filing, extra, params.tariff, params.progressionsvorbehalt
        )

    k = guenstigerpruefung(zve, children, filing, est_fn, params.kinder, year=ctx.year)
    without_kg = est_fn(k.zve_tariff)
    tarifliche = without_kg + k.kindergeld_added
    applied = min(erm, tarifliche)
    bmg_full = est_fn(k.zve_bmg)
    bmg = bmg_full - min(erm, bmg_full)
    if church_member is not None and state is not None and filing is FilingStatus.JOINT:
        church = church_tax_joint(bmg, k.zve_bmg, church_member, state, params.church_tax)
    else:
        full = church_tax(bmg, state, params.church_tax)
        capped = church_tax(bmg, state, params.church_tax, k.zve_bmg)  # Kappung (#112)
        cap_note = (Note(DeductionNote.KIST_KAPPUNG_APPLIED),) if capped < full else ()
        if state is not None and kappung_unmodelled(state, params.church_tax):
            cap_note = (Note(DeductionNote.KIST_KAPPUNG_NOT_MODELLED),)
        church = ChurchResult(capped, KistMethod.FULL, cap_note)
    return Festsetzung(
        kinder=k,
        tarifliche_est_without_kindergeld=without_kg,
        kindergeld_added=k.kindergeld_added,
        tarifliche_est=tarifliche,
        ermaessigung_applied=applied,
        festzusetzende_est=tarifliche - applied,
        bmg=bmg,
        soli=solidarity_surcharge(bmg, filing, params.soli),
        kist=church.kist,
        notes=k.notes + church.notes,
        kist_method=church.method,
    )
