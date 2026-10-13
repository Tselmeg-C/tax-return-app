"""`/auth/passkey/*` and `/auth/passkeys*` (#12). Ceremony logic: `app/auth/passkeys.py`.

Never logged or traced: credential ids, keys, challenges, client data, signatures, the
challenge cookie, passkey names, `str(exc)` of the webauthn lib. Errors carry a code only.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import IntegrityError

from app.api.auth import client_ip, open_session, rate_limited
from app.api.deps import DbSession, Scope, SignedIn
from app.auth import passkeys as ceremony
from app.auth import service
from app.auth.clock import Clock
from app.auth.ratelimit import RateLimiter
from app.auth.tokens import safe_next
from app.config import Settings
from app.db.audit import Actor, record
from app.db.models import AppUser, Passkey
from app.domain.enums import AuditAction

logger = structlog.stdlib.get_logger("app.auth")

router = APIRouter(prefix="/auth", tags=["auth"])

INVALID_PASSKEY = "invalid_passkey"
PASSKEY_LIMIT = "passkey_limit"
INVALID_NAME = "invalid_name"


class CredentialBody(BaseModel):
    # Loose types on purpose: validation happens in the handler, so a 422 never echoes input.
    model_config = ConfigDict(extra="ignore")
    credential: Any = None
    name: Any = None
    next: Any = None


class RenameBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: Any = None


def _invalid() -> JSONResponse:
    return JSONResponse({"detail": INVALID_PASSKEY}, status_code=400)


def _with_cookie(response: JSONResponse, request: Request, value: str, max_age: int) -> None:
    settings: Settings = request.app.state.settings
    response.headers.append(
        "Set-Cookie",
        ceremony.challenge_cookie(value, secure=settings.secure_cookies, max_age=max_age),
    )


def _seal(request: Request, purpose: str, challenge: bytes, user_id: uuid.UUID | None) -> str:
    return ceremony.seal(
        request.app.state.passkey_key,
        purpose=purpose,
        challenge=challenge,
        user_id=user_id,
        now=request.app.state.clock.now(),
    )


def _unseal(request: Request, purpose: str) -> tuple[bytes, uuid.UUID | None] | None:
    settings: Settings = request.app.state.settings
    token = request.cookies.get(ceremony.challenge_cookie_name(settings.secure_cookies))
    return ceremony.unseal(
        request.app.state.passkey_key, token, purpose=purpose, now=request.app.state.clock.now()
    )


def _out(p: Passkey) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "name": p.name,
        "created_at": p.created_at.isoformat(),
        "last_used_at": p.last_used_at.isoformat() if p.last_used_at else None,
    }


def _clean_name(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None
    name = raw.strip()
    return name if 0 < len(name) <= ceremony.MAX_NAME_LENGTH else None


async def _own(scope: Scope, user: SignedIn) -> list[Passkey]:
    result = await scope.session.execute(
        scope.select(Passkey).where(Passkey.user_id == user.user_id).order_by(Passkey.created_at)
    )
    return list(result.scalars())


async def _own_one(scope: Scope, user: SignedIn, passkey_id: uuid.UUID) -> Passkey:
    found = await scope.get(Passkey, passkey_id)
    if found is None or found.user_id != user.user_id:
        raise HTTPException(status_code=404, detail="not_found")
    return found


@router.post("/passkey/register/options")
async def register_options(
    request: Request, user: SignedIn, scope: Scope, db: DbSession
) -> JSONResponse:
    settings: Settings = request.app.state.settings
    app_user = await scope.get(AppUser, user.user_id)
    if app_user is None:
        raise HTTPException(status_code=401, detail="not_authenticated")
    existing = await _own(scope, user)
    if len(existing) >= ceremony.MAX_PASSKEYS:
        return JSONResponse({"detail": PASSKEY_LIMIT}, status_code=409)
    options, challenge = ceremony.registration_options(
        settings,
        user_id=user.user_id,
        email=app_user.email,
        existing=[p.credential_id for p in existing],
    )
    response = JSONResponse(options)
    _with_cookie(
        response,
        request,
        _seal(request, ceremony.REGISTER, challenge, user.user_id),
        int(ceremony.CHALLENGE_TTL.total_seconds()),
    )
    return response


@router.post("/passkey/register/verify")
async def register_verify(
    body: CredentialBody, request: Request, user: SignedIn, scope: Scope, db: DbSession
) -> JSONResponse:
    settings: Settings = request.app.state.settings
    sealed = _unseal(request, ceremony.REGISTER)
    if sealed is None or sealed[1] != user.user_id or not isinstance(body.credential, dict):
        return _invalid()
    existing = await _own(scope, user)
    if len(existing) >= ceremony.MAX_PASSKEYS:
        return JSONResponse({"detail": PASSKEY_LIMIT}, status_code=409)
    try:
        credential_id, public_key, sign_count = ceremony.verify_registration(
            settings, body.credential, sealed[0]
        )
    except Exception as exc:
        logger.info("auth.passkey.register_failed", error=type(exc).__name__)
        return _invalid()
    name = _clean_name(body.name) or f"Passkey {len(existing) + 1}"
    passkey = Passkey(
        user_id=user.user_id,
        credential_id=credential_id,
        public_key=public_key,
        sign_count=sign_count,
        transports=ceremony.clean_transports(body.credential),
        name=name,
    )
    scope.add(passkey)
    try:
        await db.flush()
    except IntegrityError:  # credential already registered
        await db.rollback()
        return _invalid()
    await record(
        db,
        household_id=user.household_id,
        entity="passkey",
        entity_id=passkey.id,
        action=AuditAction.CREATE,
        before=None,
        after={"user_id": str(user.user_id), "name": name},
        actor=Actor.user(user.user_id),
    )
    await db.commit()
    logger.info("auth.passkey.registered", user_id=str(user.user_id), passkey_id=str(passkey.id))
    response = JSONResponse(_out(passkey), status_code=201)
    _with_cookie(response, request, "", 0)
    return response


@router.post("/passkey/login/options")
async def login_options(request: Request) -> JSONResponse:
    limiter: RateLimiter = request.app.state.passkey_options_limiter
    wait = limiter.hit(client_ip(request))
    if wait is not None:
        return rate_limited("passkey_options", wait)
    options, challenge = ceremony.authentication_options(request.app.state.settings)
    response = JSONResponse(options)
    _with_cookie(
        response,
        request,
        _seal(request, ceremony.LOGIN, challenge, None),
        int(ceremony.CHALLENGE_TTL.total_seconds()),
    )
    return response


@router.post("/passkey/login/verify")
async def login_verify(body: CredentialBody, request: Request, db: DbSession) -> Response:
    settings: Settings = request.app.state.settings
    clock: Clock = request.app.state.clock
    failures: RateLimiter = request.app.state.verify_failure_limiter
    ip = client_ip(request)
    wait = failures.retry_after(ip)
    if wait is not None:
        return rate_limited("passkey_verify", wait)

    def fail(reason: str, exc: Exception | None = None) -> JSONResponse:
        failures.add(ip)
        logger.info(
            "auth.passkey.login_failed", reason=reason, error=type(exc).__name__ if exc else None
        )
        return _invalid()

    sealed = _unseal(request, ceremony.LOGIN)
    raw_id = ceremony.raw_credential_id(body.credential)
    if sealed is None or raw_id is None:
        return fail("malformed")
    found = await service.find_passkey_with_user(db, raw_id)
    if found is None:
        return fail("unknown")
    passkey, user = found
    try:
        new_count = ceremony.verify_authentication(
            settings,
            body.credential,
            sealed[0],
            public_key=passkey.public_key,
            sign_count=passkey.sign_count,
        )
    except Exception as exc:
        return fail("verification", exc)
    if user.disabled_at is not None:
        return fail("user_disabled")

    passkey.sign_count = new_count
    passkey.last_used_at = clock.now()
    response = JSONResponse({"next": safe_next(body.next)})
    await open_session(request, db, user, response)
    logger.info("auth.passkey.login", passkey_id=str(passkey.id))
    _with_cookie(response, request, "", 0)
    return response


@router.get("/passkeys")
async def list_passkeys(user: SignedIn, scope: Scope) -> list[dict[str, Any]]:
    return [_out(p) for p in await _own(scope, user)]


@router.patch("/passkeys/{passkey_id}")
async def rename_passkey(
    passkey_id: uuid.UUID, body: RenameBody, user: SignedIn, scope: Scope, db: DbSession
) -> JSONResponse:
    passkey = await _own_one(scope, user, passkey_id)
    name = _clean_name(body.name)
    if name is None:
        return JSONResponse({"detail": INVALID_NAME}, status_code=422)
    old = passkey.name
    passkey.name = name
    await record(
        db,
        household_id=user.household_id,
        entity="passkey",
        entity_id=passkey.id,
        action=AuditAction.UPDATE,
        before={"name": old},
        after={"name": name},
        actor=Actor.user(user.user_id),
    )
    await db.commit()
    return JSONResponse(_out(passkey))


@router.delete("/passkeys/{passkey_id}", status_code=204)
async def delete_passkey(
    passkey_id: uuid.UUID, user: SignedIn, scope: Scope, db: DbSession
) -> Response:
    passkey = await _own_one(scope, user, passkey_id)
    await record(
        db,
        household_id=user.household_id,
        entity="passkey",
        entity_id=passkey.id,
        action=AuditAction.DELETE,
        before={"user_id": str(user.user_id), "name": passkey.name},
        after=None,
        actor=Actor.user(user.user_id),
    )
    await db.delete(passkey)
    await db.commit()
    logger.info("auth.passkey.deleted", user_id=str(user.user_id), passkey_id=str(passkey_id))
    return Response(status_code=204)
