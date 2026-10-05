"""Protected by default (#5): every route needs a session unless it is in `PUBLIC_PATHS`.

The meta-test walks `app.routes`, so a future route that is reachable without a session and
is not on the allowlist (`app/api/public.py`) fails CI. Also: CSRF checks and production docs.
"""

from __future__ import annotations

import re

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute, iter_route_contexts
from sqlalchemy.ext.asyncio import AsyncConnection

from app.api.main import create_app
from app.api.public import PUBLIC_PATHS
from tests.auth.conftest import Api, Family, auth_settings, running_app

CSRF = {"detail": "csrf"}


def _probes(app: FastAPI) -> list[tuple[str, str]]:
    """(method, concrete path) for every route in `app.routes`."""
    found: list[tuple[str, str]] = []
    # `app.routes` holds included routers as one entry each; this yields their routes too.
    for route in iter_route_contexts(app.routes):
        path = route.path
        assert isinstance(path, str), f"unexpected route type {type(route.original_route)}"
        methods = sorted(route.methods or {"GET"})
        concrete = re.sub(r"\{[^}]+\}", "00000000-0000-4000-8000-000000000000", path)
        found.extend((m, concrete) for m in methods if m != "HEAD")
    return found


ALL_ROUTES = _probes(create_app())
ROUTES = [(m, p) for m, p in ALL_ROUTES if p not in PUBLIC_PATHS]


async def unprotected_routes(api: Api) -> list[str]:
    """Routes outside the allowlist that answer anything but 401 without a cookie."""
    offenders: list[str] = []
    for method, path in _probes(api.app):
        if path in PUBLIC_PATHS:
            continue
        response = await api.request(method, path, json={})
        if response.status_code != 401:
            offenders.append(f"{method} {path} -> {response.status_code}")
    return offenders


def test_allowlist_is_exactly_the_groomed_one() -> None:
    assert PUBLIC_PATHS <= {p for _, p in ALL_ROUTES}
    assert PUBLIC_PATHS == {
        "/health",
        "/version",
        "/auth/magic-link",
        "/auth/verify",
        "/auth/logout",
    }


@pytest.mark.parametrize(("method", "path"), ROUTES, ids=[f"{m} {p}" for m, p in ROUTES])
async def test_every_non_public_route_requires_a_session(api: Api, method: str, path: str) -> None:
    response = await api.request(method, path, json={})
    assert response.status_code == 401, response.text
    assert response.json() == {"detail": "not_authenticated"}


async def test_meta_check_catches_an_unprotected_route(api: Api) -> None:
    assert await unprotected_routes(api) == []

    async def leaky() -> dict[str, str]:
        return {"secret": "data"}

    # Registered without the app-level dependency (bypasses `require_session`).
    api.app.router.routes.append(APIRoute("/test/leaky", leaky, methods=["GET"]))
    offenders = await unprotected_routes(api)
    assert offenders == ["GET /test/leaky -> 200"]
    with pytest.raises(AssertionError):
        assert offenders == [], offenders


async def test_docs_need_a_session_in_development(api: Api, family: Family) -> None:
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert (await api.get(path)).status_code == 401
    cookie = await api.login(family.owner.email)
    docs = await api.get("/docs", cookie=cookie)
    assert docs.status_code == 200 and "swagger" in docs.text.lower()
    spec = await api.get("/openapi.json", cookie=cookie)
    assert spec.status_code == 200 and "/auth/magic-link" in spec.json()["paths"]


async def test_production_has_no_docs(
    migrated_database: str, db_connection: AsyncConnection
) -> None:
    settings = auth_settings(
        migrated_database,
        app_env="production",
        mail_backend="resend",
        resend_api_key="re_test_placeholder",
        resend_from_email="belegbot <login@example.com>",
    )
    async with running_app(settings, conn=db_connection) as api:
        for path in ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"):
            assert (await api.get(path)).status_code == 404, path
        assert (await api.get("/health")).status_code == 200


# --- CSRF ---------------------------------------------------------------------------


@pytest.fixture
def protected_post(api: Api) -> str:
    @api.app.post("/test/protected")
    async def protected() -> dict[str, str]:
        return {"ok": "yes"}

    return "/test/protected"


@pytest.mark.parametrize(
    "path", ["/auth/magic-link", "/auth/verify", "/auth/logout", "/auth/logout-all"]
)
async def test_post_without_header_is_403(api: Api, path: str) -> None:
    response = await api.post(path, json={"email": "a@example.com", "token": "x"}, csrf=False)
    assert response.status_code == 403
    assert response.json() == CSRF
    assert response.headers["cache-control"] == "no-store"


async def test_protected_post_without_header_is_403(
    api: Api, family: Family, protected_post: str
) -> None:
    cookie = await api.login(family.owner.email)
    assert (await api.post(protected_post, cookie=cookie, csrf=False)).json() == CSRF
    assert (await api.post(protected_post, cookie=cookie)).json() == {"ok": "yes"}
    assert (await api.post(protected_post)).status_code == 401


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
async def test_other_unsafe_methods_need_the_header(api: Api, method: str) -> None:
    response = await api.request(method, "/auth/me", csrf=False)
    assert response.status_code == 403
    assert response.json() == CSRF


async def test_wrong_origin_is_403(api: Api, family: Family) -> None:
    body = {"email": family.owner.email}
    evil = await api.post("/auth/magic-link", json=body, headers={"Origin": "https://evil.example"})
    assert evil.status_code == 403 and evil.json() == CSRF
    same = await api.post("/auth/magic-link", json=body, headers={"Origin": "https://app.test"})
    assert same.status_code == 202
    none = await api.post("/auth/magic-link", json=body)
    assert none.status_code == 202


async def test_get_is_not_affected(api: Api) -> None:
    response = await api.get("/auth/me", csrf=False, headers={"Origin": "https://evil.example"})
    assert response.status_code == 401
    assert (await api.get("/health", csrf=False)).status_code == 200
