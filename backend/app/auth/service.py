"""Auth persistence: link tokens and sessions (used by the api and the CLI).

Lookups by e-mail or token hash happen before any household is known, so this module is the
one place that queries `app_user`, `magic_link_token` and `user_session` without a
`HouseholdScope`. Everything after login goes through `get_scope`.

Times always come from the caller's clock (`app.auth.clock`), never from `now()` in SQL.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.tokens import hash_token, new_token
from app.db.audit import Actor, record, snapshot
from app.db.models import AppUser, MagicLinkToken, Passkey, UserSession
from app.domain.enums import AuditAction, UserRole

# Per e-mail: at most 3 links per 15 min and 10 per 24 h (counted from token rows).
EMAIL_LIMITS: tuple[tuple[int, timedelta], ...] = (
    (3, timedelta(minutes=15)),
    (10, timedelta(hours=24)),
)
TOKEN_RETENTION = timedelta(hours=24)  # expired rows older than this are deleted
LAST_SEEN_GRANULARITY = timedelta(minutes=5)


async def find_user_by_email(session: AsyncSession, email: str) -> AppUser | None:
    """`email` must already be normalised (lowercase); `app_user.email` is globally unique."""
    result = await session.execute(select(AppUser).where(AppUser.email == email))
    return result.scalar_one_or_none()


async def delete_stale_tokens(session: AsyncSession, now: datetime) -> None:
    await session.execute(
        delete(MagicLinkToken).where(MagicLinkToken.expires_at < now - TOKEN_RETENTION)
    )


async def email_limit_reached(session: AsyncSession, user_id: uuid.UUID, now: datetime) -> bool:
    for limit, window in EMAIL_LIMITS:
        count = await session.scalar(
            select(func.count())
            .select_from(MagicLinkToken)
            .where(MagicLinkToken.user_id == user_id, MagicLinkToken.created_at > now - window)
        )
        if (count or 0) >= limit:
            return True
    return False


async def create_link_token(
    session: AsyncSession,
    user: AppUser,
    *,
    redirect_path: str | None,
    now: datetime,
    ttl: timedelta,
    new_email: str | None = None,
) -> str:
    """Insert a token row (hash only) and return the raw token for the link."""
    token = new_token()
    session.add(
        MagicLinkToken(
            household_id=user.household_id,
            user_id=user.id,
            token_hash=hash_token(token),
            redirect_path=redirect_path,
            new_email=new_email,
            created_at=now,
            expires_at=now + ttl,
        )
    )
    await session.flush()
    return token


@dataclass(frozen=True)
class ConsumedToken:
    id: uuid.UUID
    user_id: uuid.UUID
    household_id: uuid.UUID
    redirect_path: str | None
    new_email: str | None = None


async def consume_link_token(
    session: AsyncSession, token: str, now: datetime, *, email_change: bool = False
) -> ConsumedToken | None:
    """Mark the token used in one atomic UPDATE; concurrent calls have exactly one winner.

    Login links (`email_change=False`) and e-mail change links (`True`) are not interchangeable.
    """
    result = await session.execute(
        update(MagicLinkToken)
        .where(
            MagicLinkToken.token_hash == hash_token(token),
            MagicLinkToken.used_at.is_(None),
            MagicLinkToken.expires_at > now,
            MagicLinkToken.new_email.is_not(None)
            if email_change
            else MagicLinkToken.new_email.is_(None),
        )
        .values(used_at=now)
        .returning(
            MagicLinkToken.id,
            MagicLinkToken.user_id,
            MagicLinkToken.household_id,
            MagicLinkToken.redirect_path,
            MagicLinkToken.new_email,
        )
        .execution_options(synchronize_session=False)
    )
    row = result.one_or_none()
    if row is None:
        return None
    return ConsumedToken(row.id, row.user_id, row.household_id, row.redirect_path, row.new_email)


async def invalidate_unused_tokens(
    session: AsyncSession, user_id: uuid.UUID, now: datetime, *, kind: str = "all"
) -> int:
    """Burn unused links; `kind` = `all`, `login` (no e-mail change links) or `email_change`."""
    conditions = [MagicLinkToken.user_id == user_id, MagicLinkToken.used_at.is_(None)]
    if kind == "login":
        conditions.append(MagicLinkToken.new_email.is_(None))
    elif kind == "email_change":
        conditions.append(MagicLinkToken.new_email.is_not(None))
    result = await session.execute(
        update(MagicLinkToken)
        .where(*conditions)
        .values(used_at=now)
        .execution_options(synchronize_session=False)
    )
    return int(getattr(result, "rowcount", 0) or 0)


async def create_session(
    session: AsyncSession, user: AppUser, *, now: datetime, max_age: timedelta
) -> tuple[str, UserSession]:
    """A new session row; returns the raw cookie value (stored only as its hash)."""
    token = new_token()
    row = UserSession(
        household_id=user.household_id,
        user_id=user.id,
        token_hash=hash_token(token),
        created_at=now,
        last_seen_at=now,
        expires_at=now + max_age,
    )
    session.add(row)
    await session.flush()
    return token, row


async def revoke_session_token(
    session: AsyncSession, token: str, now: datetime
) -> uuid.UUID | None:
    """Revoke the (unrevoked) session with this cookie value; returns its id if there was one."""
    result = await session.execute(
        update(UserSession)
        .where(UserSession.token_hash == hash_token(token), UserSession.revoked_at.is_(None))
        .values(revoked_at=now)
        .returning(UserSession.id)
        .execution_options(synchronize_session=False)
    )
    return result.scalar_one_or_none()


async def revoke_user_sessions(session: AsyncSession, user_id: uuid.UUID, now: datetime) -> int:
    result = await session.execute(
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=now)
        .execution_options(synchronize_session=False)
    )
    return int(getattr(result, "rowcount", 0) or 0)


async def find_valid_session(
    session: AsyncSession, token: str, *, now: datetime, idle: timedelta
) -> tuple[UserSession, AppUser] | None:
    """The session for this cookie value and its user, if usable right now.

    Rejects unknown, revoked, idle-expired (`now >= last_seen_at + idle`), absolutely expired
    (`now >= expires_at`) and disabled-user sessions. Touches `last_seen_at` at most once per
    5 min (flushes; the caller commits).
    """
    result = await session.execute(
        select(UserSession, AppUser)
        .join(AppUser, AppUser.id == UserSession.user_id)
        .where(UserSession.token_hash == hash_token(token))
    )
    row = result.one_or_none()
    if row is None:
        return None
    user_session, user = row
    if (
        user_session.revoked_at is not None
        or now >= user_session.expires_at
        or now >= user_session.last_seen_at + idle
        or user.disabled_at is not None
    ):
        return None
    if now - user_session.last_seen_at >= LAST_SEEN_GRANULARITY:
        user_session.last_seen_at = now
        await session.flush()
    return user_session, user


# --- passkeys (#12): lookup by credential id happens before a household is known ---------


async def find_passkey_with_user(
    session: AsyncSession, credential_id: bytes
) -> tuple[Passkey, AppUser] | None:
    result = await session.execute(
        select(Passkey, AppUser)
        .join(AppUser, AppUser.id == Passkey.user_id)
        .where(Passkey.credential_id == credential_id)
    )
    row = result.one_or_none()
    return None if row is None else (row[0], row[1])


# --- user management (#34), shared by the CLI and `/users` ------------------------------


async def create_user(
    session: AsyncSession, household_id: uuid.UUID, email: str, role: UserRole, actor: Actor
) -> AppUser:
    """Insert a login (`email` normalised) and its audit row."""
    user = AppUser(household_id=household_id, email=email, role=role)
    session.add(user)
    await session.flush()
    await record(
        session,
        household_id=household_id,
        entity="app_user",
        entity_id=user.id,
        action=AuditAction.CREATE,
        before=None,
        after=snapshot(user),
        actor=actor,
    )
    return user


async def set_disabled(
    session: AsyncSession, user: AppUser, *, disabled: bool, now: datetime, actor: Actor
) -> tuple[int, int]:
    """(Un)block login. Disabling also ends the sessions and burns unused links; returns their
    counts. Never touches documents."""
    before = snapshot(user)
    user.disabled_at = now if disabled else None
    await session.flush()
    await record(
        session,
        household_id=user.household_id,
        entity="app_user",
        entity_id=user.id,
        action=AuditAction.UPDATE,
        before=before,
        after=snapshot(user),
        actor=actor,
    )
    if not disabled:
        return 0, 0
    return (
        await revoke_user_sessions(session, user.id, now),
        await invalidate_unused_tokens(session, user.id, now),
    )
