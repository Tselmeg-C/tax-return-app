"""`POST /auth/magic-link` (#5): link mail, normalisation, enumeration, rate limits, `next`."""

from __future__ import annotations

import asyncio
import io
import json
import time
from datetime import timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.auth.mail import Mail
from app.auth.tokens import hash_token
from app.db.models import MagicLinkToken
from tests.auth.conftest import (
    LINK_RE,
    Api,
    Family,
    auth_settings,
    random_email,
    running_app,
    token_from_mail,
)


async def _tokens(db_session: AsyncSession) -> list[MagicLinkToken]:
    return list((await db_session.scalars(select(MagicLinkToken))).all())


async def test_known_email_gets_one_mail_and_a_hashed_row(
    api: Api, family: Family, db_session: AsyncSession
) -> None:
    response = await api.post("/auth/magic-link", json={"email": family.owner.email})
    assert response.status_code == 202
    assert response.json() == {"status": "sent"}
    assert response.headers["cache-control"] == "no-store"
    await api.drain()

    assert len(api.outbox) == 1
    mail = api.outbox[0]
    assert mail.to == family.owner.email
    links = [line for line in mail.text.splitlines() if line.startswith("https://")]
    assert len(links) == 1 and LINK_RE.match(links[0])
    token = token_from_mail(mail)

    rows = await _tokens(db_session)
    assert len(rows) == 1
    assert rows[0].token_hash == hash_token(token)
    assert rows[0].user_id == family.owner.id

    # The raw token is in no column of any row of any table.
    tables = (
        await db_session.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        )
    ).scalars()
    for table in tables:
        dump = (await db_session.execute(text(f'SELECT t::text FROM "{table}" t'))).scalars()
        for row in dump:
            assert token not in row, table


async def test_email_is_trimmed_and_lowercased(api: Api, family: Family) -> None:
    local, domain = family.owner.email.split("@")
    messy = f"  {local.capitalize()}@{domain.upper()} "
    await api.request_link(messy)
    assert api.outbox[-1].to == family.owner.email


async def test_unknown_and_disabled_look_identical(
    api: Api, family: Family, db_session: AsyncSession
) -> None:
    known = await api.post("/auth/magic-link", json={"email": family.owner.email})
    await api.drain()
    db_rows_before = len(await _tokens(db_session))
    mails_before = len(api.outbox)

    family.member.disabled_at = api.clock.now()
    await db_session.commit()
    unknown = await api.post("/auth/magic-link", json={"email": random_email("nobody")})
    disabled = await api.post("/auth/magic-link", json={"email": family.member.email})
    await api.drain()

    for response in (unknown, disabled):
        assert response.status_code == known.status_code == 202
        assert response.content == known.content
        assert response.headers["content-type"] == known.headers["content-type"]
    assert len(api.outbox) == mails_before
    assert len(await _tokens(db_session)) == db_rows_before


@pytest.mark.parametrize(
    "bad",
    ["foo", "a b@example.com", "x" * 309 + "@example.com", "", None, 42, ["a@example.com"]],
    ids=["no-at", "space", "321-chars", "empty", "null", "number", "list"],
)
async def test_invalid_email_is_422_without_echo(api: Api, bad: object) -> None:
    if isinstance(bad, str) and bad.startswith("x"):
        assert len(bad) == 321
    response = await api.post("/auth/magic-link", json={"email": bad})
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_email"}
    if isinstance(bad, str) and bad:
        assert bad not in response.text


async def test_non_json_body_is_422_without_echo(api: Api) -> None:
    response = await api.request(
        "POST",
        "/auth/magic-link",
        headers={"Content-Type": "application/json"},
        json=None,
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_email"}


async def test_per_email_limit_15_min_and_24_h(api: Api, family: Family) -> None:
    email = family.owner.email
    for _ in range(3):
        await api.request_link(email)
    fourth = await api.post("/auth/magic-link", json={"email": email})
    await api.drain()
    assert fourth.status_code == 202 and fourth.json() == {"status": "sent"}
    assert len(api.outbox) == 3

    api.clock.advance(timedelta(minutes=15))
    await api.request_link(email)  # allowed again
    assert len(api.outbox) == 4

    # Fill up to 10 within 24 h, keeping at most 3 per 15 min.
    sent = 4
    while sent < 10:
        api.clock.advance(timedelta(minutes=16))
        for _ in range(min(3, 10 - sent)):
            await api.request_link(email)
            sent += 1
    assert len(api.outbox) == 10
    api.clock.advance(timedelta(minutes=16))
    eleventh = await api.post("/auth/magic-link", json={"email": email})
    await api.drain()
    assert eleventh.status_code == 202 and eleventh.json() == {"status": "sent"}
    assert len(api.outbox) == 10


async def test_per_ip_limit(api: Api, family: Family) -> None:
    for n in range(10):
        response = await api.post("/auth/magic-link", json={"email": random_email(f"m{n}")})
        assert response.status_code == 202
    eleventh = await api.post("/auth/magic-link", json={"email": family.owner.email})
    assert eleventh.status_code == 429
    assert eleventh.json() == {"detail": "rate_limited"}
    assert int(eleventh.headers["retry-after"]) > 0
    assert eleventh.headers["cache-control"] == "no-store"

    other = await api.post("/auth/magic-link", json={"email": random_email()}, ip="198.51.100.99")
    assert other.status_code == 202

    api.clock.advance(timedelta(minutes=15))
    again = await api.post("/auth/magic-link", json={"email": random_email()})
    assert again.status_code == 202


class SlowBackend:
    def __init__(self) -> None:
        self.outbox: list[Mail] = []

    async def send(self, mail: Mail) -> None:
        await asyncio.sleep(2)
        self.outbox.append(mail)


class FailingBackend:
    outbox: list[Mail] = []

    async def send(self, mail: Mail) -> None:
        raise ConnectionError(f"cannot reach mail server for {mail.to}")


async def test_slow_mail_does_not_delay_the_response(
    migrated_database: str, db_connection: AsyncConnection, family: Family
) -> None:
    backend = SlowBackend()
    async with running_app(
        auth_settings(migrated_database), backend=backend, conn=db_connection
    ) as api:
        started = time.monotonic()
        response = await api.post("/auth/magic-link", json={"email": family.owner.email})
        elapsed = time.monotonic() - started
        assert response.status_code == 202
        assert elapsed < 0.5, elapsed
        await api.drain()
    assert len(backend.outbox) == 1


async def test_mail_failure_still_202_and_logs_class_only(
    migrated_database: str, db_connection: AsyncConnection, family: Family, json_log: io.StringIO
) -> None:
    async with running_app(
        auth_settings(migrated_database), backend=FailingBackend(), conn=db_connection
    ) as api:
        response = await api.post("/auth/magic-link", json={"email": family.owner.email})
        assert response.status_code == 202
        assert response.json() == {"status": "sent"}
        await api.drain()
    lines = [json.loads(line) for line in json_log.getvalue().splitlines() if line.strip()]
    failed = [line for line in lines if line["event"] == "auth.mail.failed"]
    assert len(failed) == 1
    assert failed[0]["error_kind"] == "ConnectionError"
    assert family.owner.email not in json_log.getvalue()
    assert "cannot reach" not in json_log.getvalue()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/belege", "/belege"),
        ("/belege?jahr=2025", "/belege?jahr=2025"),
        (None, "/"),
        ("//evil.example", "/"),
        ("https://evil.example", "/"),
        ("/\\evil.example", "/"),
        ("javascript:alert(1)", "/"),
        ("/" + "a" * 512, "/"),
    ],
    ids=["belege", "query", "none", "protocol-relative", "absolute", "backslash", "js", "513"],
)
async def test_next_comes_back_from_verify_sanitised(
    api: Api, family: Family, raw: str | None, expected: str
) -> None:
    if raw is not None and raw.startswith("/a"):
        assert len(raw) == 513
    token = await api.request_link(family.owner.email, next_path=raw)
    response = await api.post("/auth/verify", json={"token": token})
    assert response.status_code == 200
    assert response.json() == {"next": expected}


async def test_stale_tokens_are_cleaned_up(
    api: Api, family: Family, db_session: AsyncSession
) -> None:
    await api.request_link(family.owner.email)
    assert len(await _tokens(db_session)) == 1
    api.clock.advance(timedelta(minutes=15) + timedelta(hours=24, seconds=1))
    await api.request_link(family.member.email)
    rows = await _tokens(db_session)
    assert [r.user_id for r in rows] == [family.member.id]
