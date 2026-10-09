"""Household writes (#13): persons, the per-year tax profile, employments, child rows, copy.

Every write is one transaction with exactly one audit row per changed row (actor = the
user); a refusal rolls the session back and raises `Rejected`. Log events carry ids, years,
codes and changed field *names* only, never names, dates of birth, employers or the Steuer-ID
(which is write-only and leaves this module only masked, see `validation.mask_steuer_id`).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

import structlog
from sqlalchemy import case, func, or_

from app.db.audit import Actor, record, snapshot
from app.db.base import Base
from app.db.models import AppUser, ChildYear, Employment, Person, TaxItem, TaxProfile
from app.db.scope import HouseholdScope
from app.domain.enums import (
    AllowanceShare,
    AuditAction,
    Bundesland,
    FilingStatus,
    PersonKind,
    Religion,
    Steuerklasse,
)
from app.household.validation import (
    Rejected,
    check_disability_grade,
    check_dob,
    check_employment,
    clean_employer_name,
    clean_first_name,
    clean_last_name,
    invalid,
    max_months,
    normalise_steuer_id,
)
from app.tax_params import supported_years

log = structlog.stdlib.get_logger("app.household")

NOT_FOUND = "not_found"


def check_year(year: int, field: str = "year") -> None:
    if year not in supported_years():
        raise invalid("unsupported_year", field)


async def _audit(
    scope: HouseholdScope,
    actor: Actor,
    row: Base,
    action: AuditAction,
    before: dict[str, Any] | None = None,
) -> None:
    after = None if action is AuditAction.DELETE else snapshot(row)
    await record(
        scope.session,
        household_id=scope.household_id,
        entity=row.__tablename__,
        entity_id=row.id,  # type: ignore[attr-defined]
        action=action,
        before=before if action is not AuditAction.DELETE else snapshot(row),
        after=after,
        actor=actor,
    )


async def _transaction[T](scope: HouseholdScope, work: Callable[[], Awaitable[T]]) -> T:
    """Run `work`, commit; roll back on any error (`Rejected` is logged by code only)."""
    try:
        result = await work()
        await scope.session.commit()
    except BaseException as exc:
        await scope.session.rollback()
        if isinstance(exc, Rejected):
            log.info("household.rejected", code=exc.code)
        raise
    return result


async def _all[M: Base](scope: HouseholdScope, model: type[M], *where: Any) -> list[M]:
    stmt = scope.select(model).where(*where)
    return list((await scope.session.execute(stmt)).scalars().all())


async def _profile(scope: HouseholdScope, year: int) -> TaxProfile | None:
    rows = await _all(scope, TaxProfile, TaxProfile.year == year)
    return rows[0] if rows else None


def _in_return(profile: TaxProfile | None) -> set[uuid.UUID]:
    if profile is None:
        return set()
    return {p for p in (profile.taxpayer_person_id, profile.spouse_person_id) if p is not None}


def _months_limit(person: Person, year: int) -> int | None:
    assert person.dob is not None  # children always have a dob (CHECK + validation)
    return max_months(person.dob, year, has_disability=person.disability_grade is not None)


# --- persons -------------------------------------------------------------------------------

PERSON_FIELDS = (
    "first_name",
    "last_name",
    "dob",
    "steuer_id",
    "religion",
    "disability_grade",
    "merkzeichen_h_bl_tbl",
)


async def _check_duplicate_steuer_id(
    scope: HouseholdScope, steuer_id: str, exclude: uuid.UUID | None
) -> None:
    # Encrypted: compared in memory after decrypting the household's persons, never in SQL.
    for other in await _all(scope, Person):
        if other.id != exclude and other.steuer_id == steuer_id:
            raise invalid("duplicate_steuer_id", "steuer_id")


async def _clean_person(
    scope: HouseholdScope,
    values: dict[str, Any],
    *,
    kind: PersonKind,
    today: date,
    exclude: uuid.UUID | None,
    steuer_id_sent: bool,
) -> dict[str, Any]:
    values["first_name"] = clean_first_name(values.get("first_name"))
    values["last_name"] = clean_last_name(values.get("last_name"))
    check_dob(values.get("dob"), is_child=kind is PersonKind.CHILD, today=today)
    check_disability_grade(values.get("disability_grade"))
    if values.get("religion") is None:
        values["religion"] = Religion.NONE
    if not isinstance(values.get("merkzeichen_h_bl_tbl"), bool):
        raise invalid("invalid_merkzeichen", "merkzeichen_h_bl_tbl")
    if steuer_id_sent and values.get("steuer_id") is not None:
        values["steuer_id"] = normalise_steuer_id(values["steuer_id"])
        await _check_duplicate_steuer_id(scope, values["steuer_id"], exclude)
    return values


async def create_person(
    scope: HouseholdScope,
    user_id: uuid.UUID,
    kind: PersonKind,
    sent: dict[str, Any],
    *,
    link_to_me: bool,
    today: date,
) -> Person:
    actor = Actor.user(user_id)

    async def work() -> Person:
        values = {key: sent.get(key) for key in PERSON_FIELDS}
        values["merkzeichen_h_bl_tbl"] = sent.get("merkzeichen_h_bl_tbl", False)
        values = await _clean_person(
            scope, values, kind=kind, today=today, exclude=None, steuer_id_sent=True
        )
        user = None
        if link_to_me:
            user = await scope.get(AppUser, user_id)
            if user is None or user.person_id is not None:
                raise Rejected(409, "already_linked")
        person = Person(kind=kind, **values)
        scope.add(person)
        await scope.session.flush()
        await _audit(scope, actor, person, AuditAction.CREATE)
        if user is not None:
            before = snapshot(user)
            user.person_id = person.id
            await scope.session.flush()
            await _audit(scope, actor, user, AuditAction.UPDATE, before)
        return person

    person = await _transaction(scope, work)
    log.info(
        "household.person_created",
        person_id=str(person.id),
        fields=sorted(k for k in PERSON_FIELDS if sent.get(k) is not None),
        linked=link_to_me,
    )
    return person


async def update_person(
    scope: HouseholdScope,
    user_id: uuid.UUID,
    person_id: uuid.UUID,
    sent: dict[str, Any],
    *,
    today: date,
) -> Person:
    """Merge semantics: absent = unchanged; `steuer_id: None` removes it."""
    actor = Actor.user(user_id)
    changed: list[str] = []

    async def work() -> Person:
        person = await scope.get(Person, person_id)
        if person is None:
            raise Rejected(404, NOT_FOUND)
        if "kind" in sent:
            raise invalid("kind_immutable", "kind")
        current = {key: getattr(person, key) for key in PERSON_FIELDS}
        values = {**current, **{k: v for k, v in sent.items() if k in PERSON_FIELDS}}
        values = await _clean_person(
            scope,
            values,
            kind=person.kind,
            today=today,
            exclude=person.id,
            steuer_id_sent="steuer_id" in sent,
        )
        changed.extend(k for k in PERSON_FIELDS if values[k] != current[k])
        if person.kind is PersonKind.CHILD and {"dob", "disability_grade"} & set(changed):
            field = "dob" if "dob" in changed else "disability_grade"
            await _recheck_child_rows(scope, person, values, field)
        before = snapshot(person)
        for key in changed:
            setattr(person, key, values[key])
        await scope.session.flush()
        await _audit(scope, actor, person, AuditAction.UPDATE, before)
        return person

    person = await _transaction(scope, work)
    log.info("household.person_updated", person_id=str(person.id), fields=sorted(changed))
    return person


async def _recheck_child_rows(
    scope: HouseholdScope, person: Person, values: dict[str, Any], field: str
) -> None:
    """A new dob / disability grade must keep every stored `child_year` row valid."""
    probe = Person(kind=person.kind, dob=values["dob"], disability_grade=values["disability_grade"])
    for row in await _all(scope, ChildYear, ChildYear.person_id == person.id):
        limit = _months_limit(probe, row.year)
        if limit is None:
            raise invalid("child_not_born", field)
        if row.months > limit:
            raise invalid("months_exceed", field)


async def delete_person(scope: HouseholdScope, user_id: uuid.UUID, person_id: uuid.UUID) -> None:
    """Decision 8: never with tax items or a profile; employment / child rows go with it."""
    actor = Actor.user(user_id)
    removed: dict[str, int] = {}

    async def work() -> None:
        session = scope.session
        person = await scope.get(Person, person_id)
        if person is None:
            raise Rejected(404, NOT_FOUND)
        count = await session.scalar(
            scope.select(TaxItem)
            .with_only_columns(func.count(TaxItem.id))
            .where(TaxItem.person_id == person.id)
        )
        if count:
            raise Rejected(409, "person_has_tax_items", count=int(count))
        profiles = await _all(
            scope,
            TaxProfile,
            or_(
                TaxProfile.taxpayer_person_id == person.id,
                TaxProfile.spouse_person_id == person.id,
            ),
        )
        if profiles:
            raise Rejected(409, "person_in_profile", years=sorted(p.year for p in profiles))
        for model in (Employment, ChildYear):
            rows = await _all(scope, model, model.person_id == person.id)
            removed[model.__tablename__] = len(rows)
            for row in rows:
                await _audit(scope, actor, row, AuditAction.DELETE)
                await session.delete(row)
        await session.flush()  # dependent rows before the person (no ORM relationships)
        for user in await _all(scope, AppUser, AppUser.person_id == person.id):
            before = snapshot(user)
            user.person_id = None
            await session.flush()
            await _audit(scope, actor, user, AuditAction.UPDATE, before)
        await _audit(scope, actor, person, AuditAction.DELETE)
        await session.delete(person)
        await session.flush()

    await _transaction(scope, work)
    log.info("household.person_deleted", person_id=str(person_id), **removed)


# --- profile -------------------------------------------------------------------------------


@dataclass
class ProfileInput:
    filing_status: FilingStatus
    bundesland: Bundesland
    taxpayer_person_id: uuid.UUID
    spouse_person_id: uuid.UUID | None


async def _adult(scope: HouseholdScope, person_id: uuid.UUID, field: str) -> Person:
    person = await scope.get(Person, person_id)
    if person is None:
        raise invalid("unknown_person", field)
    if person.kind is not PersonKind.ADULT:
        raise invalid("person_must_be_adult", field)
    return person


async def put_profile(
    scope: HouseholdScope, user_id: uuid.UUID, year: int, data: ProfileInput
) -> TaxProfile:
    actor = Actor.user(user_id)

    async def work() -> TaxProfile:
        check_year(year)
        joint = data.filing_status is FilingStatus.JOINT
        if joint and data.spouse_person_id is None:
            raise invalid("spouse_required", "spouse_person_id")
        if not joint and data.spouse_person_id is not None:
            raise invalid("spouse_not_allowed", "spouse_person_id")
        await _adult(scope, data.taxpayer_person_id, "taxpayer_person_id")
        if data.spouse_person_id is not None:
            await _adult(scope, data.spouse_person_id, "spouse_person_id")
            if data.spouse_person_id == data.taxpayer_person_id:
                raise invalid("spouse_same_person", "spouse_person_id")
        values = {
            "filing_status": data.filing_status,
            "bundesland": data.bundesland,
            "taxpayer_person_id": data.taxpayer_person_id,
            "spouse_person_id": data.spouse_person_id,
        }
        profile = await _profile(scope, year)
        if profile is None:
            profile = TaxProfile(year=year, **values)
            scope.add(profile)
            await scope.session.flush()
            await _audit(scope, actor, profile, AuditAction.CREATE)
        else:
            before = snapshot(profile)
            for key, value in values.items():
                setattr(profile, key, value)
            await scope.session.flush()
            await _audit(scope, actor, profile, AuditAction.UPDATE, before)
        return profile

    profile = await _transaction(scope, work)
    log.info("household.profile_saved", year=year, filing_status=profile.filing_status.value)
    return profile


# --- employments ---------------------------------------------------------------------------

EMPLOYMENT_FIELDS = (
    "employer_name",
    "steuerklasse",
    "has_factor",
    "commute_km",
    "office_days",
    "homeoffice_days",
)
EMPLOYMENT_DEFAULTS: dict[str, Any] = {
    "has_factor": False,
    "commute_km": None,
    "office_days": 0,
    "homeoffice_days": 0,
}


async def _check_employment(
    scope: HouseholdScope, person_id: uuid.UUID, year: int, values: dict[str, Any], exclude: Any
) -> dict[str, Any]:
    profile = await _profile(scope, year)
    if profile is None:
        raise invalid("profile_required", "year")
    if person_id not in _in_return(profile):
        raise invalid("person_not_in_return", "person_id")
    values["employer_name"] = clean_employer_name(values["employer_name"])
    for key in ("steuerklasse", "has_factor", "office_days", "homeoffice_days"):
        if values[key] is None:
            raise invalid("invalid_request", key)
    others = await _all(
        scope,
        Employment,
        Employment.person_id == person_id,
        Employment.year == year,
        Employment.id != exclude,
    )
    check_employment(
        year=year,
        steuerklasse=Steuerklasse(values["steuerklasse"]).value,
        has_factor=values["has_factor"],
        commute_km=values["commute_km"],
        office_days=values["office_days"],
        homeoffice_days=values["homeoffice_days"],
        other_days=sum(e.office_days + e.homeoffice_days for e in others),
    )
    return values


async def create_employment(
    scope: HouseholdScope, user_id: uuid.UUID, person_id: uuid.UUID, year: int, sent: dict[str, Any]
) -> Employment:
    actor = Actor.user(user_id)

    async def work() -> Employment:
        check_year(year)
        if await scope.get(Person, person_id) is None:
            raise invalid("unknown_person", "person_id")
        values = {**EMPLOYMENT_DEFAULTS, **sent}
        values = await _check_employment(scope, person_id, year, values, exclude=None)
        row = Employment(person_id=person_id, year=year, **values)
        scope.add(row)
        await scope.session.flush()
        await _audit(scope, actor, row, AuditAction.CREATE)
        return row

    row = await _transaction(scope, work)
    log.info("household.employment_created", employment_id=str(row.id), year=year)
    return row


async def update_employment(
    scope: HouseholdScope, user_id: uuid.UUID, employment_id: uuid.UUID, sent: dict[str, Any]
) -> Employment:
    actor = Actor.user(user_id)
    changed: list[str] = []

    async def work() -> Employment:
        row = await scope.get(Employment, employment_id)
        if row is None:
            raise Rejected(404, NOT_FOUND)
        check_year(row.year)
        current = {key: getattr(row, key) for key in EMPLOYMENT_FIELDS}
        values = await _check_employment(
            scope, row.person_id, row.year, {**current, **sent}, exclude=row.id
        )
        changed.extend(k for k in EMPLOYMENT_FIELDS if values[k] != current[k])
        before = snapshot(row)
        for key in changed:
            setattr(row, key, values[key])
        await scope.session.flush()
        await _audit(scope, actor, row, AuditAction.UPDATE, before)
        return row

    row = await _transaction(scope, work)
    log.info("household.employment_updated", employment_id=str(row.id), fields=sorted(changed))
    return row


async def delete_employment(
    scope: HouseholdScope, user_id: uuid.UUID, employment_id: uuid.UUID
) -> None:
    actor = Actor.user(user_id)

    async def work() -> None:
        row = await scope.get(Employment, employment_id)
        if row is None:
            raise Rejected(404, NOT_FOUND)
        await _audit(scope, actor, row, AuditAction.DELETE)
        await scope.session.delete(row)
        await scope.session.flush()

    await _transaction(scope, work)
    log.info("household.employment_deleted", employment_id=str(employment_id))


# --- children ------------------------------------------------------------------------------


@dataclass
class ChildYearInput:
    months: int
    allowance_share: AllowanceShare
    in_household: bool


async def _child(scope: HouseholdScope, person_id: uuid.UUID) -> Person:
    person = await scope.get(Person, person_id)
    if person is None:
        raise Rejected(404, NOT_FOUND)
    return person


async def put_child_year(
    scope: HouseholdScope,
    user_id: uuid.UUID,
    year: int,
    person_id: uuid.UUID,
    data: ChildYearInput,
) -> ChildYear:
    actor = Actor.user(user_id)

    async def work() -> ChildYear:
        check_year(year)
        person = await _child(scope, person_id)
        if person.kind is not PersonKind.CHILD:
            raise invalid("not_a_child", "person_id")
        limit = _months_limit(person, year)
        if limit is None:
            raise invalid("child_not_born", "person_id")
        if limit == 0:
            raise invalid("child_too_old", "person_id")
        if not 0 <= data.months <= limit:
            raise invalid("months_exceed", "months")
        values = {
            "months": data.months,
            "allowance_share": data.allowance_share,
            "in_household": data.in_household,
        }
        rows = await _all(
            scope, ChildYear, ChildYear.person_id == person.id, ChildYear.year == year
        )
        if not rows:
            row = ChildYear(person_id=person.id, year=year, **values)
            scope.add(row)
            await scope.session.flush()
            await _audit(scope, actor, row, AuditAction.CREATE)
            return row
        row = rows[0]
        before = snapshot(row)
        for key, value in values.items():
            setattr(row, key, value)
        await scope.session.flush()
        await _audit(scope, actor, row, AuditAction.UPDATE, before)
        return row

    row = await _transaction(scope, work)
    log.info("household.child_year_saved", child_year_id=str(row.id), year=year)
    return row


async def delete_child_year(
    scope: HouseholdScope, user_id: uuid.UUID, year: int, person_id: uuid.UUID
) -> None:
    actor = Actor.user(user_id)

    async def work() -> None:
        check_year(year)
        person = await _child(scope, person_id)
        rows = await _all(
            scope, ChildYear, ChildYear.person_id == person.id, ChildYear.year == year
        )
        if not rows:
            raise Rejected(404, NOT_FOUND)
        await _audit(scope, actor, rows[0], AuditAction.DELETE)
        await scope.session.delete(rows[0])
        await scope.session.flush()

    await _transaction(scope, work)
    log.info("household.child_year_deleted", person_id=str(person_id), year=year)


# --- copy ----------------------------------------------------------------------------------


async def copy_year(
    scope: HouseholdScope, user_id: uuid.UUID, year: int, from_year: int
) -> dict[str, int]:
    """Profile, the employments of the persons in that return and the child rows of
    `from_year` into `year` (months capped; children without entitlement are skipped)."""
    actor = Actor.user(user_id)

    async def work() -> dict[str, int]:
        check_year(year)
        check_year(from_year, "from_year")
        if await _profile(scope, year) is not None:
            raise Rejected(409, "year_not_empty")
        source = await _profile(scope, from_year)
        if source is None:
            raise Rejected(404, "nothing_to_copy")
        new_rows: list[Base] = [
            TaxProfile(
                year=year,
                filing_status=source.filing_status,
                bundesland=source.bundesland,
                taxpayer_person_id=source.taxpayer_person_id,
                spouse_person_id=source.spouse_person_id,
            )
        ]
        jobs = await _all(
            scope,
            Employment,
            Employment.year == from_year,
            Employment.person_id.in_(_in_return(source)),
        )
        # ponytail: days are copied as they are; a leap-year source (366 days) into a
        # non-leap year would need a cap once such a pair of years is supported.
        for job in sorted(jobs, key=lambda e: (e.created_at, str(e.id))):
            values = {key: getattr(job, key) for key in EMPLOYMENT_FIELDS}
            new_rows.append(Employment(person_id=job.person_id, year=year, **values))
        existing = {row.person_id for row in await _all(scope, ChildYear, ChildYear.year == year)}
        persons = {p.id: p for p in await _all(scope, Person)}
        for child in await _all(scope, ChildYear, ChildYear.year == from_year):
            limit = _months_limit(persons[child.person_id], year)
            if not limit or child.person_id in existing:
                continue
            new_rows.append(
                ChildYear(
                    person_id=child.person_id,
                    year=year,
                    months=min(child.months, limit),
                    allowance_share=child.allowance_share,
                    in_household=child.in_household,
                )
            )
        for row in new_rows:
            scope.add(row)
        await scope.session.flush()
        for row in new_rows:
            await _audit(scope, actor, row, AuditAction.CREATE)
        return {
            "employments": sum(isinstance(r, Employment) for r in new_rows),
            "children": sum(isinstance(r, ChildYear) for r in new_rows),
        }

    counts = await _transaction(scope, work)
    log.info("household.copied", from_year=from_year, year=year, **counts)
    return counts


# --- read ----------------------------------------------------------------------------------


@dataclass
class ChildEntry:
    person: Person
    max_months: int
    row: ChildYear | None


@dataclass
class HouseholdYear:
    year: int
    profile: TaxProfile | None
    persons: list[Person]
    employments: list[Employment]
    in_return: set[uuid.UUID]
    children: list[ChildEntry]
    years_with_profile: list[int]


async def list_persons(scope: HouseholdScope) -> list[Person]:
    """Adults first, then by first name (#10's order)."""
    stmt = scope.select(Person).order_by(
        case((Person.kind == PersonKind.ADULT, 0), else_=1), Person.first_name, Person.id
    )
    return list((await scope.session.execute(stmt)).scalars().all())


async def read_year(scope: HouseholdScope, year: int) -> HouseholdYear:
    check_year(year)
    persons = await list_persons(scope)
    profiles = await _all(scope, TaxProfile)
    profile = next((p for p in profiles if p.year == year), None)
    employments = await _all(scope, Employment, Employment.year == year)
    employments.sort(key=lambda e: (e.created_at, str(e.id)))
    rows = {r.person_id: r for r in await _all(scope, ChildYear, ChildYear.year == year)}
    children = []
    for person in persons:
        if person.kind is not PersonKind.CHILD:
            continue
        limit = _months_limit(person, year)
        if limit is not None:
            children.append(ChildEntry(person, limit, rows.get(person.id)))
    return HouseholdYear(
        year=year,
        profile=profile,
        persons=persons,
        employments=employments,
        in_return=_in_return(profile),
        children=children,
        years_with_profile=sorted(p.year for p in profiles),
    )
