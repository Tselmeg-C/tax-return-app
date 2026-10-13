"""Chat links and one-time link codes (#11).

The second place (after `app/auth/service.py`) that queries tables without a `HouseholdScope`:
a chat or a code is looked up by hash before any household is known (`channel_link`,
`channel_link_code`, `app_user.disabled_at`). Everything after the lookup uses
`HouseholdScope(session, link.household_id)`.

Never log a chat id, a code, a hash or a token. Times come from the caller's clock.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.tokens import hash_token
from app.db.audit import Actor, record, snapshot
from app.db.models import AppUser, ChannelLink, ChannelLinkCode
from app.db.scope import HouseholdScope
from app.domain.enums import AuditAction, Channel

CODE_TTL = timedelta(minutes=10)
CODE_RETENTION = timedelta(hours=24)
CODE_DIGITS = 8


def external_hash(channel: Channel, external_id: str) -> str:
    return hashlib.sha256(f"{channel.value}:{external_id}".encode()).hexdigest()


def normalise_code(raw: str) -> str | None:
    """The 8 digits of what a user typed (spaces and dashes allowed), else `None`."""
    code = "".join(ch for ch in raw if ch not in " -")
    return code if len(code) == CODE_DIGITS and code.isascii() and code.isdigit() else None


@dataclass(frozen=True)
class LinkedUser:
    link_id: uuid.UUID
    user_id: uuid.UUID
    household_id: uuid.UUID
    year: int | None
    last_update_id: int | None
    disabled: bool


def _linked(link: ChannelLink, user: AppUser) -> LinkedUser:
    return LinkedUser(
        link.id,
        link.user_id,
        link.household_id,
        link.year,
        link.last_update_id,
        user.disabled_at is not None,
    )


async def find_link(session: AsyncSession, channel: Channel, external_id: str) -> LinkedUser | None:
    row = (
        await session.execute(
            select(ChannelLink, AppUser)
            .join(AppUser, AppUser.id == ChannelLink.user_id)
            .where(
                ChannelLink.channel == channel,
                ChannelLink.external_id_hash == external_hash(channel, external_id),
            )
        )
    ).one_or_none()
    return None if row is None else _linked(*row)


async def find_link_of_user(
    session: AsyncSession, user_id: uuid.UUID, channel: Channel
) -> tuple[LinkedUser, str] | None:
    """The user's link and the chat id to send to (notifications). Disabled user: `None`."""
    row = (
        await session.execute(
            select(ChannelLink, AppUser)
            .join(AppUser, AppUser.id == ChannelLink.user_id)
            .where(ChannelLink.user_id == user_id, ChannelLink.channel == channel)
        )
    ).one_or_none()
    if row is None or row[1].disabled_at is not None:
        return None
    return _linked(*row), row[0].external_id


async def claim_update(session: AsyncSession, link_id: uuid.UUID, update_id: int) -> bool:
    """Atomically move `last_update_id` forward; `False` for a repeated or older update."""
    result = await session.execute(
        update(ChannelLink)
        .where(
            ChannelLink.id == link_id,
            (ChannelLink.last_update_id.is_(None)) | (ChannelLink.last_update_id < update_id),
        )
        .values(last_update_id=update_id)
        .returning(ChannelLink.id)
        .execution_options(synchronize_session=False)
    )
    claimed = result.scalar_one_or_none() is not None
    await session.commit()
    return claimed


async def set_year(session: AsyncSession, link_id: uuid.UUID, year: int) -> None:
    await session.execute(
        update(ChannelLink)
        .where(ChannelLink.id == link_id)
        .values(year=year)
        .execution_options(synchronize_session=False)
    )
    await session.commit()


async def _delete_link(scope: HouseholdScope, link: ChannelLink, actor: Actor) -> None:
    before = snapshot(link)
    await scope.session.delete(link)
    await scope.session.flush()
    await record(
        scope.session,
        household_id=scope.household_id,
        entity="channel_link",
        entity_id=link.id,
        action=AuditAction.DELETE,
        before=before,
        after=None,
        actor=actor,
    )


async def unlink_user(scope: HouseholdScope, user_id: uuid.UUID, channel: Channel) -> bool:
    """Delete the user's link (audited, commits). `False` if there was none (idempotent)."""
    link = (
        await scope.session.execute(
            scope.select(ChannelLink).where(
                ChannelLink.user_id == user_id, ChannelLink.channel == channel
            )
        )
    ).scalar_one_or_none()
    if link is None:
        return False
    await _delete_link(scope, link, Actor.user(user_id))
    await scope.session.commit()
    return True


async def issue_code(
    scope: HouseholdScope, user_id: uuid.UUID, now: datetime
) -> tuple[str, datetime]:
    """A new code for the user; their open codes stop working. Commits. Raw code returned once."""
    session = scope.session
    await session.execute(
        delete(ChannelLinkCode).where(ChannelLinkCode.expires_at < now - CODE_RETENTION)
    )
    await session.execute(
        update(ChannelLinkCode)
        .where(ChannelLinkCode.user_id == user_id, ChannelLinkCode.used_at.is_(None))
        .values(used_at=now)
        .execution_options(synchronize_session=False)
    )
    expires_at = now + CODE_TTL
    for _ in range(5):  # a hash collision with another user's code is possible, not likely
        code = f"{secrets.randbelow(10**CODE_DIGITS):0{CODE_DIGITS}d}"
        try:
            async with session.begin_nested():
                scope.add(
                    ChannelLinkCode(
                        user_id=user_id, code_hash=hash_token(code), expires_at=expires_at
                    )
                )
        except IntegrityError:
            continue
        await session.commit()
        return code, expires_at
    raise RuntimeError("no free link code")  # pragma: no cover


@dataclass(frozen=True)
class ConsumedCode:
    user_id: uuid.UUID
    household_id: uuid.UUID


async def consume_code(session: AsyncSession, code: str, now: datetime) -> ConsumedCode | None:
    """Mark the code used in one atomic UPDATE (one winner); `None` for unknown, expired, used
    or a disabled user's code. Commits."""
    result = await session.execute(
        update(ChannelLinkCode)
        .where(
            ChannelLinkCode.code_hash == hash_token(code),
            ChannelLinkCode.used_at.is_(None),
            ChannelLinkCode.expires_at > now,
        )
        .values(used_at=now)
        .returning(ChannelLinkCode.user_id, ChannelLinkCode.household_id)
        .execution_options(synchronize_session=False)
    )
    row = result.one_or_none()
    disabled = False
    if row is not None:
        disabled = (
            await session.scalar(select(AppUser.disabled_at).where(AppUser.id == row.user_id))
        ) is not None
    await session.commit()
    if row is None or disabled:
        return None
    return ConsumedCode(row.user_id, row.household_id)


async def create_link(
    scope: HouseholdScope, user_id: uuid.UUID, channel: Channel, external_id: str
) -> bool:
    """Link the chat to the user, replacing the user's old link (audited as delete + create).

    `False` if the chat already belongs to someone else (unlink on the web first).
    """
    session = scope.session
    taken = await session.scalar(
        select(ChannelLink.user_id).where(
            ChannelLink.channel == channel,
            ChannelLink.external_id_hash == external_hash(channel, external_id),
        )
    )
    if taken is not None:
        return False
    actor = Actor.user(user_id)
    old = (
        await session.execute(
            scope.select(ChannelLink).where(
                ChannelLink.user_id == user_id, ChannelLink.channel == channel
            )
        )
    ).scalar_one_or_none()
    if old is not None:
        await _delete_link(scope, old, actor)
    link = ChannelLink(
        user_id=user_id,
        channel=channel,
        external_id=external_id,
        external_id_hash=external_hash(channel, external_id),
    )
    try:
        scope.add(link)
        await session.flush()
    except IntegrityError:  # the chat was linked at the same moment
        await session.rollback()
        return False
    await record(
        session,
        household_id=scope.household_id,
        entity="channel_link",
        entity_id=link.id,
        action=AuditAction.CREATE,
        before=None,
        after=snapshot(link),
        actor=actor,
    )
    await session.commit()
    return True
