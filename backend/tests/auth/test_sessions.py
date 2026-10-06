"""Sessions (#5): `/auth/me`, expiry, `last_seen_at`, revocation, cascade, scope, logout."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Scope
from app.auth.tokens import hash_token
from app.db.models import AppUser, MagicLinkToken, Person, UserSession
from app.db.scope import HouseholdScope
from tests.auth.conftest import Api, Family, cookie_attributes, set_cookies
from tests.domain.factories import make_world

NOT_AUTH = {"detail": "not_authenticated"}


async def _session_row(db_session: AsyncSession, cookie: str) -> UserSession:
    row = await db_session.scalar(
        select(UserSession)
        .where(UserSession.token_hash == hash_token(cookie))
        .execution_options(populate_existing=True)
    )
    assert row is not None
    return row


async def test_me_with_and_without_cookie(api: Api, family: Family) -> None:
    cookie = await api.login(family.owner.email)
    response = await api.get("/auth/me", cookie=cookie)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "user_id": str(family.owner.id),
        "email": family.owner.email,
        "role": "owner",
        "household_id": str(family.household.id),
        "household_name": "Testhaushalt",
    }

    anonymous = await api.get("/auth/me")
    assert anonymous.status_code == 401
    assert anonymous.json() == NOT_AUTH
    assert anonymous.headers["cache-control"] == "no-store"
    assert not set_cookies(anonymous)

    garbage = await api.get("/auth/me", cookie="garbage-cookie-value")
    assert garbage.status_code == 401
    assert garbage.json() == NOT_AUTH
    cleared = [cookie_attributes(c) for c in set_cookies(garbage)]
    assert len(cleared) == 1
    assert cleared[0]["__name"] == api.cookie_name
    assert cleared[0]["__value"] == ""
    assert cleared[0]["max-age"] == "0"
    assert cleared[0]["path"] == "/"


async def test_idle_expiry(api: Api, family: Family) -> None:
    cookie = await api.login(family.owner.email)
    api.clock.advance(timedelta(days=7, seconds=1))
    assert (await api.get("/auth/me", cookie=cookie)).status_code == 401


async def test_idle_just_inside_is_fine(api: Api, family: Family) -> None:
    cookie = await api.login(family.owner.email)
    api.clock.advance(timedelta(days=7) - timedelta(seconds=1))
    assert (await api.get("/auth/me", cookie=cookie)).status_code == 200


async def test_absolute_expiry_despite_daily_use(api: Api, family: Family) -> None:
    cookie = await api.login(family.owner.email)
    for _ in range(29):
        api.clock.advance(timedelta(days=1))
        assert (await api.get("/auth/me", cookie=cookie)).status_code == 200
    api.clock.advance(timedelta(days=1))  # 30 days since login
    assert (await api.get("/auth/me", cookie=cookie)).status_code == 401


async def test_last_seen_is_touched_at_most_every_5_min(
    api: Api, family: Family, db_session: AsyncSession
) -> None:
    cookie = await api.login(family.owner.email)
    login_time = (await _session_row(db_session, cookie)).last_seen_at

    api.clock.advance(timedelta(minutes=1))
    await api.get("/auth/me", cookie=cookie)
    api.clock.advance(timedelta(minutes=1))
    await api.get("/auth/me", cookie=cookie)
    assert (await _session_row(db_session, cookie)).last_seen_at == login_time  # not touched

    api.clock.advance(timedelta(minutes=4))  # 6 min after login
    await api.get("/auth/me", cookie=cookie)
    first = (await _session_row(db_session, cookie)).last_seen_at
    assert first == api.clock.now()
    api.clock.advance(timedelta(minutes=6))
    await api.get("/auth/me", cookie=cookie)
    second = (await _session_row(db_session, cookie)).last_seen_at
    assert second == api.clock.now() and second > first


async def test_revoked_and_disabled_are_401(
    api: Api, family: Family, db_session: AsyncSession
) -> None:
    owner_cookie = await api.login(family.owner.email)
    member_cookie = await api.login(family.member.email)

    row = await _session_row(db_session, owner_cookie)
    row.revoked_at = api.clock.now()
    family.member.disabled_at = api.clock.now()
    await db_session.commit()

    assert (await api.get("/auth/me", cookie=owner_cookie)).status_code == 401
    assert (await api.get("/auth/me", cookie=member_cookie)).status_code == 401


async def test_deleting_a_user_cascades_to_tokens_and_sessions(
    api: Api, family: Family, db_session: AsyncSession
) -> None:
    # The member has no documents (document.uploaded_by_user_id is ON DELETE RESTRICT).
    await api.login(family.member.email)
    await api.request_link(family.member.email)
    member_id = family.member.id
    await db_session.execute(delete(AppUser).where(AppUser.id == member_id))
    await db_session.commit()
    tokens = await db_session.scalars(
        select(MagicLinkToken).where(MagicLinkToken.user_id == member_id)
    )
    sessions = await db_session.scalars(select(UserSession).where(UserSession.user_id == member_id))
    assert tokens.all() == []
    assert sessions.all() == []


async def test_get_scope_returns_only_own_household(api: Api, db_session: AsyncSession) -> None:
    a = await make_world(db_session, "A")
    b = await make_world(db_session, "B")
    await db_session.commit()

    seen: dict[str, object] = {}

    @api.app.get("/test/scope-persons")
    async def scope_persons(scope: Scope) -> dict[str, list[str]]:
        rows = (await scope.session.scalars(scope.select(Person))).all()
        seen["household"] = scope.household_id
        return {"ids": [str(r.id) for r in rows]}

    cookie = await api.login(a.user.email)
    response = await api.get("/test/scope-persons", cookie=cookie)
    assert response.status_code == 200
    assert response.json() == {"ids": [str(a.person.id)]}
    assert seen["household"] == a.household.id
    assert str(b.person.id) not in response.text

    scope = HouseholdScope(db_session, a.household.id)
    assert await scope.get(Person, b.person.id) is None


async def test_logout_revokes_and_clears(
    api: Api, family: Family, db_session: AsyncSession
) -> None:
    cookie = await api.login(family.owner.email)
    response = await api.post("/auth/logout", cookie=cookie)
    assert response.status_code == 204
    assert response.headers["cache-control"] == "no-store"
    cleared = [cookie_attributes(c) for c in set_cookies(response)]
    assert len(cleared) == 1
    assert cleared[0]["__name"] == api.cookie_name
    assert cleared[0]["path"] == "/"
    assert cleared[0]["max-age"] == "0"
    assert (await _session_row(db_session, cookie)).revoked_at is not None
    assert (await api.get("/auth/me", cookie=cookie)).status_code == 401

    anonymous = await api.post("/auth/logout")
    assert anonymous.status_code == 204
    assert len(set_cookies(anonymous)) == 1


async def test_logout_all_only_hits_own_sessions(api: Api, family: Family) -> None:
    laptop = await api.login(family.owner.email)
    api.clock.advance(timedelta(minutes=16))  # stay under the per-e-mail link limit
    phone = await api.login(family.owner.email)
    other = await api.login(family.member.email)

    response = await api.post("/auth/logout-all", cookie=phone)
    assert response.status_code == 204
    assert cookie_attributes(set_cookies(response)[0])["max-age"] == "0"
    assert (await api.get("/auth/me", cookie=laptop)).status_code == 401
    assert (await api.get("/auth/me", cookie=phone)).status_code == 401
    assert (await api.get("/auth/me", cookie=other)).status_code == 200


async def test_logout_all_requires_a_session(api: Api) -> None:
    response = await api.post("/auth/logout-all")
    assert response.status_code == 401
    assert response.json() == NOT_AUTH
