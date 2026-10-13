"""Household member management (#34): `/users*` (owner only) and own sessions `/auth/sessions*`.

Reuses `app.auth.service` (same behaviour as the CLI). Every query goes through the caller's
`HouseholdScope`: another household's ids answer 404. Never logged or traced: e-mail addresses,
tokens, link URLs. Logs carry user / session ids and codes only. Disabling never touches documents.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentUser, Scope, SignedIn
from app.auth import service
from app.auth.mail import MailDispatcher, email_change_mail, invite_mail
from app.auth.tokens import normalise_email
from app.config import Settings
from app.db.audit import Actor, record, snapshot
from app.db.models import AppUser, Person, UserSession
from app.domain.enums import AuditAction, UserRole

logger = structlog.stdlib.get_logger("app.users")

router = APIRouter(tags=["users"])

FORBIDDEN = "forbidden"
NOT_FOUND = "not_found"
LAST_OWNER = "last_owner"
EMAIL_TAKEN = "email_taken"
PERSON_TAKEN = "person_taken"
INVALID_FIELD = "invalid_field"


async def owner_only(user: SignedIn) -> CurrentUser:
    if user.role is not UserRole.OWNER:
        raise HTTPException(status_code=403, detail=FORBIDDEN)
    return user


Owner = Annotated[CurrentUser, Depends(owner_only)]


class InviteBody(BaseModel):
    # Loose types on purpose: validation happens in the handler, so a 422 never echoes input.
    model_config = ConfigDict(extra="ignore")
    email: Any = None
    role: Any = None


class PatchBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    role: Any = None
    disabled: Any = None
    email: Any = None
    person_id: Any = None


def _invalid(field: str) -> JSONResponse:
    return JSONResponse({"detail": INVALID_FIELD, "field": field}, status_code=422)


def _code(code: str, status: int) -> JSONResponse:
    return JSONResponse({"detail": code}, status_code=status)


def _user_out(user: AppUser, last_login: datetime | None, caller: uuid.UUID) -> dict[str, Any]:
    return {
        "id": str(user.id),
        "email": user.email,
        "role": user.role.value,
        "disabled": user.disabled_at is not None,
        "last_login_at": last_login.isoformat() if last_login else None,
        "person_id": str(user.person_id) if user.person_id else None,
        "is_self": user.id == caller,
    }


async def _last_login(scope: Scope, user_id: uuid.UUID) -> datetime | None:
    return await scope.session.scalar(
        select(func.max(UserSession.created_at)).where(
            UserSession.household_id == scope.household_id, UserSession.user_id == user_id
        )
    )


async def _target(scope: Scope, user_id: uuid.UUID) -> AppUser:
    user = await scope.get(AppUser, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return user


@router.get("/users")
async def list_users(caller: Owner, scope: Scope) -> list[dict[str, Any]]:
    last = (
        select(UserSession.user_id, func.max(UserSession.created_at).label("last"))
        .where(UserSession.household_id == scope.household_id)
        .group_by(UserSession.user_id)
        .subquery()
    )
    rows = await scope.session.execute(
        select(AppUser, last.c.last)
        .outerjoin(last, last.c.user_id == AppUser.id)
        .where(AppUser.household_id == scope.household_id)
        .order_by(AppUser.created_at, AppUser.id)
    )
    return [_user_out(u, seen, caller.user_id) for u, seen in rows.all()]


@router.post("/users", status_code=201)
async def invite_user(body: InviteBody, request: Request, caller: Owner, scope: Scope) -> Response:
    settings: Settings = request.app.state.settings
    db = scope.session
    email = normalise_email(body.email)
    if email is None:
        return _invalid("email")
    role = UserRole.MEMBER
    if body.role is not None:
        if not isinstance(body.role, str) or body.role not in {r.value for r in UserRole}:
            return _invalid("role")
        role = UserRole(body.role)
    if await service.find_user_by_email(db, email) is not None:
        return _code(EMAIL_TAKEN, 409)
    now = request.app.state.clock.now()
    try:
        user = await service.create_user(
            db, caller.household_id, email, role, Actor.user(caller.user_id)
        )
    except IntegrityError:  # a concurrent invite of the same address
        await db.rollback()
        return _code(EMAIL_TAKEN, 409)
    ttl = settings.magic_link_ttl_minutes
    token = await service.create_link_token(
        db, user, redirect_path="/", now=now, ttl=timedelta(minutes=ttl)
    )
    await db.commit()
    dispatcher: MailDispatcher = request.app.state.mailer
    dispatcher.dispatch(
        invite_mail(email, f"{settings.app_base_url}/login/verify#token={token}", ttl)
    )
    logger.info("users.invited", user_id=str(user.id), by=str(caller.user_id), role=role.value)
    return JSONResponse(_user_out(user, None, caller.user_id), status_code=201)


async def _other_active_owner_exists(scope: Scope, target: AppUser) -> bool:
    """Lock the household's other active owners (serialises concurrent demote / disable)."""
    rows = await scope.session.scalars(
        scope.select(AppUser)
        .where(
            AppUser.role == UserRole.OWNER,
            AppUser.disabled_at.is_(None),
            AppUser.id != target.id,
        )
        .with_for_update()
    )
    return rows.first() is not None


@router.patch("/users/{user_id}")
async def update_user(
    user_id: uuid.UUID, body: PatchBody, request: Request, caller: Owner, scope: Scope
) -> Response:
    settings: Settings = request.app.state.settings
    db = scope.session
    sent = body.model_fields_set
    user = await _target(scope, user_id)
    actor = Actor.user(caller.user_id)

    # Validate everything first: nothing is applied when one field is bad.
    new_role: UserRole | None = None
    if "role" in sent:
        if not isinstance(body.role, str) or body.role not in {r.value for r in UserRole}:
            return _invalid("role")
        new_role = UserRole(body.role)
    if "disabled" in sent and not isinstance(body.disabled, bool):
        return _invalid("disabled")
    new_email: str | None = None
    if "email" in sent:
        new_email = normalise_email(body.email)
        if new_email is None:
            return _invalid("email")
    person_id: uuid.UUID | None = None
    if "person_id" in sent and body.person_id is not None:
        try:
            person_id = uuid.UUID(str(body.person_id))
        except ValueError:
            return _invalid("person_id")
        if await scope.get(Person, person_id) is None:
            return _invalid("person_id")

    was_active_owner = user.role is UserRole.OWNER and user.disabled_at is None
    demotes = new_role is UserRole.MEMBER
    disables = body.disabled is True
    if (
        was_active_owner
        and (demotes or disables)
        and not await _other_active_owner_exists(scope, user)
    ):
        await db.rollback()
        return _code(LAST_OWNER, 409)

    now = request.app.state.clock.now()
    before = snapshot(user)
    if new_role is not None:
        user.role = new_role
    if "person_id" in sent:
        user.person_id = person_id
    try:
        await db.flush()
    except IntegrityError:  # `app_user.person_id` is unique
        await db.rollback()
        return _code(PERSON_TAKEN, 409)
    await record(
        db,
        household_id=user.household_id,
        entity="app_user",
        entity_id=user.id,
        action=AuditAction.UPDATE,
        before=before,
        after=snapshot(user),
        actor=actor,
    )
    if "disabled" in sent and (user.disabled_at is not None) != body.disabled:
        await service.set_disabled(db, user, disabled=body.disabled, now=now, actor=actor)

    mail = None
    if new_email is not None and new_email != user.email:
        if await service.find_user_by_email(db, new_email) is not None:
            await db.rollback()
            return _code(EMAIL_TAKEN, 409)
        if await service.email_limit_reached(db, user.id, now):
            await db.rollback()
            return _code("rate_limited", 429)
        await service.invalidate_unused_tokens(db, user.id, now, kind="email_change")
        ttl = settings.magic_link_ttl_minutes
        token = await service.create_link_token(
            db,
            user,
            redirect_path=None,
            now=now,
            ttl=timedelta(minutes=ttl),
            new_email=new_email,
        )
        await record(
            db,
            household_id=user.household_id,
            entity="app_user",
            entity_id=user.id,
            action=AuditAction.UPDATE,
            before={"email_change_pending": False},
            after={"email_change_pending": True},
            actor=actor,
        )
        link = f"{settings.app_base_url}/login/confirm-email#token={token}"
        mail = email_change_mail(new_email, link, ttl)
    await db.commit()
    if mail is not None:
        dispatcher: MailDispatcher = request.app.state.mailer
        dispatcher.dispatch(mail)
    logger.info("users.updated", user_id=str(user.id), by=str(caller.user_id), fields=sorted(sent))
    return JSONResponse(_user_out(user, await _last_login(scope, user.id), caller.user_id))


def _session_out(row: UserSession, current: uuid.UUID) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "created_at": row.created_at.isoformat(),
        "last_seen_at": row.last_seen_at.isoformat(),
        "expires_at": row.expires_at.isoformat(),
        "current": row.id == current,
    }


async def _active_sessions(scope: Scope, request: Request, user_id: uuid.UUID) -> list[UserSession]:
    settings: Settings = request.app.state.settings
    now = request.app.state.clock.now()
    idle = timedelta(days=settings.session_idle_days)
    rows = await scope.session.scalars(
        scope.select(UserSession)
        .where(
            UserSession.user_id == user_id,
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > now,
        )
        .order_by(UserSession.created_at.desc())
    )
    return [r for r in rows.all() if now < r.last_seen_at + idle]


@router.get("/users/{user_id}/sessions")
async def user_sessions(
    user_id: uuid.UUID, request: Request, caller: Owner, scope: Scope
) -> list[dict[str, Any]]:
    user = await _target(scope, user_id)
    return [
        _session_out(r, caller.session_id) for r in await _active_sessions(scope, request, user.id)
    ]


@router.delete("/users/{user_id}/sessions")
async def end_user_sessions(
    user_id: uuid.UUID, request: Request, caller: Owner, scope: Scope
) -> dict[str, int]:
    user = await _target(scope, user_id)
    open_sessions = await _active_sessions(scope, request, user.id)
    count = await service.revoke_user_sessions(
        scope.session, user.id, request.app.state.clock.now()
    )
    await record(
        scope.session,
        household_id=user.household_id,
        entity="app_user",
        entity_id=user.id,
        action=AuditAction.UPDATE,
        before={"active_sessions": len(open_sessions)},
        after={"active_sessions": 0},
        actor=Actor.user(caller.user_id),
    )
    await scope.session.commit()
    logger.info("users.sessions_ended", user_id=str(user.id), by=str(caller.user_id), ended=count)
    return {"ended": count}


@router.get("/auth/sessions")
async def own_sessions(request: Request, user: SignedIn, scope: Scope) -> list[dict[str, Any]]:
    return [
        _session_out(r, user.session_id)
        for r in await _active_sessions(scope, request, user.user_id)
    ]


@router.delete("/auth/sessions/{session_id}", status_code=204)
async def end_own_session(
    session_id: uuid.UUID, request: Request, user: SignedIn, scope: Scope
) -> Response:
    row = await scope.get(UserSession, session_id)
    if row is None or row.user_id != user.user_id or row.revoked_at is not None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    row.revoked_at = request.app.state.clock.now()
    await record(
        scope.session,
        household_id=user.household_id,
        entity="user_session",
        entity_id=row.id,
        action=AuditAction.DELETE,
        before={"user_id": str(user.user_id)},
        after=None,
        actor=Actor.user(user.user_id),
    )
    await scope.session.commit()
    logger.info("users.session_ended", user_id=str(user.user_id), session_id=str(row.id))
    return Response(status_code=204)
