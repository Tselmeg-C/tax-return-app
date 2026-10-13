"""Passkeys (#12): register, login, manage, against a software authenticator."""

from __future__ import annotations

import io
import json
import logging
from datetime import timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.passkeys import CHALLENGE_TTL, challenge_cookie_name
from app.db.models import AuditLog, Passkey, UserSession
from tests.auth.authenticator import Authenticator, b64
from tests.auth.conftest import (
    Api,
    Family,
    cookie_attributes,
    session_cookie_value,
    set_cookies,
)

ORIGIN = "https://app.test"
RP_ID = "app.test"
INVALID = {"detail": "invalid_passkey"}


def _challenge_cookie(api: Api, response: httpx.Response) -> str:
    value = session_cookie_value(response, challenge_cookie_name(True))
    assert value
    return value


def _headers(api: Api, challenge: str | None, session: str | None = None) -> dict[str, str]:
    parts = []
    if challenge:
        parts.append(f"{challenge_cookie_name(True)}={challenge}")
    if session:
        parts.append(f"{api.cookie_name}={session}")
    return {"Cookie": "; ".join(parts)} if parts else {}


async def register(
    api: Api, session: str, auth: Authenticator, name: str | None = None, **create: Any
) -> httpx.Response:
    opts = await api.post("/auth/passkey/register/options", cookie=session)
    assert opts.status_code == 200, opts.text
    credential = auth.create(opts.json(), origin=ORIGIN, rp_id=RP_ID, **create)
    body: dict[str, Any] = {"credential": credential}
    if name is not None:
        body["name"] = name
    return await api.post(
        "/auth/passkey/register/verify",
        json=body,
        headers=_headers(api, _challenge_cookie(api, opts), session),
    )


async def passkey_login(
    api: Api, auth: Authenticator, *, session: str | None = None, **get: Any
) -> httpx.Response:
    opts = await api.post("/auth/passkey/login/options")
    assert opts.status_code == 200
    get.setdefault("origin", ORIGIN)
    get.setdefault("rp_id", RP_ID)
    credential = auth.get(opts.json(), **get)
    return await api.post(
        "/auth/passkey/login/verify",
        json={"credential": credential, "next": "/belege"},
        headers=_headers(api, _challenge_cookie(api, opts), session),
    )


@pytest.fixture
async def owner_session(api: Api, family: Family) -> str:
    return await api.login(family.owner.email)


async def test_register_login_happy_path(api: Api, family: Family, owner_session: str) -> None:
    auth = Authenticator()
    created = await register(api, owner_session, auth)
    assert created.status_code == 201
    assert created.json()["name"] == "Passkey 1" and created.json()["last_used_at"] is None
    cleared = [c for c in set_cookies(created) if challenge_cookie_name(True) in c]
    assert cleared and cookie_attributes(cleared[0])["max-age"] == "0"

    listed = await api.get("/auth/passkeys", cookie=owner_session)
    assert [p["id"] for p in listed.json()] == [created.json()["id"]]
    assert "credential_id" not in listed.text

    # Signed out: discoverable login, no e-mail. Old session cookie is revoked (no fixation).
    response = await passkey_login(api, auth, session=owner_session)
    assert response.status_code == 200 and response.json() == {"next": "/belege"}
    new_cookie = session_cookie_value(response, api.cookie_name)
    assert new_cookie and new_cookie != owner_session
    assert (await api.get("/auth/me", cookie=owner_session)).status_code == 401
    assert (await api.get("/auth/me", cookie=new_cookie)).json()["user_id"] == str(family.owner.id)
    header = next(c for c in set_cookies(response) if c.startswith(api.cookie_name))
    attrs = cookie_attributes(header)
    assert {"httponly", "secure"} <= attrs.keys() and attrs["samesite"] == "Lax"
    after = (await api.get("/auth/passkeys", cookie=new_cookie)).json()
    assert after[0]["last_used_at"] is not None


async def test_challenge_cookie_attributes(api: Api) -> None:
    opts = await api.post("/auth/passkey/login/options")
    attrs = cookie_attributes(set_cookies(opts)[0])
    assert attrs["__name"] == "__Host-belegbot_passkey"
    assert {"httponly", "secure"} <= attrs.keys()
    assert attrs["samesite"] == "Strict" and attrs["max-age"] == "300" and attrs["path"] == "/"
    body = opts.json()
    assert body["userVerification"] == "required" and not body.get("allowCredentials")
    assert body["rpId"] == RP_ID


async def test_registration_options(api: Api, owner_session: str) -> None:
    body = (await api.post("/auth/passkey/register/options", cookie=owner_session)).json()
    assert body["rp"]["id"] == RP_ID
    selection = body["authenticatorSelection"]
    assert selection["residentKey"] == "required" and selection["userVerification"] == "required"
    assert body["attestation"] == "none" and body["excludeCredentials"] == []
    auth = Authenticator()
    await register(api, owner_session, auth)
    again = (await api.post("/auth/passkey/register/options", cookie=owner_session)).json()
    assert [c["id"] for c in again["excludeCredentials"]] == [b64(auth.credential_id)]


@pytest.mark.parametrize(
    ("kw"),
    [
        {"origin": "https://evil.test"},
        {"rp_id": "evil.test"},
        {"tamper": True},
        {"flags": 0x01},  # user verification missing
    ],
    ids=["origin", "rp_id", "signature", "no_uv"],
)
async def test_login_failures_are_identical(
    api: Api, owner_session: str, kw: dict[str, Any]
) -> None:
    auth = Authenticator()
    await register(api, owner_session, auth)
    response = await passkey_login(api, auth, **kw)
    assert response.status_code == 400 and response.json() == INVALID


async def test_registration_failures(api: Api, owner_session: str) -> None:
    for kw in ({"flags": 0x01},):
        response = await register(api, owner_session, Authenticator(), **kw)
        assert response.status_code == 400 and response.json() == INVALID
    opts = await api.post("/auth/passkey/register/options", cookie=owner_session)
    wrong = Authenticator().create(opts.json(), origin="https://evil.test", rp_id=RP_ID)
    response = await api.post(
        "/auth/passkey/register/verify",
        json={"credential": wrong},
        headers=_headers(api, _challenge_cookie(api, opts), owner_session),
    )
    assert response.status_code == 400
    # No challenge cookie at all.
    ok = Authenticator().create(opts.json(), origin=ORIGIN, rp_id=RP_ID)
    none = await api.post(
        "/auth/passkey/register/verify", json={"credential": ok}, cookie=owner_session
    )
    assert none.status_code == 400
    # A login challenge cannot be used to register (purpose).
    login = await api.post("/auth/passkey/login/options")
    cred = Authenticator().create(login.json(), origin=ORIGIN, rp_id=RP_ID)
    crossed = await api.post(
        "/auth/passkey/register/verify",
        json={"credential": cred},
        headers=_headers(api, _challenge_cookie(api, login), owner_session),
    )
    assert crossed.status_code == 400


async def test_register_challenge_bound_to_user(
    api: Api, family: Family, owner_session: str
) -> None:
    member = await api.login(family.member.email)
    opts = await api.post("/auth/passkey/register/options", cookie=owner_session)
    cred = Authenticator().create(opts.json(), origin=ORIGIN, rp_id=RP_ID)
    response = await api.post(
        "/auth/passkey/register/verify",
        json={"credential": cred},
        headers=_headers(api, _challenge_cookie(api, opts), member),
    )
    assert response.status_code == 400


async def test_replayed_and_expired_challenge(api: Api, owner_session: str) -> None:
    auth = Authenticator()
    await register(api, owner_session, auth)
    opts = await api.post("/auth/passkey/login/options")
    cookie = _challenge_cookie(api, opts)
    cred = auth.get(opts.json(), origin=ORIGIN, rp_id=RP_ID)
    # Tampered cookie.
    bad = await api.post(
        "/auth/passkey/login/verify",
        json={"credential": cred},
        headers=_headers(api, cookie[:-1] + ("0" if cookie[-1] != "0" else "1")),
    )
    assert bad.status_code == 400
    # Expired.
    api.clock.advance(CHALLENGE_TTL + timedelta(seconds=1))
    late = await api.post(
        "/auth/passkey/login/verify", json={"credential": cred}, headers=_headers(api, cookie)
    )
    assert late.status_code == 400 and late.json() == INVALID


async def test_sign_count_must_increase(api: Api, owner_session: str) -> None:
    auth = Authenticator()
    await register(api, owner_session, auth)
    assert (await passkey_login(api, auth, sign_count=5)).status_code == 200
    assert (await passkey_login(api, auth, sign_count=5)).status_code == 400
    assert (await passkey_login(api, auth, sign_count=3)).status_code == 400
    assert (await passkey_login(api, auth, sign_count=6)).status_code == 200


async def test_zero_sign_count_authenticators_work(api: Api, owner_session: str) -> None:
    auth = Authenticator()
    await register(api, owner_session, auth)
    assert (await passkey_login(api, auth, sign_count=0)).status_code == 200
    assert (await passkey_login(api, auth, sign_count=0)).status_code == 200


async def test_unknown_disabled_and_deleted_give_the_same_400(
    api: Api, family: Family, owner_session: str, db_session: AsyncSession
) -> None:
    unknown = await passkey_login(api, Authenticator())
    auth = Authenticator()
    created = await register(api, owner_session, auth)
    deleted_auth = Authenticator()
    gone = (await register(api, owner_session, deleted_auth)).json()["id"]
    assert (
        await api.request("DELETE", f"/auth/passkeys/{gone}", cookie=owner_session)
    ).status_code == 204
    deleted = await passkey_login(api, deleted_auth)

    family.owner.disabled_at = api.clock.now()
    await db_session.commit()
    disabled = await passkey_login(api, auth)
    assert created.status_code == 201
    for response in (unknown, deleted, disabled):
        assert response.status_code == 400 and response.json() == INVALID
        assert api.cookie_name not in "".join(set_cookies(response))
    assert unknown.text == deleted.text == disabled.text


async def test_manage_rename_delete_audit_and_isolation(
    api: Api, family: Family, owner_session: str, db_session: AsyncSession
) -> None:
    member = await api.login(family.member.email)
    auth = Authenticator()
    created = (await register(api, owner_session, auth, name="  Mein Handy ")).json()
    assert created["name"] == "Mein Handy"
    pid = created["id"]

    # Another user of the same household gets 404 everywhere.
    assert (await api.get("/auth/passkeys", cookie=member)).json() == []
    patch = await api.request("PATCH", f"/auth/passkeys/{pid}", json={"name": "x"}, cookie=member)
    assert patch.status_code == 404
    assert (await api.request("DELETE", f"/auth/passkeys/{pid}", cookie=member)).status_code == 404

    for bad in ("", "  ", "x" * 61, 5, None):
        r = await api.request(
            "PATCH", f"/auth/passkeys/{pid}", json={"name": bad}, cookie=owner_session
        )
        assert r.status_code == 422
    renamed = await api.request(
        "PATCH", f"/auth/passkeys/{pid}", json={"name": "Laptop"}, cookie=owner_session
    )
    assert renamed.json()["name"] == "Laptop"
    assert (
        await api.request("DELETE", f"/auth/passkeys/{pid}", cookie=owner_session)
    ).status_code == 204
    assert (await api.get("/auth/passkeys", cookie=owner_session)).json() == []
    # Existing sessions stay valid after a delete.
    assert (await api.get("/auth/me", cookie=owner_session)).status_code == 200

    rows = (
        (
            await db_session.execute(
                select(AuditLog).where(AuditLog.entity == "passkey").order_by(AuditLog.created_at)
            )
        )
        .scalars()
        .all()
    )
    assert sorted(r.action.value for r in rows) == ["create", "delete", "update"]
    dump = json.dumps([[r.before, r.after] for r in rows])
    for secret in (b64(auth.credential_id), "Laptop", "Mein Handy", "transports", "internal"):
        assert secret not in dump
    assert all(r.actor_user_id == family.owner.id for r in rows)


async def test_limit_of_ten(api: Api, owner_session: str) -> None:
    for _ in range(10):
        assert (await register(api, owner_session, Authenticator())).status_code == 201
    over = await api.post("/auth/passkey/register/options", cookie=owner_session)
    assert over.status_code == 409 and over.json() == {"detail": "passkey_limit"}


async def test_duplicate_credential_is_rejected(api: Api, owner_session: str) -> None:
    auth = Authenticator()
    assert (await register(api, owner_session, auth)).status_code == 201
    again = await register(api, owner_session, auth)
    assert again.status_code == 400


async def test_register_routes_need_a_session_and_csrf(api: Api) -> None:
    assert (await api.post("/auth/passkey/register/options")).status_code == 401
    assert (await api.post("/auth/passkey/register/verify", json={})).status_code == 401
    assert (await api.get("/auth/passkeys")).status_code == 401
    assert (await api.post("/auth/passkey/login/options", csrf=False)).status_code == 403


async def test_login_rate_limits(api: Api) -> None:
    for _ in range(20):
        assert (
            await api.post("/auth/passkey/login/options", ip="198.51.100.77")
        ).status_code == 200
    limited = await api.post("/auth/passkey/login/options", ip="198.51.100.77")
    assert limited.status_code == 429 and "retry-after" in limited.headers
    for _ in range(20):
        r = await api.post("/auth/passkey/login/verify", json={"credential": 1}, ip="198.51.100.78")
        assert r.status_code == 400
    blocked = await api.post("/auth/passkey/login/verify", json={}, ip="198.51.100.78")
    assert blocked.status_code == 429


async def test_garbage_bodies_are_400(api: Api) -> None:
    for body in (
        {},
        {"credential": "x"},
        {"credential": {"rawId": 3}},
        {"credential": {"rawId": "!!"}},
    ):
        r = await api.post("/auth/passkey/login/verify", json=body)
        assert r.status_code == 400 and r.json() == INVALID


async def test_no_credential_data_in_logs_or_responses(
    api: Api, owner_session: str, json_log: io.StringIO, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    auth = Authenticator()
    created = await register(api, owner_session, auth, name="SentinelName-4711")
    opts = await api.post("/auth/passkey/login/options")
    cookie = _challenge_cookie(api, opts)
    await passkey_login(api, auth, tamper=True)
    ok = await passkey_login(api, auth)
    assert ok.status_code == 200
    logged = json_log.getvalue() + caplog.text
    assert "auth.passkey.registered" in logged and "auth.passkey.login_failed" in logged
    needles = [
        b64(auth.credential_id),
        opts.json()["challenge"],
        cookie,
        "SentinelName-4711",
        auth.credential_id.hex(),
    ]
    for needle in needles:
        assert needle not in logged
    assert "SentinelName-4711" not in ok.text and created.json()["name"] == "SentinelName-4711"


async def test_passkey_login_creates_a_session_row(
    api: Api, owner_session: str, db_session: AsyncSession
) -> None:
    auth = Authenticator()
    await register(api, owner_session, auth)
    before = len((await db_session.execute(select(UserSession))).scalars().all())
    assert (await passkey_login(api, auth)).status_code == 200
    after = len((await db_session.execute(select(UserSession))).scalars().all())
    assert after == before + 1
    row = (await db_session.execute(select(Passkey))).scalar_one()
    assert row.transports == ["internal"] and row.sign_count == 1
