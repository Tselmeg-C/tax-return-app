"""`python -m app.auth.cli` (#5): bootstrap, invite, disable, enable, revoke-sessions."""

from __future__ import annotations

import io
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.auth.cli import run
from app.db.models import AppUser, AuditLog, Document, Household
from app.domain.enums import ActorType, AuditAction, UserRole
from tests.auth.conftest import Api, Family, random_email
from tests.domain.factories import make_document

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Result:
    def __init__(self, code: int, out: str, err: str) -> None:
        self.code, self.out, self.err = code, out, err

    @property
    def text(self) -> str:
        return self.out + self.err


async def cli(session: AsyncSession, *argv: str, api: Api | None = None) -> Result:
    out, err = io.StringIO(), io.StringIO()
    code = await run(list(argv), session, out=out, err=err, clock=api.clock if api else None)
    return Result(code, out.getvalue(), err.getvalue())


async def _count(session: AsyncSession, model: type) -> int:
    return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def test_bootstrap_once(db_session: AsyncSession) -> None:
    assert await _count(db_session, Household) == 0, "test DB must start without households"
    email = random_email("Boot").replace("Boot", "BOOT")
    first = await cli(db_session, "bootstrap", "--household-name", "Familie Test", "--email", email)
    assert first.code == 0, first.text
    assert await _count(db_session, Household) == 1
    users = (await db_session.scalars(select(AppUser))).all()
    assert len(users) == 1
    assert users[0].role is UserRole.OWNER
    assert users[0].email == email.lower()

    again = await cli(
        db_session, "bootstrap", "--household-name", "Zweite", "--email", random_email()
    )
    assert again.code != 0
    assert await _count(db_session, Household) == 1
    assert await _count(db_session, AppUser) == 1
    for result in (first, again):
        assert email.lower() not in result.text.lower()


async def test_invite_lowercases_and_refuses_duplicates(
    db_session: AsyncSession, family: Family
) -> None:
    token = random_email("new").split("@")[0].split("-")[1]
    raw = f"New-{token}@Example.com"
    first = await cli(db_session, "invite", "--email", raw)
    assert first.code == 0, first.text
    user = await db_session.scalar(select(AppUser).where(AppUser.email == raw.lower()))
    assert user is not None
    assert user.role is UserRole.MEMBER
    assert user.household_id == family.household.id
    assert "n***@example.com" in first.out
    assert raw.lower() not in first.text.lower()

    audit = (await db_session.scalars(select(AuditLog).where(AuditLog.entity_id == user.id))).all()
    assert len(audit) == 1
    assert audit[0].actor_type is ActorType.SYSTEM
    assert audit[0].action is AuditAction.CREATE

    duplicate = await cli(db_session, "invite", "--email", raw.upper())
    assert duplicate.code != 0
    assert raw.lower() not in duplicate.text.lower()


async def test_invite_needs_household_choice_when_several(
    db_session: AsyncSession, family: Family
) -> None:
    other = Household(name="Andere")
    db_session.add(other)
    await db_session.commit()
    other_id = other.id  # the refusal rolls back, which expires loaded objects
    ambiguous = await cli(db_session, "invite", "--email", random_email())
    assert ambiguous.code != 0
    email = random_email()
    chosen = await cli(
        db_session, "invite", "--email", email, "--role", "owner", "--household-id", str(other_id)
    )
    assert chosen.code == 0, chosen.text
    user = await db_session.scalar(select(AppUser).where(AppUser.email == email))
    assert user is not None and user.household_id == other_id and user.role is UserRole.OWNER


async def test_disable_enable_revoke(api: Api, db_session: AsyncSession, family: Family) -> None:
    owner = family.owner.email
    cookie = await api.login(owner)
    api.clock.advance(timedelta(minutes=1))
    pending = await api.request_link(owner)
    doc = await make_document(db_session, family.household, family.owner)
    await db_session.commit()

    disabled = await cli(db_session, "disable", "--email", owner, api=api)
    assert disabled.code == 0, disabled.text
    assert (await api.get("/auth/me", cookie=cookie)).status_code == 401
    assert (await api.post("/auth/verify", json={"token": pending})).status_code == 400
    # Documents are untouched.
    assert await db_session.get(Document, doc.id) is not None
    audit = (
        await db_session.scalars(
            select(AuditLog).where(
                AuditLog.entity_id == family.owner.id, AuditLog.action == AuditAction.UPDATE
            )
        )
    ).all()
    assert len(audit) == 1 and audit[0].actor_type is ActorType.SYSTEM

    # Disabled users get no new link either.
    before = len(api.outbox)
    await api.post("/auth/magic-link", json={"email": owner})
    await api.drain()
    assert len(api.outbox) == before

    enabled = await cli(db_session, "enable", "--email", owner, api=api)
    assert enabled.code == 0, enabled.text
    api.clock.advance(timedelta(minutes=16))
    new_cookie = await api.login(owner)
    member_cookie = await api.login(family.member.email)

    revoked = await cli(db_session, "revoke-sessions", "--email", owner, api=api)
    assert revoked.code == 0, revoked.text
    assert (await api.get("/auth/me", cookie=new_cookie)).status_code == 401
    assert (await api.get("/auth/me", cookie=member_cookie)).status_code == 200
    # revoke-sessions does not disable: logging in again works.
    api.clock.advance(timedelta(minutes=16))
    await api.login(owner)

    for result in (disabled, enabled, revoked):
        assert owner not in result.text


async def test_unknown_user_and_bad_email_exit_non_zero(db_session: AsyncSession) -> None:
    email = random_email("ghost")
    missing = await cli(db_session, "disable", "--email", email)
    assert missing.code == 1
    assert email not in missing.text
    bad = await cli(db_session, "enable", "--email", "not-an-address")
    assert bad.code == 1
    assert "not-an-address" not in bad.text


@pytest.fixture
async def committed_household(db_engine: AsyncEngine) -> AsyncIterator[Household]:
    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        household = Household(name="CLI-Prod-Test")
        session.add(household)
        await session.commit()
    try:
        yield household
    finally:
        async with AsyncSession(db_engine) as session:
            await session.execute(delete(AuditLog).where(AuditLog.household_id == household.id))
            await session.execute(delete(AppUser).where(AppUser.household_id == household.id))
            await session.execute(delete(Household).where(Household.id == household.id))
            await session.commit()


def test_cli_runs_in_production(migrated_database: str, committed_household: Household) -> None:
    email = random_email("prod")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("OTEL_", "MAIL_", "RESEND_"))}
    env.update(
        {
            "DATABASE_URL": migrated_database,
            "APP_ENV": "production",
            "APP_BASE_URL": "http://not-https.example",  # api rules do not apply to the CLI
        }
    )
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.auth.cli",
            "invite",
            "--email",
            email,
            "--household-id",
            str(committed_household.id),
        ],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert email not in proc.stdout + proc.stderr
    assert "p***@example.com" in proc.stdout
