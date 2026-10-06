"""Request dependencies: DB session, clock, the signed-in user and the household scope.

Every route is protected by default: `require_session` is an app-level dependency (see
`app.api.main.create_app`) that lets only `PUBLIC_PATHS` (`app/api/public.py`) through
without a valid session cookie.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.public import DEV_ONLY_PATHS, PUBLIC_PATHS
from app.auth.clock import Clock
from app.auth.service import find_valid_session
from app.auth.tokens import clear_session_cookie, cookie_name
from app.config import Settings
from app.db.scope import HouseholdScope
from app.domain.enums import UserRole

NOT_AUTHENTICATED = "not_authenticated"


@dataclass(frozen=True)
class CurrentUser:
    user_id: uuid.UUID
    household_id: uuid.UUID
    role: UserRole
    session_id: uuid.UUID


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """One `AsyncSession` per request (shared by all dependencies). Endpoints commit."""
    async with request.app.state.sessionmaker() as session:
        yield session


DbSession = Annotated[AsyncSession, Depends(get_db)]


def get_settings_dep(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


def session_cookie_value(request: Request) -> str | None:
    settings: Settings = request.app.state.settings
    value = request.cookies.get(cookie_name(settings.secure_cookies))
    return value or None


async def _authenticate(request: Request, db: AsyncSession) -> CurrentUser:
    settings: Settings = request.app.state.settings
    clock: Clock = request.app.state.clock
    value = session_cookie_value(request)
    if value is None:
        raise HTTPException(status_code=401, detail=NOT_AUTHENTICATED)
    found = await find_valid_session(
        db, value, now=clock.now(), idle=timedelta(days=settings.session_idle_days)
    )
    if found is None:
        # The browser sent a cookie we no longer accept: clear it.
        raise HTTPException(
            status_code=401,
            detail=NOT_AUTHENTICATED,
            headers={"Set-Cookie": clear_session_cookie(secure=settings.secure_cookies)},
        )
    user_session, user = found
    await db.commit()  # persists a `last_seen_at` touch, if any
    return CurrentUser(
        user_id=user.id, household_id=user.household_id, role=user.role, session_id=user_session.id
    )


async def require_session(request: Request, db: DbSession) -> None:
    """App-level guard: every route needs a session unless its path is in `PUBLIC_PATHS`."""
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if path in DEV_ONLY_PATHS and request.app.state.settings.is_production:
        raise HTTPException(status_code=404, detail="Not Found")
    if path in PUBLIC_PATHS:
        return
    request.state.current_user = await _authenticate(request, db)


async def current_user(request: Request, db: DbSession) -> CurrentUser:
    cached = getattr(request.state, "current_user", None)
    if isinstance(cached, CurrentUser):
        return cached
    user = await _authenticate(request, db)
    request.state.current_user = user
    return user


SignedIn = Annotated[CurrentUser, Depends(current_user)]


async def get_scope(user: SignedIn, db: DbSession) -> HouseholdScope:
    """The signed-in user's household scope (all household-owned queries go through it)."""
    return HouseholdScope(db, user.household_id)


Scope = Annotated[HouseholdScope, Depends(get_scope)]
