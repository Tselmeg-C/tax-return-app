"""User management CLI: `uv run python -m app.auth.cli <command> ...`.

Commands: `bootstrap`, `invite`, `disable`, `enable`, `revoke-sessions`. Allowed in production
(this is how the real users get created, via `railway ssh --service api`). Output shows ids
and masked e-mails (`o***@example.com`) only. Changes are audited with `Actor.system()`.

`disable` only blocks login (sets `disabled_at`, ends sessions, burns unused links); it never
touches the household's documents. There is deliberately no `delete` command.

Exit codes: 0 ok, 1 refused / not found / invalid input, 2 usage error (argparse).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from collections.abc import Sequence
from typing import TextIO

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import service
from app.auth.clock import Clock, SystemClock
from app.auth.tokens import mask_email, normalise_email
from app.config import get_settings
from app.db.audit import Actor, record, snapshot
from app.db.models import AppUser, Household
from app.db.session import create_engine, create_sessionmaker
from app.domain.enums import AuditAction, UserRole

PROG = "python -m app.auth.cli"


class CliError(Exception):
    """A refusal with a message that is safe to print (no full e-mail)."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=PROG, description="belegbot user management")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("bootstrap", help="create the first household and its owner")
    p.add_argument("--household-name", required=True)
    p.add_argument("--email", required=True)

    p = sub.add_parser("invite", help="add a user to a household")
    p.add_argument("--email", required=True)
    p.add_argument("--role", choices=[r.value for r in UserRole], default=UserRole.MEMBER.value)
    p.add_argument("--household-id", type=uuid.UUID, default=None)

    for name, help_text in (
        ("disable", "block login, end sessions, burn unused links"),
        ("enable", "allow login again"),
        ("revoke-sessions", "end all sessions of a user"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--email", required=True)
    return parser


def _email(raw: str) -> str:
    email = normalise_email(raw)
    if email is None:
        raise CliError("invalid e-mail address")
    return email


async def _user(session: AsyncSession, email: str) -> AppUser:
    user = await service.find_user_by_email(session, email)
    if user is None:
        raise CliError(f"no user {mask_email(email)}")
    return user


async def _bootstrap(session: AsyncSession, args: argparse.Namespace, out: TextIO) -> None:
    email = _email(args.email)
    name = args.household_name.strip()
    if not name or len(name) > 100:
        raise CliError("household name must be 1-100 characters")
    if (await session.scalar(select(func.count()).select_from(Household))) != 0:
        raise CliError("refusing: a household already exists (use invite)")
    household = Household(name=name)
    session.add(household)
    await session.flush()
    user = AppUser(household_id=household.id, email=email, role=UserRole.OWNER)
    session.add(user)
    await session.flush()
    await record(
        session,
        household_id=household.id,
        entity="app_user",
        entity_id=user.id,
        action=AuditAction.CREATE,
        before=None,
        after=snapshot(user),
        actor=Actor.system(),
    )
    print(f"created household {household.id} and owner {user.id} ({mask_email(email)})", file=out)


async def _invite(session: AsyncSession, args: argparse.Namespace, out: TextIO) -> None:
    email = _email(args.email)
    if await service.find_user_by_email(session, email) is not None:
        raise CliError(f"refusing: {mask_email(email)} is already a user")
    if args.household_id is not None:
        household = await session.get(Household, args.household_id)
        if household is None:
            raise CliError(f"no household {args.household_id}")
    else:
        households = (await session.scalars(select(Household).limit(2))).all()
        if len(households) != 1:
            raise CliError(
                "no household (run bootstrap)"
                if not households
                else "several households: pass --household-id"
            )
        household = households[0]
    user = AppUser(household_id=household.id, email=email, role=UserRole(args.role))
    session.add(user)
    await session.flush()
    await record(
        session,
        household_id=household.id,
        entity="app_user",
        entity_id=user.id,
        action=AuditAction.CREATE,
        before=None,
        after=snapshot(user),
        actor=Actor.system(),
    )
    print(f"invited {user.role.value} {user.id} ({mask_email(email)})", file=out)


async def _set_disabled(
    session: AsyncSession, args: argparse.Namespace, out: TextIO, clock: Clock, disabled: bool
) -> None:
    email = _email(args.email)
    user = await _user(session, email)
    now = clock.now()
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
        actor=Actor.system(),
    )
    if disabled:
        sessions = await service.revoke_user_sessions(session, user.id, now)
        links = await service.invalidate_unused_tokens(session, user.id, now)
        print(
            f"disabled {user.id} ({mask_email(email)}): "
            f"{sessions} session(s) ended, {links} link(s) invalidated",
            file=out,
        )
    else:
        print(f"enabled {user.id} ({mask_email(email)})", file=out)


async def _revoke_sessions(
    session: AsyncSession, args: argparse.Namespace, out: TextIO, clock: Clock
) -> None:
    email = _email(args.email)
    user = await _user(session, email)
    count = await service.revoke_user_sessions(session, user.id, clock.now())
    print(f"ended {count} session(s) of {user.id} ({mask_email(email)})", file=out)


async def run(
    argv: Sequence[str],
    session: AsyncSession,
    *,
    out: TextIO | None = None,
    err: TextIO | None = None,
    clock: Clock | None = None,
) -> int:
    """Run one command in `session` and commit it; returns the exit code."""
    out = out or sys.stdout
    err = err or sys.stderr
    the_clock = clock or SystemClock()
    args = build_parser().parse_args(list(argv))
    try:
        if args.command == "bootstrap":
            await _bootstrap(session, args, out)
        elif args.command == "invite":
            await _invite(session, args, out)
        elif args.command in ("disable", "enable"):
            await _set_disabled(session, args, out, the_clock, args.command == "disable")
        else:
            await _revoke_sessions(session, args, out, the_clock)
    except CliError as exc:
        await session.rollback()
        print(f"error: {exc}", file=err)
        return 1
    await session.commit()
    return 0


async def _main(argv: Sequence[str]) -> int:
    settings = get_settings()
    engine = create_engine(settings)
    try:
        async with create_sessionmaker(engine)() as session:
            return await run(argv, session)
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    build_parser().parse_args(args)  # usage errors exit 2 before touching the DB
    try:
        return asyncio.run(_main(args))
    except Exception as exc:
        # Class name only: messages can contain row values (e-mail) or connection details.
        print(f"error: failed ({type(exc).__name__})", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
