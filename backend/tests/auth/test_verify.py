"""`POST /auth/verify` (#5): cookie, single use, expiry, races, fixation, rate limit."""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession

from app.auth.tokens import hash_token
from app.db.models import AppUser, Household, UserSession
from app.domain.enums import UserRole
from tests.auth.conftest import (
    Api,
    Family,
    auth_settings,
    cookie_attributes,
    random_email,
    running_app,
    session_cookie_value,
    set_cookies,
)

INVALID = {"detail": "invalid_or_expired"}


async def _sessions(db_session: AsyncSession) -> list[UserSession]:
    return list((await db_session.scalars(select(UserSession))).all())


async def test_valid_token_sets_host_cookie(
    api: Api, family: Family, db_session: AsyncSession
) -> None:
    token = await api.request_link(family.owner.email, next_path="/belege")
    response = await api.post("/auth/verify", json={"token": token})
    assert response.status_code == 200
    assert response.json() == {"next": "/belege"}
    assert response.headers["cache-control"] == "no-store"

    cookies = set_cookies(response)
    assert len(cookies) == 1
    attrs = cookie_attributes(cookies[0])
    assert attrs["__name"] == "__Host-belegbot_session"
    value = attrs.pop("__value")
    assert len(value) == 43
    assert {k: v for k, v in attrs.items() if k != "__name"} == {
        "httponly": "",
        "secure": "",
        "samesite": "Lax",
        "path": "/",
        "max-age": "2592000",
    }
    assert "domain" not in attrs and "expires" not in attrs

    rows = await _sessions(db_session)
    assert len(rows) == 1
    assert rows[0].token_hash == hash_token(value)
    assert rows[0].user_id == family.owner.id


async def test_localhost_cookie_has_plain_name_and_no_secure(
    migrated_database: str, db_connection: AsyncConnection, family: Family
) -> None:
    settings = auth_settings(migrated_database, app_base_url="http://localhost:3000")
    async with running_app(settings, conn=db_connection) as api:
        api.base_url = "http://localhost:3000"
        token = await api.request_link(family.owner.email)
        response = await api.post("/auth/verify", json={"token": token})
    assert response.status_code == 200
    cookies = set_cookies(response)
    assert len(cookies) == 1
    attrs = cookie_attributes(cookies[0])
    assert attrs["__name"] == "belegbot_session"
    attrs.pop("__value")
    assert {k: v for k, v in attrs.items() if k != "__name"} == {
        "httponly": "",
        "samesite": "Lax",
        "path": "/",
        "max-age": "2592000",
    }


async def test_failures_are_identical_and_create_no_session(
    api: Api, family: Family, db_session: AsyncSession
) -> None:
    used = await api.request_link(family.owner.email)
    assert (await api.post("/auth/verify", json={"token": used})).status_code == 200
    sessions_before = len(await _sessions(db_session))

    expired = await api.request_link(family.member.email)
    api.clock.advance(timedelta(minutes=15))  # exactly expires_at

    bodies = [
        {"token": used},
        {"token": expired},
        {"token": secrets.token_urlsafe(32)},
        {"token": ""},
        {"token": "x" * 10_240},
        {"token": None},
        {},
    ]
    responses = [await api.post("/auth/verify", json=body) for body in bodies]
    for response in responses:
        assert response.status_code == 400
        assert response.json() == INVALID
        assert response.content == responses[0].content
        assert not set_cookies(response)
    assert len(await _sessions(db_session)) == sessions_before


async def test_token_works_one_second_before_expiry(api: Api, family: Family) -> None:
    token = await api.request_link(family.owner.email)
    api.clock.advance(timedelta(minutes=15) - timedelta(seconds=1))
    response = await api.post("/auth/verify", json={"token": token})
    assert response.status_code == 200


async def test_older_unused_link_is_burned_by_login(api: Api, family: Family) -> None:
    older = await api.request_link(family.owner.email)
    newer = await api.request_link(family.owner.email)
    assert (await api.post("/auth/verify", json={"token": newer})).status_code == 200
    response = await api.post("/auth/verify", json={"token": older})
    assert response.status_code == 400
    assert response.json() == INVALID


async def test_user_disabled_after_request_gets_400(
    api: Api, family: Family, db_session: AsyncSession
) -> None:
    token = await api.request_link(family.owner.email)
    family.owner.disabled_at = api.clock.now()
    await db_session.commit()
    response = await api.post("/auth/verify", json={"token": token})
    assert response.status_code == 400
    assert response.json() == INVALID
    assert not set_cookies(response)


async def test_verify_with_existing_cookie_rotates_session(
    api: Api, family: Family, db_session: AsyncSession
) -> None:
    old = await api.login(family.owner.email)
    token = await api.request_link(family.owner.email)
    response = await api.post("/auth/verify", json={"token": token}, cookie=old)
    assert response.status_code == 200
    new = session_cookie_value(response, api.cookie_name)
    assert new and new != old

    rows = {r.token_hash: r for r in await _sessions(db_session)}
    await db_session.refresh(rows[hash_token(old)])
    assert rows[hash_token(old)].revoked_at is not None
    assert (await api.get("/auth/me", cookie=old)).status_code == 401
    assert (await api.get("/auth/me", cookie=new)).status_code == 200


async def test_get_verify_is_405(api: Api) -> None:
    response = await api.get("/auth/verify")
    assert response.status_code == 405


async def test_21st_failed_verify_from_one_ip_is_429(api: Api, family: Family) -> None:
    for _ in range(20):
        response = await api.post("/auth/verify", json={"token": secrets.token_urlsafe(32)})
        assert response.status_code == 400
    blocked = await api.post("/auth/verify", json={"token": secrets.token_urlsafe(32)})
    assert blocked.status_code == 429
    assert blocked.json() == {"detail": "rate_limited"}
    assert int(blocked.headers["retry-after"]) > 0
    # Even a valid token is refused from that IP until the window passes; others are fine.
    token = await api.request_link(family.owner.email, ip="198.51.100.77")
    assert (await api.post("/auth/verify", json={"token": token})).status_code == 429
    assert (
        await api.post("/auth/verify", json={"token": token}, ip="198.51.100.77")
    ).status_code == 200


@pytest.fixture
async def committed_user(db_engine: AsyncEngine) -> AsyncIterator[tuple[Household, AppUser]]:
    """A household + user committed for real (for the two-connection race), removed after."""
    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        household = Household(name="Racehaushalt")
        session.add(household)
        await session.flush()
        user = AppUser(household_id=household.id, email=random_email("race"), role=UserRole.OWNER)
        session.add(user)
        await session.commit()
    try:
        yield household, user
    finally:
        async with AsyncSession(db_engine) as session:
            await session.execute(delete(AppUser).where(AppUser.id == user.id))  # cascades
            await session.execute(delete(Household).where(Household.id == household.id))
            await session.commit()


async def test_concurrent_verifies_have_one_winner(
    migrated_database: str, committed_user: tuple[Household, AppUser], db_engine: AsyncEngine
) -> None:
    _, user = committed_user
    # No connection override: each request gets its own pooled connection and commits.
    async with running_app(auth_settings(migrated_database)) as api:
        token = await api.request_link(user.email)
        first, second = await asyncio.gather(
            api.post("/auth/verify", json={"token": token}, ip="198.51.100.1"),
            api.post("/auth/verify", json={"token": token}, ip="198.51.100.2"),
        )
    assert sorted([first.status_code, second.status_code]) == [200, 400]
    async with AsyncSession(db_engine) as session:
        count = len(
            (await session.scalars(select(UserSession).where(UserSession.user_id == user.id))).all()
        )
    assert count == 1
