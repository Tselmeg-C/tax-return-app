"""`build_summary`: the one place the refund estimate is computed (web `GET /summary`, bot #11).

Loads the household's year through `HouseholdScope`, builds the engine DTOs (ids as `str`, a
real `FilingStatus` member), calls the pure engines of `app.tax` in the order of #17 and
returns a JSON-ready dict (money as dot-decimal strings, codes only). Inputs that are not
entered count as 0 and show up in `what_missing`.

Never log or store a name, an amount in a log line, a document or a Steuer-ID: the log line
carries the year, the status and counts. The `estimate` row is a snapshot per inputs hash
(`INSERT ... ON CONFLICT DO NOTHING`); it is never served, so a code change cannot return an
old result.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

import structlog
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert

from app.db.models import (
    ChildYear,
    Document,
    Employment,
    Estimate,
    Lohnersatz,
    Person,
    PersonVorsorge,
    TaxItem,
    TaxProfile,
)
from app.db.scope import HouseholdScope
from app.domain.enums import (
    CATEGORY_GROUP,
    AllowanceShare,
    Anlage,
    CategoryGroup,
    DeductionNote,
    DocumentStatus,
    EstimateMissing,
    PersonKind,
    Religion,
)
from app.estimate.deadlines import next_working_day
from app.tax.assessment import festsetzung
from app.tax.deductions import (
    ChildInput,
    EmploymentInput,
    ItemInput,
    Note,
    PersonInput,
    ReturnContext,
    VorsorgeInput,
    agb,
    haushaltsnahe,
    sonderausgaben,
    vorsorge,
    vorsorge_from_items,
    werbungskosten,
)
from app.tax.deductions.select import EXCLUDED_REASONS
from app.tax.pflicht import PflichtEmployment, PflichtPerson, pflichtveranlagung
from app.tax.progression import LohnersatzInput, progression_amount
from app.tax_params import load_params, params_version

log = structlog.stdlib.get_logger("app.estimate")

_ZERO = Decimal(0)
_CENT = Decimal("0.01")

# Vorsorge fields of `employment` per group; a group with any field set replaces the matching
# item categories entirely (never both, or the contributions would count twice).
_RV = ("rv_employee", "rv_employer")
_KVPV = ("kv", "pv")


def _m(value: Decimal) -> str:
    result = value.quantize(_CENT, rounding=ROUND_HALF_EVEN)
    return f"{abs(result) if result == 0 else result:.2f}"


def _sum(values: Any) -> Decimal:
    return sum(values, _ZERO)


@dataclass(frozen=True)
class _Loaded:
    profile: TaxProfile
    persons: dict[str, Person]
    employments: list[Employment]
    children: list[ChildYear]
    lohnersatz: dict[str, Decimal]
    person_vorsorge: dict[str, PersonVorsorge]
    items: list[ItemInput]
    attention_count: int


async def _load(scope: HouseholdScope, year: int, profile: TaxProfile) -> _Loaded:
    session = scope.session
    in_return = {profile.taxpayer_person_id}
    if profile.spouse_person_id is not None:
        in_return.add(profile.spouse_person_id)

    async def rows(model: Any, *where: Any) -> list[Any]:
        return list((await session.execute(scope.select(model).where(*where))).scalars().all())

    persons = {str(p.id): p for p in await rows(Person)}
    employments = [
        e
        for e in await rows(Employment, Employment.year == year)
        if e.person_id in in_return  # persons outside the return are ignored
    ]
    employments.sort(key=lambda e: (e.created_at, str(e.id)))
    children = await rows(ChildYear, ChildYear.year == year)
    lohnersatz = {
        str(r.person_id): r.amount
        for r in await rows(Lohnersatz, Lohnersatz.year == year)
        if r.person_id in in_return
    }
    person_vorsorge = {
        str(r.person_id): r
        for r in await rows(PersonVorsorge, PersonVorsorge.year == year)
        if r.person_id in in_return
    }
    docs = {
        d.id: d.attention_reason
        for d in await rows(Document, Document.status == DocumentStatus.NEEDS_ATTENTION)
    }
    items = [
        ItemInput(
            id=str(i.id),
            person_id=None if i.person_id is None else str(i.person_id),
            category=i.category,
            year=i.year,
            deductible_amount=i.deductible_amount,
            labour_share_35a=i.labour_share_35a,
            payment_method=i.payment_method,
            is_relevant=i.is_relevant,
            overridden_by_user=i.overridden_by_user,
            attention_reason=docs.get(i.document_id) if i.document_id is not None else None,
        )
        for i in sorted(await rows(TaxItem, TaxItem.year == year), key=lambda i: str(i.id))
    ]
    return _Loaded(
        profile, persons, employments, children, lohnersatz, person_vorsorge, items, len(docs)
    )


def _vorsorge_inputs(
    ctx: ReturnContext,
    loaded: _Loaded,
    employed: frozenset[str],
    notes: list[Note],
) -> tuple[list[VorsorgeInput], list[str]]:
    """Per person and group: person-level row > employment fields > items (never summed)."""
    from_items, item_notes = vorsorge_from_items(ctx, loaded.items, employed)
    by_person: dict[str, list[Employment]] = {}
    for e in loaded.employments:
        by_person.setdefault(str(e.person_id), []).append(e)

    def manual(pid: str, name: str) -> Decimal | None:
        values = [v for e in by_person.get(pid, []) if (v := getattr(e, name)) is not None]
        return _sum(values) if values else None

    def group(pid: str, *names: str) -> dict[str, Decimal | None]:
        row = loaded.person_vorsorge.get(pid)
        if row is not None and any(getattr(row, n) is not None for n in names):
            return {n: getattr(row, n) for n in names}
        return {n: manual(pid, n) for n in names}

    result: list[VorsorgeInput] = []
    kvpv_manual: set[str] = set()
    missing: list[str] = []
    for base in from_items:
        pid = base.person_id
        rv = group(pid, *_RV)
        kvpv = group(pid, *_KVPV)
        ruerup = group(pid, "basisrente")["basisrente"]
        sonst = group(pid, "sonstige_vorsorge")["sonstige_vorsorge"]
        use_rv = any(v is not None for v in rv.values())
        use_kv = any(v is not None for v in kvpv.values())
        if use_kv:
            kvpv_manual.add(pid)
        final = VorsorgeInput(
            person_id=pid,
            employed=base.employed,
            rv_employee=(rv["rv_employee"] or _ZERO) if use_rv else base.rv_employee,
            rv_employer=rv["rv_employer"] if use_rv else base.rv_employer,
            basisrente=ruerup if ruerup is not None else base.basisrente,
            kv=(kvpv["kv"] or _ZERO) if use_kv else base.kv,
            pv=(kvpv["pv"] or _ZERO) if use_kv else base.pv,
            sonstige=sonst if sonst is not None else base.sonstige,
        )
        result.append(final)
        if not any((final.rv_employee, final.basisrente, final.kv, final.pv, final.sonstige)):
            missing.append(pid)
    # A manual KV / PV amount replaces the items, so their "not split" hint no longer applies.
    notes.extend(
        n
        for n in item_notes
        if not (n.code is DeductionNote.KV_PV_UNSPLIT and n.person_id in kvpv_manual)
    )
    return result, missing


def _child_inputs(loaded: _Loaded) -> list[ChildInput]:
    kids: list[ChildInput] = []
    for row in loaded.children:
        person = loaded.persons.get(str(row.person_id))
        if person is None or person.kind is not PersonKind.CHILD or person.dob is None:
            continue
        kids.append(
            ChildInput(
                person_id=str(person.id),
                dob=person.dob,
                disability_grade=person.disability_grade,
                months=row.months,
                allowance_half=row.allowance_share is AllowanceShare.HALF,
                in_household=row.in_household,
            )
        )
    return sorted(kids, key=lambda c: c.person_id)


def _by_anlage(ctx: ReturnContext, loaded: _Loaded, mapping: Any, kids: set[str]) -> list[Any]:
    """Receipt sums per Anlage (relevant, not excluded, person in the return or household)."""
    totals: dict[Anlage, Decimal] = {}
    for item in loaded.items:
        if not item.is_relevant or item.category not in mapping:
            continue
        if item.person_id is not None and item.person_id not in ctx.person_ids | kids:
            continue
        if not item.overridden_by_user and item.attention_reason in EXCLUDED_REASONS:
            continue
        anlage = mapping[item.category].anlage
        totals[anlage] = totals.get(anlage, _ZERO) + item.deductible_amount
    return [{"anlage": a.value, "amount": _m(totals[a])} for a in Anlage if a in totals]


def _hash(parts: dict[str, Any]) -> str:
    text = json.dumps(parts, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def _present(core: dict[str, Any], today: date) -> dict[str, Any]:
    """Add what depends on `today` (§ 108 Abs. 3 AO shift, overdue); `core` is what is stored."""
    result = dict(core)
    pflicht = core.get("pflicht")
    if pflicht is not None:
        statutory = date.fromisoformat(pflicht["deadline_statutory"])
        shifted = next_working_day(statutory)
        result["pflicht"] = {
            **pflicht,
            "deadline": shifted.isoformat(),
            "overdue": today > shifted,
        }
    return result


async def build_summary(scope: HouseholdScope, year: int, today: date) -> dict[str, Any]:
    """The estimate of `year`. `UnsupportedTaxYear` (no params file) is raised as is.

    `status: "no_profile"` (no `tax_profile` for the year) carries `null` for every amount and
    for `pflicht`; otherwise `status: "ok"`.
    """
    params = load_params(year)
    version = params_version(year)
    provisional = list(params.provisional_sections)
    base: dict[str, Any] = {
        "year": year,
        "params_version": version,
        "provisional": bool(provisional),
        "provisional_sections": provisional,
    }
    session = scope.session
    profile = (
        await session.execute(scope.select(TaxProfile).where(TaxProfile.year == year))
    ).scalar_one_or_none()
    if profile is None:
        attention = await session.scalar(
            scope.select(Document)
            .with_only_columns(func.count(Document.id))
            .where(Document.status == DocumentStatus.NEEDS_ATTENTION)
        )
        log.info("estimate.built", year=year, status="no_profile")
        return {
            **base,
            "status": "no_profile",
            **dict.fromkeys(_AMOUNT_KEYS),
            "withheld": None,
            "by_anlage": [],
            "pflicht": None,
            "what_missing": [],
            "notes": [],
            "attention_count": int(attention or 0),
        }

    loaded = await _load(scope, year, profile)
    taxpayer = str(profile.taxpayer_person_id)
    spouse = None if profile.spouse_person_id is None else str(profile.spouse_person_id)
    ctx = ReturnContext(year, profile.filing_status, taxpayer, spouse)
    ids = sorted(ctx.person_ids)
    adults = [loaded.persons[i] for i in ids]
    state = (
        None
        if all(p.religion is Religion.NONE for p in adults)
        else profile.bundesland.value.upper()
    )
    kids = _child_inputs(loaded)
    kid_ids = {k.person_id for k in kids}
    people = [
        PersonInput(i, p.disability_grade, p.merkzeichen_h_bl_tbl)
        for i, p in sorted(loaded.persons.items())
        if i in ctx.person_ids or i in kid_ids
    ]

    wage: dict[str, Decimal] = dict.fromkeys(ids, _ZERO)
    for e in loaded.employments:
        wage[str(e.person_id)] += e.gross_wage or _ZERO
    employment_inputs = [
        EmploymentInput(str(e.person_id), e.commute_km, e.office_days, e.homeoffice_days)
        for e in loaded.employments
    ]
    employed = frozenset(str(e.person_id) for e in loaded.employments)

    notes: list[Note] = []
    missing: list[tuple[EstimateMissing, str | None]] = []

    # 3. Werbungskosten -> Einkünfte aus nichtselbständiger Arbeit -> GdE.
    wk = werbungskosten(ctx, loaded.items, employment_inputs, params.werbungskosten)
    notes.extend(wk.notes)
    einkuenfte = _ZERO
    wk_on_wages: dict[str, Decimal] = {}
    for pid, w in wk.by_person.items():
        # § 9a: only the Pauschbetrag is limited by the wages; real costs may exceed them.
        pauschbetrag_part = w.applied - w.beside_pauschbetrag
        limited = (
            min(pauschbetrag_part, wage[pid]) if w.used_pauschbetrag else pauschbetrag_part
        ) + w.beside_pauschbetrag
        einkuenfte += wage[pid] - limited
        wk_on_wages[pid] = min(wage[pid], pauschbetrag_part)
    gde = einkuenfte  # + V, KAP = 0 (#19 / #20), - Entlastungsbetrag = 0 (#81)

    # 4. Sonderausgaben, Vorsorge, agB -> zvE before children.
    withheld_kist = _sum(e.kirchensteuer_withheld or _ZERO for e in loaded.employments)
    sa = sonderausgaben(ctx, loaded.items, kids, gde, withheld_kist, params.sonderausgaben)
    notes.extend(sa.notes)
    vorsorge_inputs, vorsorge_missing = _vorsorge_inputs(ctx, loaded, employed, notes)
    vs = vorsorge(ctx, vorsorge_inputs, params.vorsorge)
    notes.extend(vs.notes)
    ag = agb(ctx, loaded.items, people, kids, gde, params.agb)
    notes.extend(ag.notes)
    zve_before = max(_ZERO, gde - sa.applied - vs.total - ag.total)

    # 5. Progressionsvorbehalt from the Lohnersatz.
    benefits = [
        LohnersatzInput(pid, amount, wk_on_wages.get(pid, _ZERO))
        for pid, amount in sorted(loaded.lohnersatz.items())
    ]
    progression = progression_amount(ctx, benefits, params.werbungskosten.arbeitnehmer_pauschbetrag)
    notes.extend(progression.notes)

    # 6. § 35a, 7. Festsetzung.
    erm = haushaltsnahe(ctx, loaded.items, params.haushaltsnahe)
    notes.extend(erm.notes)
    fest = festsetzung(zve_before, ctx, state, progression.total, kids, erm.total, params)
    notes.extend(fest.notes)

    # 8. Refund: positive = Erstattung. Kindergeld is not part of it (it was paid out).
    withheld = {
        "lohnsteuer": _sum(e.lohnsteuer or _ZERO for e in loaded.employments),
        "soli": _sum(e.soli_withheld or _ZERO for e in loaded.employments),
        "kirchensteuer": withheld_kist,
    }
    owed = fest.festzusetzende_est + fest.soli + fest.kist
    refund = _sum(withheld.values()) - owed

    # 9. Pflichtveranlagung (Nr. 3a: only persons with wages; an employment whose wage is not
    # entered yet counts, so the check never says "voluntary" because of missing input).
    pflicht = pflichtveranlagung(
        ctx,
        [
            PflichtEmployment(str(e.person_id), e.steuerklasse, e.has_factor)
            for e in loaded.employments
            if e.gross_wage is None or e.gross_wage > 0
        ],
        [PflichtPerson(pid, loaded.lohnersatz.get(pid, _ZERO), None) for pid in ids],
        today,
        params,
    )

    # What is missing.
    for pid in ids:
        mine = [e for e in loaded.employments if str(e.person_id) == pid]
        if any(e.gross_wage is None or e.lohnsteuer is None for e in mine):
            missing.append((EstimateMissing.NO_LSTB, pid))
    missing.extend((EstimateMissing.VORSORGE_MISSING, pid) for pid in vorsorge_missing)
    if any(
        i.is_relevant
        and CATEGORY_GROUP[i.category] in (CategoryGroup.KAPITAL, CategoryGroup.VERMIETUNG)
        for i in loaded.items
    ):
        missing.append((EstimateMissing.KAP_V_NOT_SUPPORTED, None))
    if any(n.code is DeductionNote.PERSON_UNASSIGNED for n in notes):
        missing.append((EstimateMissing.PERSON_UNASSIGNED, None))
    unique_notes = sorted(
        set(notes), key=lambda n: (n.code.value, n.item_id or "", n.person_id or "")
    )

    core: dict[str, Any] = {
        **base,
        "status": "ok",
        "refund": _m(refund),
        "zve": _m(fest.kinder.zve_tariff),
        "zve_before_children": _m(zve_before),
        "gde": _m(gde),
        "tarifliche_est": _m(fest.tarifliche_est),
        "ermaessigung_35a": _m(fest.ermaessigung_applied),
        "festzusetzende_est": _m(fest.festzusetzende_est),
        "soli": _m(fest.soli),
        "kirchensteuer": _m(fest.kist),
        "kindergeld_added": _m(fest.kindergeld_added),
        "withheld": {k: _m(v) for k, v in withheld.items()},
        "by_anlage": _by_anlage(ctx, loaded, params.mapping, kid_ids),
        "pflicht": {
            "state": pflicht.state.value,
            "reasons": [r.value for r in pflicht.reasons],
            "not_checked": [c.value for c in pflicht.not_checked],
            "deadline_statutory": pflicht.deadline.isoformat(),
            "deadline_kind": pflicht.deadline_kind.value,
        },
        "what_missing": [{"code": c.value, "person_id": p} for c, p in missing],
        "notes": [
            {"code": n.code.value, "item_id": n.item_id, "person_id": n.person_id}
            for n in unique_notes
        ],
        "attention_count": loaded.attention_count,
    }

    inputs_hash = _hash(
        {
            "ctx": asdict(ctx),
            "state": state,
            "params_version": version,
            "items": [asdict(i) for i in loaded.items],
            "employments": [
                {k: getattr(e, k) for k in _EMPLOYMENT_HASHED} | {"id": str(e.id)}
                for e in loaded.employments
            ],
            "children": [asdict(k) for k in kids],
            "persons": [asdict(p) for p in people],
            "religions": [p.religion.value for p in adults],
            "lohnersatz": loaded.lohnersatz,
            "attention_count": loaded.attention_count,
        }
    )
    await session.execute(
        insert(Estimate)
        .values(
            household_id=scope.household_id,
            year=year,
            params_version=version,
            inputs_hash=inputs_hash,
            result=core,
        )
        .on_conflict_do_nothing(index_elements=["household_id", "year", "inputs_hash"])
    )
    await session.commit()
    log.info(
        "estimate.built",
        year=year,
        status="ok",
        items=len(loaded.items),
        notes=len(unique_notes),
        missing=len(missing),
    )
    return _present(core, today)


_AMOUNT_KEYS = (
    "refund",
    "zve",
    "zve_before_children",
    "gde",
    "tarifliche_est",
    "ermaessigung_35a",
    "festzusetzende_est",
    "soli",
    "kirchensteuer",
    "kindergeld_added",
)
_EMPLOYMENT_HASHED = (
    "person_id",
    "steuerklasse",
    "has_factor",
    "commute_km",
    "office_days",
    "homeoffice_days",
    "gross_wage",
    "lohnsteuer",
    "soli_withheld",
    "kirchensteuer_withheld",
    "rv_employee",
    "rv_employer",
    "basisrente",
    "kv",
    "pv",
    "sonstige_vorsorge",
)
