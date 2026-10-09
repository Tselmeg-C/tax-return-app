"""Household and per-year profile api (#13): persons, `GET /household/{year}`, the profile,
employments, child rows and copy.

Writes go through `app.household.service` (validation, audit, logs by id / code). Error bodies
are a code plus the field name (plus `count` / `years` for the person 409s), never a value. The
Steuer-ID is accepted in POST / PATCH bodies only and answered masked (`steuer_id_masked`).
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictBool

from app.api.deps import Scope, SignedIn
from app.auth.clock import Clock
from app.db.models import AppUser, ChildYear, Employment, Person, TaxProfile
from app.db.scope import HouseholdScope
from app.domain.enums import (
    AllowanceShare,
    Bundesland,
    FilingStatus,
    PersonKind,
    Religion,
    Steuerklasse,
)
from app.household import service
from app.household.service import ChildYearInput, HouseholdYear, ProfileInput
from app.household.validation import Rejected, mask_steuer_id

router = APIRouter()
BERLIN = ZoneInfo("Europe/Berlin")


def _today(request: Request) -> date:
    clock: Clock = request.app.state.clock
    return clock.now().astimezone(BERLIN).date()


def _rejected(exc: Rejected) -> JSONResponse:
    body: dict[str, Any] = {"detail": exc.code}
    if exc.field is not None:
        body["field"] = exc.field
    body.update(exc.extra)
    return JSONResponse(body, status_code=exc.status)


# --- response shapes -----------------------------------------------------------------------


class PersonOut(BaseModel):
    id: uuid.UUID
    kind: PersonKind
    first_name: str
    last_name: str | None
    dob: date | None
    religion: Religion
    disability_grade: int | None
    merkzeichen_h_bl_tbl: bool
    steuer_id_masked: str | None
    is_me: bool

    @classmethod
    def of(cls, person: Person, me: uuid.UUID | None) -> PersonOut:
        return cls(
            id=person.id,
            kind=person.kind,
            first_name=person.first_name,
            last_name=person.last_name,
            dob=person.dob,
            religion=person.religion,
            disability_grade=person.disability_grade,
            merkzeichen_h_bl_tbl=person.merkzeichen_h_bl_tbl,
            steuer_id_masked=mask_steuer_id(person.steuer_id),
            is_me=person.id == me,
        )


class TaxProfileOut(BaseModel):
    year: int
    filing_status: FilingStatus
    bundesland: Bundesland
    taxpayer_person_id: uuid.UUID
    spouse_person_id: uuid.UUID | None

    @classmethod
    def of(cls, p: TaxProfile) -> TaxProfileOut:
        return cls(
            year=p.year,
            filing_status=p.filing_status,
            bundesland=p.bundesland,
            taxpayer_person_id=p.taxpayer_person_id,
            spouse_person_id=p.spouse_person_id,
        )


class EmploymentOut(BaseModel):
    id: uuid.UUID
    person_id: uuid.UUID
    year: int
    employer_name: str
    steuerklasse: Steuerklasse
    has_factor: bool
    commute_km: int | None
    office_days: int
    homeoffice_days: int
    in_return: bool

    @classmethod
    def of(cls, e: Employment, in_return: bool) -> EmploymentOut:
        return cls(
            id=e.id,
            person_id=e.person_id,
            year=e.year,
            employer_name=e.employer_name,
            steuerklasse=e.steuerklasse,
            has_factor=e.has_factor,
            commute_km=e.commute_km,
            office_days=e.office_days,
            homeoffice_days=e.homeoffice_days,
            in_return=in_return,
        )


class ChildYearOut(BaseModel):
    person_id: uuid.UUID
    year: int
    months: int
    allowance_share: AllowanceShare
    in_household: bool

    @classmethod
    def of(cls, c: ChildYear) -> ChildYearOut:
        return cls(
            person_id=c.person_id,
            year=c.year,
            months=c.months,
            allowance_share=c.allowance_share,
            in_household=c.in_household,
        )


class ChildEntryOut(BaseModel):
    person_id: uuid.UUID
    max_months: int
    child_year: ChildYearOut | None


class HouseholdOut(BaseModel):
    year: int
    profile: TaxProfileOut | None
    persons: list[PersonOut]
    employments: list[EmploymentOut]
    children: list[ChildEntryOut]
    years_with_profile: list[int]

    @classmethod
    def of(cls, h: HouseholdYear, me: uuid.UUID | None) -> HouseholdOut:
        return cls(
            year=h.year,
            profile=TaxProfileOut.of(h.profile) if h.profile is not None else None,
            persons=[PersonOut.of(p, me) for p in h.persons],
            employments=[EmploymentOut.of(e, e.person_id in h.in_return) for e in h.employments],
            children=[
                ChildEntryOut(
                    person_id=c.person.id,
                    max_months=c.max_months,
                    child_year=ChildYearOut.of(c.row) if c.row is not None else None,
                )
                for c in h.children
            ],
            years_with_profile=h.years_with_profile,
        )


# --- request bodies (unknown keys -> `invalid_request`; the Steuer-ID is a `SecretStr`) -----


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class PersonPatch(_Body):
    kind: Any = None  # never allowed: `kind_immutable`
    first_name: Any = None
    last_name: Any = None
    dob: date | None = None
    steuer_id: SecretStr | None = None
    religion: Religion | None = None
    disability_grade: int | None = None
    merkzeichen_h_bl_tbl: StrictBool | None = None  # null is rejected: the column is NOT NULL


class PersonCreate(PersonPatch):
    kind: PersonKind
    link_to_me: bool = False


class ProfileBody(_Body):
    filing_status: FilingStatus
    bundesland: Bundesland
    taxpayer_person_id: uuid.UUID
    spouse_person_id: uuid.UUID | None = None


class EmploymentPatch(_Body):
    employer_name: Any = None
    steuerklasse: Steuerklasse | None = None
    has_factor: bool | None = None
    commute_km: int | None = None
    office_days: int | None = None
    homeoffice_days: int | None = None


class EmploymentCreate(EmploymentPatch):
    person_id: uuid.UUID
    year: int
    employer_name: Any
    steuerklasse: Steuerklasse


class ChildYearBody(_Body):
    months: int = Field(ge=0, le=12)
    allowance_share: AllowanceShare
    in_household: bool = True


class CopyBody(_Body):
    from_year: int


def _person_fields(body: PersonPatch) -> dict[str, Any]:
    sent = {k: getattr(body, k) for k in body.model_fields_set if k != "link_to_me"}
    if isinstance(sent.get("steuer_id"), SecretStr):
        sent["steuer_id"] = sent["steuer_id"].get_secret_value()
    return sent


async def _me(scope: HouseholdScope, user_id: uuid.UUID) -> uuid.UUID | None:
    user = await scope.get(AppUser, user_id)
    return user.person_id if user is not None else None


async def _person_out(scope: HouseholdScope, person: Person, user_id: uuid.UUID) -> Any:
    return PersonOut.of(person, await _me(scope, user_id)).model_dump(mode="json")


async def _in_return(scope: HouseholdScope, row: Employment) -> bool:
    found = await scope.session.execute(scope.select(TaxProfile).where(TaxProfile.year == row.year))
    profile = found.scalar_one_or_none()
    return profile is not None and row.person_id in (
        profile.taxpayer_person_id,
        profile.spouse_person_id,
    )


# --- persons -------------------------------------------------------------------------------


@router.get("/persons")
async def list_persons(user: SignedIn, scope: Scope) -> list[PersonOut]:
    me = await _me(scope, user.user_id)
    return [PersonOut.of(p, me) for p in await service.list_persons(scope)]


@router.post("/persons", status_code=201)
async def create_person(
    request: Request, user: SignedIn, scope: Scope, body: PersonCreate
) -> JSONResponse:
    try:
        person = await service.create_person(
            scope,
            user.user_id,
            body.kind,
            _person_fields(body),
            link_to_me=body.link_to_me,
            today=_today(request),
        )
    except Rejected as exc:
        return _rejected(exc)
    return JSONResponse(await _person_out(scope, person, user.user_id), status_code=201)


@router.patch("/persons/{person_id}")
async def patch_person(
    request: Request, user: SignedIn, scope: Scope, person_id: uuid.UUID, body: PersonPatch
) -> JSONResponse:
    try:
        person = await service.update_person(
            scope, user.user_id, person_id, _person_fields(body), today=_today(request)
        )
    except Rejected as exc:
        return _rejected(exc)
    return JSONResponse(await _person_out(scope, person, user.user_id))


@router.delete("/persons/{person_id}", status_code=204)
async def delete_person(user: SignedIn, scope: Scope, person_id: uuid.UUID) -> Response:
    try:
        await service.delete_person(scope, user.user_id, person_id)
    except Rejected as exc:
        return _rejected(exc)
    return Response(status_code=204)


# --- household year ------------------------------------------------------------------------


async def _household(scope: HouseholdScope, user_id: uuid.UUID, year: int) -> JSONResponse:
    try:
        found = await service.read_year(scope, year)
    except Rejected as exc:
        return _rejected(exc)
    out = HouseholdOut.of(found, await _me(scope, user_id))
    return JSONResponse(out.model_dump(mode="json"))


@router.get("/household/{year}")
async def get_household(user: SignedIn, scope: Scope, year: int) -> JSONResponse:
    return await _household(scope, user.user_id, year)


@router.put("/household/{year}/profile")
async def put_profile(user: SignedIn, scope: Scope, year: int, body: ProfileBody) -> JSONResponse:
    data = ProfileInput(
        filing_status=body.filing_status,
        bundesland=body.bundesland,
        taxpayer_person_id=body.taxpayer_person_id,
        spouse_person_id=body.spouse_person_id,
    )
    try:
        profile = await service.put_profile(scope, user.user_id, year, data)
    except Rejected as exc:
        return _rejected(exc)
    return JSONResponse(TaxProfileOut.of(profile).model_dump(mode="json"))


@router.post("/household/{year}/copy")
async def copy_year(user: SignedIn, scope: Scope, year: int, body: CopyBody) -> JSONResponse:
    try:
        await service.copy_year(scope, user.user_id, year, body.from_year)
    except Rejected as exc:
        return _rejected(exc)
    return await _household(scope, user.user_id, year)


@router.put("/household/{year}/children/{person_id}")
async def put_child_year(
    user: SignedIn, scope: Scope, year: int, person_id: uuid.UUID, body: ChildYearBody
) -> JSONResponse:
    data = ChildYearInput(
        months=body.months, allowance_share=body.allowance_share, in_household=body.in_household
    )
    try:
        row = await service.put_child_year(scope, user.user_id, year, person_id, data)
    except Rejected as exc:
        return _rejected(exc)
    return JSONResponse(ChildYearOut.of(row).model_dump(mode="json"))


@router.delete("/household/{year}/children/{person_id}", status_code=204)
async def delete_child_year(
    user: SignedIn, scope: Scope, year: int, person_id: uuid.UUID
) -> Response:
    try:
        await service.delete_child_year(scope, user.user_id, year, person_id)
    except Rejected as exc:
        return _rejected(exc)
    return Response(status_code=204)


# --- employments ---------------------------------------------------------------------------


@router.post("/employments", status_code=201)
async def create_employment(user: SignedIn, scope: Scope, body: EmploymentCreate) -> JSONResponse:
    sent = {k: getattr(body, k) for k in body.model_fields_set if k not in ("person_id", "year")}
    try:
        row = await service.create_employment(scope, user.user_id, body.person_id, body.year, sent)
    except Rejected as exc:
        return _rejected(exc)
    return JSONResponse(EmploymentOut.of(row, True).model_dump(mode="json"), status_code=201)


@router.patch("/employments/{employment_id}")
async def patch_employment(
    user: SignedIn, scope: Scope, employment_id: uuid.UUID, body: EmploymentPatch
) -> JSONResponse:
    sent = {k: getattr(body, k) for k in body.model_fields_set}
    try:
        row = await service.update_employment(scope, user.user_id, employment_id, sent)
    except Rejected as exc:
        return _rejected(exc)
    out = EmploymentOut.of(row, await _in_return(scope, row))
    return JSONResponse(out.model_dump(mode="json"))


@router.delete("/employments/{employment_id}", status_code=204)
async def delete_employment(user: SignedIn, scope: Scope, employment_id: uuid.UUID) -> Response:
    try:
        await service.delete_employment(scope, user.user_id, employment_id)
    except Rejected as exc:
        return _rejected(exc)
    return Response(status_code=204)
