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
from app.db.models import AppUser, MagicLinkToken, UserSession

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
    session: AsyncSession, user: AppUser, *, redirect_path: str, now: datetime, ttl: timedelta
) -> str:
    """Insert a token row (hash only) and return the raw token for the link."""
    token = new_token()
    session.add(
        MagicLinkToken(
            household_id=user.household_id,
            user_id=user.id,
            token_hash=hash_token(token),
            redirect_path=redirect_path,
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


async def consume_link_token(
    session: AsyncSession, token: str, now: datetime
) -> ConsumedToken | None:
    """Mark the token used in one atomic UPDATE; concurrent calls have exactly one winner."""
    result = await session.execute(
        update(MagicLinkToken)
        .where(
            MagicLinkToken.token_hash == hash_token(token),
            MagicLinkToken.used_at.is_(None),
            MagicLinkToken.expires_at > now,
        )
        .values(used_at=now)
        .returning(
            MagicLinkToken.id,
            MagicLinkToken.user_id,
            MagicLinkToken.household_id,
            MagicLinkToken.redirect_path,
        )
        .execution_options(synchronize_session=False)
    )
    row = result.one_or_none()
    if row is None:
        return None
    return ConsumedToken(row.id, row.user_id, row.household_id, row.redirect_path)


async def invalidate_unused_tokens(session: AsyncSession, user_id: uuid.UUID, now: datetime) -> int:
    result = await session.execute(
        update(MagicLinkToken)
        .where(MagicLinkToken.user_id == user_id, MagicLinkToken.used_at.is_(None))
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
