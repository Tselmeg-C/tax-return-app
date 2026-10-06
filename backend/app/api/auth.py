"""`/auth/*`: magic-link login, sessions, logout (browser path `/api/auth/*`).

Never logged: e-mail addresses, tokens, their hashes, cookie values, the `next` path.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from app.api.deps import (
    NOT_AUTHENTICATED,
    DbSession,
    Scope,
    SignedIn,
    session_cookie_value,
)
from app.auth import service
from app.auth.clock import Clock
from app.auth.mail import MailDispatcher, login_mail
from app.auth.ratelimit import RateLimiter
from app.auth.tokens import (
    MAX_TOKEN_LENGTH,
    clear_session_cookie,
    normalise_email,
    safe_next,
    session_cookie,
)
from app.config import Settings
from app.db.models import AppUser, Household

logger = structlog.stdlib.get_logger("app.auth")

router = APIRouter(prefix="/auth", tags=["auth"])

INVALID_EMAIL = "invalid_email"
INVALID_OR_EXPIRED = "invalid_or_expired"
RATE_LIMITED = "rate_limited"


class MagicLinkBody(BaseModel):
    # Loose types on purpose: validation happens in the handler, so a 422 never echoes input.
    model_config = ConfigDict(extra="ignore")
    email: Any = None
    next: Any = None


class VerifyBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    token: Any = None


def _client_ip(request: Request) -> str:
    # uvicorn sets this from X-Forwarded-For, which the web proxy always overwrites.
    return request.client.host if request.client else "unknown"


def _rate_limited(endpoint: str, retry_after: int) -> JSONResponse:
    logger.warning("auth.rate_limited", endpoint=endpoint)
    return JSONResponse(
        {"detail": RATE_LIMITED}, status_code=429, headers={"Retry-After": str(retry_after)}
    )


@router.post("/magic-link", status_code=202)
async def request_magic_link(body: MagicLinkBody, request: Request, db: DbSession) -> JSONResponse:
    state = request.app.state
    settings: Settings = state.settings
    clock: Clock = state.clock
    limiter: RateLimiter = state.magic_link_limiter

    wait = limiter.hit(_client_ip(request))
    if wait is not None:
        return _rate_limited("magic_link", wait)

    email = normalise_email(body.email)
    if email is None:
        return JSONResponse({"detail": INVALID_EMAIL}, status_code=422)
    next_path = safe_next(body.next)

    now = clock.now()
    await service.delete_stale_tokens(db, now)
    user = await service.find_user_by_email(db, email)
    if user is None:
        logger.info("auth.magic_link.requested", known=False)
    elif user.disabled_at is not None:
        logger.info("auth.magic_link.requested", known=True, user_id=str(user.id))
        logger.info("auth.magic_link.suppressed", reason="user_disabled", user_id=str(user.id))
    else:
        logger.info("auth.magic_link.requested", known=True, user_id=str(user.id))
        if await service.email_limit_reached(db, user.id, now):
            logger.info(
                "auth.magic_link.suppressed", reason="email_rate_limit", user_id=str(user.id)
            )
        else:
            token = await service.create_link_token(
                db,
                user,
                redirect_path=next_path,
                now=now,
                ttl=timedelta(minutes=settings.magic_link_ttl_minutes),
            )
            await db.commit()
            link = f"{settings.app_base_url}/login/verify#token={token}"
            dispatcher: MailDispatcher = state.mailer
            dispatcher.dispatch(login_mail(user.email, link, settings.magic_link_ttl_minutes))
    await db.commit()
    return JSONResponse({"status": "sent"}, status_code=202)


@router.post("/verify")
async def verify(body: VerifyBody, request: Request, db: DbSession) -> Response:
    state = request.app.state
    settings: Settings = state.settings
    clock: Clock = state.clock
    failures: RateLimiter = state.verify_failure_limiter
    ip = _client_ip(request)

    wait = failures.retry_after(ip)
    if wait is not None:
        return _rate_limited("verify", wait)

    def fail() -> JSONResponse:
        failures.add(ip)
        logger.info("auth.login.failed", reason=INVALID_OR_EXPIRED)
        return JSONResponse({"detail": INVALID_OR_EXPIRED}, status_code=400)

    token = body.token
    if not isinstance(token, str) or not token or len(token) > MAX_TOKEN_LENGTH:
        return fail()

    now = clock.now()
    consumed = await service.consume_link_token(db, token, now)
    if consumed is None:
        await db.rollback()
        return fail()
    user = await db.get(AppUser, consumed.user_id)
    if user is None or user.disabled_at is not None:
        await db.commit()  # the link stays used
        return fail()

    await service.invalidate_unused_tokens(db, user.id, now)
    old_cookie = session_cookie_value(request)
    if old_cookie is not None:
        await service.revoke_session_token(db, old_cookie, now)
    max_age = timedelta(days=settings.session_max_days)
    cookie_value, user_session = await service.create_session(db, user, now=now, max_age=max_age)
    await db.commit()

    logger.info("auth.login.succeeded", user_id=str(user.id), session_id=str(user_session.id))
    response = JSONResponse({"next": safe_next(consumed.redirect_path)})
    response.headers.append(
        "Set-Cookie",
        session_cookie(
            cookie_value,
            max_age=int(max_age.total_seconds()),
            secure=settings.secure_cookies,
        ),
    )
    return response


@router.get("/me")
async def me(
    user: SignedIn,
    scope: Scope,
) -> dict[str, str]:
    app_user = await scope.get(AppUser, user.user_id)
    household = await scope.session.get(Household, user.household_id)
    if app_user is None or household is None:  # deleted after the session check
        raise HTTPException(status_code=401, detail=NOT_AUTHENTICATED)
    return {
        "user_id": str(app_user.id),
        "email": app_user.email,
        "role": app_user.role.value,
        "household_id": str(household.id),
        "household_name": household.name,
    }


def _logged_out(settings: Settings) -> Response:
    response = Response(status_code=204)
    response.headers.append("Set-Cookie", clear_session_cookie(secure=settings.secure_cookies))
    return response


@router.post("/logout", status_code=204)
async def logout(request: Request, db: DbSession) -> Response:
    settings: Settings = request.app.state.settings
    clock: Clock = request.app.state.clock
    value = session_cookie_value(request)
    if value is not None:
        session_id = await service.revoke_session_token(db, value, clock.now())
        await db.commit()
        if session_id is not None:
            logger.info("auth.logout", session_id=str(session_id), all_sessions=False)
    return _logged_out(settings)


@router.post("/logout-all", status_code=204)
async def logout_all(
    request: Request,
    user: SignedIn,
    db: DbSession,
) -> Response:
    settings: Settings = request.app.state.settings
    clock: Clock = request.app.state.clock
    count = await service.revoke_user_sessions(db, user.user_id, clock.now())
    await db.commit()
    logger.info("auth.logout", user_id=str(user.user_id), all_sessions=True, revoked=count)
    return _logged_out(settings)
