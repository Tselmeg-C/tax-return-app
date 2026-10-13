"""Member management (#34): role matrix, guard rails, IDOR, e-mail change, audit, privacy."""

from __future__ import annotations

import io
import logging
import re
import secrets
from datetime import timedelta

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.tokens import hash_token
from app.db.models import AppUser, AuditLog, Document, MagicLinkToken, UserSession
from app.domain.enums import ActorType, AuditAction, UserRole
from tests.auth.conftest import Api, Family, random_email, session_cookie_value, token_from_mail
from tests.domain.conftest import keys  # noqa: F401 (autouse: factories hold encrypted columns)
from tests.domain.factories import make_document, make_household, make_person, make_user

CONFIRM_RE = re.compile(r"/login/confirm-email#token=([A-Za-z0-9_-]{43})$")


async def _audit(db: AsyncSession, entity_id: object) -> list[AuditLog]:
    return list(
        (
            await db.scalars(
                select(AuditLog)
                .where(AuditLog.entity_id == entity_id)
                .order_by(AuditLog.created_at)
                .execution_options(populate_existing=True)
            )
        ).all()
    )


async def _fresh(db: AsyncSession, user: AppUser) -> AppUser:
    row = await db.scalar(
        select(AppUser).where(AppUser.id == user.id).execution_options(populate_existing=True)
    )
    assert row is not None
    return row


def _patch(api: Api, user: AppUser, cookie: str, body: dict[str, object]):  # type: ignore[no-untyped-def]
    return api.request("PATCH", f"/users/{user.id}", json=body, cookie=cookie)


# --- role matrix ------------------------------------------------------------------------


async def test_member_gets_403_everywhere_owner_only(api: Api, family: Family) -> None:
    cookie = await api.login(family.member.email)
    uid = family.owner.id
    calls = [
        ("GET", "/users", None),
        ("POST", "/users", {"email": random_email()}),
        ("PATCH", f"/users/{uid}", {"disabled": True}),
        ("GET", f"/users/{uid}/sessions", None),
        ("DELETE", f"/users/{uid}/sessions", None),
    ]
    for method, path, body in calls:
        response = await api.request(method, path, json=body, cookie=cookie)
        assert response.status_code == 403, (method, path)
        assert response.json() == {"detail": "forbidden"}


async def test_list_users(api: Api, family: Family) -> None:
    cookie = await api.login(family.owner.email)
    response = await api.get("/users", cookie=cookie)
    assert response.status_code == 200
    rows = {r["id"]: r for r in response.json()}
    assert set(rows) == {str(family.owner.id), str(family.member.id)}
    assert rows[str(family.owner.id)]["is_self"] is True
    assert rows[str(family.owner.id)]["last_login_at"] is not None
    assert rows[str(family.member.id)]["last_login_at"] is None
    assert rows[str(family.member.id)]["role"] == "member"
    assert rows[str(family.member.id)]["disabled"] is False


# --- invite -----------------------------------------------------------------------------


async def test_invite_then_login(api: Api, family: Family, db_session: AsyncSession) -> None:
    cookie = await api.login(family.owner.email)
    mails_before = len(api.outbox)
    email = random_email("new")
    response = await api.post("/users", json={"email": f" {email.upper()} "}, cookie=cookie)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["email"] == email and body["role"] == "member" and body["disabled"] is False
    await api.drain()
    invite = api.outbox[mails_before]
    assert invite.to == email and invite.subject == "Du wurdest zu belegbot eingeladen"
    token = token_from_mail(invite)
    verified = await api.post("/auth/verify", json={"token": token})
    assert verified.status_code == 200
    new_cookie = session_cookie_value(verified, api.cookie_name)
    assert new_cookie
    me = (await api.get("/auth/me", cookie=new_cookie)).json()
    assert me["email"] == email and me["household_id"] == str(family.household.id)
    # a member does not get the list
    assert (await api.get("/users", cookie=new_cookie)).status_code == 403

    rows = await _audit(db_session, body["id"])
    assert [r.action for r in rows][0] is AuditAction.CREATE
    assert rows[0].actor_type is ActorType.USER and rows[0].actor_user_id == family.owner.id
    assert email not in str(rows[0].after)  # redacted


async def test_invite_can_create_owner_and_new_user_can_request_link(
    api: Api, family: Family
) -> None:
    cookie = await api.login(family.owner.email)
    email = random_email("partner")
    created = await api.post("/users", json={"email": email, "role": "owner"}, cookie=cookie)
    assert created.status_code == 201 and created.json()["role"] == "owner"
    await api.drain()
    assert await api.login(email)


async def test_invite_validation_and_duplicates(api: Api, family: Family) -> None:
    cookie = await api.login(family.owner.email)
    for body in ({}, {"email": "nope"}, {"email": random_email(), "role": "admin"}):
        response = await api.post("/users", json=body, cookie=cookie)
        assert response.status_code == 422
        assert response.json()["detail"] == "invalid_field"
    dupe = await api.post("/users", json={"email": family.member.email.upper()}, cookie=cookie)
    assert dupe.status_code == 409 and dupe.json() == {"detail": "email_taken"}


async def test_invite_needs_csrf_header(api: Api, family: Family) -> None:
    cookie = await api.login(family.owner.email)
    response = await api.post("/users", json={"email": random_email()}, cookie=cookie, csrf=False)
    assert response.status_code == 403 and response.json()["detail"] == "csrf"


# --- disable / enable ---------------------------------------------------------------------


async def test_disable_ends_sessions_links_and_keeps_documents(
    api: Api, family: Family, db_session: AsyncSession
) -> None:
    owner_cookie = await api.login(family.owner.email)
    member_cookie = await api.login(family.member.email)
    document = await make_document(db_session, family.household, family.member)
    await db_session.commit()
    link = await api.request_link(family.member.email)  # an unused link

    response = await _patch(api, family.member, owner_cookie, {"disabled": True})
    assert response.status_code == 200 and response.json()["disabled"] is True
    assert (await api.get("/auth/me", cookie=member_cookie)).status_code == 401
    assert (await api.post("/auth/verify", json={"token": link})).status_code == 400

    kept = await db_session.scalar(
        select(Document).where(Document.id == document.id).execution_options(populate_existing=True)
    )
    assert kept is not None and kept.uploaded_by_user_id == family.member.id
    assert (await api.get("/documents", cookie=owner_cookie)).status_code == 200

    audit = await _audit(db_session, family.member.id)
    assert len(audit) == 1 and audit[0].actor_user_id == family.owner.id

    enabled = await _patch(api, family.member, owner_cookie, {"disabled": False})
    assert enabled.status_code == 200 and enabled.json()["disabled"] is False
    assert await api.login(family.member.email)


async def test_disable_is_idempotent_and_writes_no_extra_row(
    api: Api, family: Family, db_session: AsyncSession
) -> None:
    cookie = await api.login(family.owner.email)
    await _patch(api, family.member, cookie, {"disabled": True})
    await _patch(api, family.member, cookie, {"disabled": True})
    assert len(await _audit(db_session, family.member.id)) == 1


# --- last owner ---------------------------------------------------------------------------


@pytest.mark.parametrize("body", [{"disabled": True}, {"role": "member"}])
async def test_last_owner_cannot_lock_the_household_out(
    api: Api, family: Family, db_session: AsyncSession, body: dict[str, object]
) -> None:
    cookie = await api.login(family.owner.email)
    response = await _patch(api, family.owner, cookie, body)
    assert response.status_code == 409 and response.json() == {"detail": "last_owner"}
    assert (await api.get("/auth/me", cookie=cookie)).status_code == 200
    owner = await _fresh(db_session, family.owner)
    assert owner.role is UserRole.OWNER and owner.disabled_at is None
    assert await _audit(db_session, family.owner.id) == []


async def test_owner_can_step_down_when_another_active_owner_exists(
    api: Api, family: Family
) -> None:
    cookie = await api.login(family.owner.email)
    assert (await _patch(api, family.member, cookie, {"role": "owner"})).status_code == 200
    assert (await _patch(api, family.owner, cookie, {"role": "member"})).status_code == 200
    # the former owner is a member now: 403 on the next call
    assert (await api.get("/users", cookie=cookie)).status_code == 403
    # the new owner is the last one
    new_cookie = await api.login(family.member.email)
    assert (await _patch(api, family.member, new_cookie, {"disabled": True})).status_code == 409


async def test_disabled_owner_does_not_count_as_another_owner(api: Api, family: Family) -> None:
    cookie = await api.login(family.owner.email)
    await _patch(api, family.member, cookie, {"role": "owner"})
    await _patch(api, family.member, cookie, {"disabled": True})
    assert (await _patch(api, family.owner, cookie, {"role": "member"})).status_code == 409


# --- IDOR ---------------------------------------------------------------------------------


async def test_other_household_is_invisible(
    api: Api, family: Family, db_session: AsyncSession
) -> None:
    other = await make_household(db_session, "Fremd")
    stranger = await make_user(db_session, other, email=random_email("stranger"))
    person = await make_person(db_session, other)
    await db_session.commit()
    cookie = await api.login(family.owner.email)
    stranger_cookie = await api.login(stranger.email)

    for method, path, body in (
        ("PATCH", f"/users/{stranger.id}", {"disabled": True}),
        ("GET", f"/users/{stranger.id}/sessions", None),
        ("DELETE", f"/users/{stranger.id}/sessions", None),
    ):
        response = await api.request(method, path, json=body, cookie=cookie)
        assert response.status_code == 404, (method, path)
        assert response.json() == {"detail": "not_found"}
    assert (await api.get("/auth/me", cookie=stranger_cookie)).status_code == 200

    listed = {r["id"] for r in (await api.get("/users", cookie=cookie)).json()}
    assert str(stranger.id) not in listed
    # linking a person of another household is refused as invalid, without revealing it exists
    bad = await _patch(api, family.member, cookie, {"person_id": str(person.id)})
    assert bad.status_code == 422
    # own session ids of another household
    foreign = (
        await db_session.scalars(select(UserSession).where(UserSession.user_id == stranger.id))
    ).first()
    assert foreign is not None
    assert (
        await api.request("DELETE", f"/auth/sessions/{foreign.id}", cookie=cookie)
    ).status_code == 404
    assert (await api.get("/auth/me", cookie=stranger_cookie)).status_code == 200


# --- person link --------------------------------------------------------------------------


async def test_link_person_unique(api: Api, family: Family, db_session: AsyncSession) -> None:
    person = await make_person(db_session, family.household)
    await db_session.commit()
    cookie = await api.login(family.owner.email)
    ok = await _patch(api, family.member, cookie, {"person_id": str(person.id)})
    assert ok.status_code == 200 and ok.json()["person_id"] == str(person.id)
    clash = await _patch(api, family.owner, cookie, {"person_id": str(person.id)})
    assert clash.status_code == 409 and clash.json() == {"detail": "person_taken"}
    cleared = await _patch(api, family.member, cookie, {"person_id": None})
    assert cleared.status_code == 200 and cleared.json()["person_id"] is None


async def test_patch_validation_applies_nothing(
    api: Api, family: Family, db_session: AsyncSession
) -> None:
    cookie = await api.login(family.owner.email)
    response = await _patch(api, family.member, cookie, {"role": "owner", "disabled": "yes"})
    assert response.status_code == 422 and response.json()["field"] == "disabled"
    assert (await _fresh(db_session, family.member)).role is UserRole.MEMBER


# --- sessions -----------------------------------------------------------------------------


async def test_owner_lists_and_ends_member_sessions(
    api: Api, family: Family, db_session: AsyncSession
) -> None:
    owner_cookie = await api.login(family.owner.email)
    first = await api.login(family.member.email)
    second = await api.login(family.member.email)
    listed = await api.get(f"/users/{family.member.id}/sessions", cookie=owner_cookie)
    assert listed.status_code == 200 and len(listed.json()) == 2
    assert not any(s["current"] for s in listed.json())
    assert "token" not in listed.text and "hash" not in listed.text

    ended = await api.request("DELETE", f"/users/{family.member.id}/sessions", cookie=owner_cookie)
    assert ended.status_code == 200 and ended.json() == {"ended": 2}
    for cookie in (first, second):
        assert (await api.get("/auth/me", cookie=cookie)).status_code == 401
    # login still works (not disabled)
    assert await api.login(family.member.email)
    audit = await _audit(db_session, family.member.id)
    assert len(audit) == 1 and audit[0].actor_user_id == family.owner.id


async def test_own_sessions(api: Api, family: Family, db_session: AsyncSession) -> None:
    here = await api.login(family.member.email)
    there = await api.login(family.member.email)
    listed = (await api.get("/auth/sessions", cookie=here)).json()
    assert len(listed) == 2 and sum(s["current"] for s in listed) == 1
    other = next(s for s in listed if not s["current"])
    # another member's session id is a 404
    owner_cookie = await api.login(family.owner.email)
    owner_session = (await api.get("/auth/sessions", cookie=owner_cookie)).json()[0]
    assert (
        await api.request("DELETE", f"/auth/sessions/{owner_session['id']}", cookie=here)
    ).status_code == 404
    ended = await api.request("DELETE", f"/auth/sessions/{other['id']}", cookie=here)
    assert ended.status_code == 204
    assert (await api.get("/auth/me", cookie=there)).status_code == 401
    assert (await api.get("/auth/me", cookie=here)).status_code == 200
    assert len((await api.get("/auth/sessions", cookie=here)).json()) == 1
    assert (
        await api.request("DELETE", f"/auth/sessions/{other['id']}", cookie=here)
    ).status_code == 404
    audit = await _audit(db_session, other["id"])
    assert len(audit) == 1 and audit[0].actor_user_id == family.member.id


async def test_expired_sessions_are_not_listed(api: Api, family: Family) -> None:
    cookie = await api.login(family.member.email)
    api.clock.advance(timedelta(days=6))
    fresh = await api.login(family.member.email)
    api.clock.advance(timedelta(days=2))  # the first is idle-expired, the second is not
    listed = (await api.get("/auth/sessions", cookie=fresh)).json()
    assert len(listed) == 1 and listed[0]["current"] is True
    assert (await api.get("/auth/me", cookie=cookie)).status_code == 401


# --- e-mail change ------------------------------------------------------------------------


async def test_email_change_applies_only_after_confirmation(
    api: Api, family: Family, db_session: AsyncSession
) -> None:
    owner_cookie = await api.login(family.owner.email)
    member_cookie = await api.login(family.member.email)
    old, new = family.member.email, random_email("neu")
    before = len(api.outbox)
    response = await _patch(api, family.member, owner_cookie, {"email": new.upper()})
    assert response.status_code == 200 and response.json()["email"] == old
    await api.drain()
    mail = api.outbox[before]
    assert mail.to == new
    match = next(
        CONFIRM_RE.search(line) for line in mail.text.splitlines() if "confirm-email" in line
    )
    assert match
    token = match.group(1)

    # nothing changed yet: the old address works, the new one is unknown, the link is no login
    assert (await api.get("/auth/me", cookie=member_cookie)).status_code == 200
    assert (await api.post("/auth/verify", json={"token": token})).status_code == 400
    await api.post("/auth/magic-link", json={"email": new})
    await api.drain()
    assert len(api.outbox) == before + 1

    # a login link cannot confirm
    login_token = await api.request_link(old)
    assert (await api.post("/auth/confirm-email", json={"token": login_token})).status_code == 400

    confirmed = await api.post("/auth/confirm-email", json={"token": token})
    assert confirmed.status_code == 200 and confirmed.json() == {"status": "confirmed"}
    assert (await _fresh(db_session, family.member)).email == new
    assert (await api.get("/auth/me", cookie=member_cookie)).status_code == 401
    # single use; old address is gone, new one logs in
    assert (await api.post("/auth/confirm-email", json={"token": token})).status_code == 400
    api.clock.advance(timedelta(minutes=16))  # clear the per-address link limit
    count = len(api.outbox)
    await api.post("/auth/magic-link", json={"email": old})
    await api.drain()
    assert len(api.outbox) == count
    assert await api.login(new)

    audit = await _audit(db_session, family.member.id)
    assert len(audit) == 2 and audit[-1].actor_user_id == family.member.id
    assert old not in str([a.before for a in audit]) and new not in str([a.after for a in audit])


async def test_email_change_refused_for_taken_address_and_new_request_replaces_old(
    api: Api, family: Family
) -> None:
    cookie = await api.login(family.owner.email)
    taken = await _patch(api, family.member, cookie, {"email": family.owner.email})
    assert taken.status_code == 409 and taken.json() == {"detail": "email_taken"}

    before = len(api.outbox)
    await _patch(api, family.member, cookie, {"email": random_email("a")})
    await _patch(api, family.member, cookie, {"email": random_email("b")})
    await api.drain()
    tokens = [
        CONFIRM_RE.search(line).group(1)  # type: ignore[union-attr]
        for m in api.outbox[before:]
        for line in m.text.splitlines()
        if "confirm-email" in line
    ]
    assert len(tokens) == 2
    assert (await api.post("/auth/confirm-email", json={"token": tokens[0]})).status_code == 400
    assert (await api.post("/auth/confirm-email", json={"token": tokens[1]})).status_code == 200


async def test_email_change_link_expires(api: Api, family: Family) -> None:
    cookie = await api.login(family.owner.email)
    before = len(api.outbox)
    await _patch(api, family.member, cookie, {"email": random_email("x")})
    await api.drain()
    token = CONFIRM_RE.search(api.outbox[before].text.splitlines()[4]).group(1)  # type: ignore[union-attr]
    api.clock.advance(timedelta(minutes=api.settings.magic_link_ttl_minutes, seconds=1))
    assert (await api.post("/auth/confirm-email", json={"token": token})).status_code == 400


async def test_login_does_not_burn_a_pending_email_change(
    api: Api, family: Family, db_session: AsyncSession
) -> None:
    cookie = await api.login(family.owner.email)
    await _patch(api, family.member, cookie, {"email": random_email("p")})
    await api.login(family.member.email)  # login burns login links only
    pending = await db_session.scalars(
        select(MagicLinkToken).where(
            MagicLinkToken.user_id == family.member.id,
            MagicLinkToken.new_email.is_not(None),
            MagicLinkToken.used_at.is_(None),
        )
    )
    assert len(pending.all()) == 1


# --- privacy ------------------------------------------------------------------------------


async def test_member_management_leaks_nothing(
    api: Api,
    family: Family,
    spans: InMemorySpanExporter,
    json_log: io.StringIO,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    invited = f"sentinel-{secrets.token_hex(8)}@example.com"
    changed = f"sentinel-{secrets.token_hex(8)}@example.com"
    cookie = await api.login(family.owner.email)
    created = (await api.post("/users", json={"email": invited}, cookie=cookie)).json()
    await api.drain()
    invite_token = token_from_mail(api.outbox[-1])
    await _patch(api, family.member, cookie, {"email": changed})
    await _patch(api, family.owner, cookie, {"disabled": True})  # 409
    await api.post("/users", json={"email": invited}, cookie=cookie)  # 409
    await api.request("DELETE", f"/users/{created['id']}/sessions", cookie=cookie)
    await api.drain()
    confirm = next(
        CONFIRM_RE.search(line).group(1)  # type: ignore[union-attr]
        for m in api.outbox
        if m.to == changed
        for line in m.text.splitlines()
        if "confirm-email" in line
    )
    await api.post("/auth/confirm-email", json={"token": confirm})
    await api.post("/auth/confirm-email", json={"token": confirm})  # failed
    await _patch(api, family.member, cookie, {"disabled": True})

    exported = "\n".join(s.to_json() for s in spans.get_finished_spans())
    logged = json_log.getvalue() + caplog.text
    assert "users.invited" in logged and "auth.email_changed" in logged
    for label, needle in {
        "invited e-mail": invited,
        "changed e-mail": changed,
        "invite token": invite_token,
        "invite token hash": hash_token(invite_token),
        "confirm token": confirm,
        "confirm token hash": hash_token(confirm),
        "cookie": cookie,
    }.items():
        assert needle not in exported, f"{label} in spans"
        assert needle not in logged, f"{label} in logs"
