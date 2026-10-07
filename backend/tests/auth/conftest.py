"""Fixtures for the auth tests (#5).

- `api`: the app with `MAIL_BACKEND=memory`, a `FakeClock` and `APP_BASE_URL=https://app.test`.
  Every request gets its own `AsyncSession` on the test connection (savepoints), so endpoint
  commits are rolled back after the test like everything else.
- Requests go through a fresh httpx client each time (no cookie jar): pass `cookie=` to send
  the session cookie, `ip=` to pick the client address, `csrf=False` to omit the CSRF header.
- E-mail addresses are fictional (`example.com`) and generated at runtime.
"""

from __future__ import annotations

import io
import logging
import re
import secrets
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.api.deps import get_db
from app.api.main import create_app
from app.auth.clock import FakeClock
from app.auth.mail import MailBackend, MemoryBackend
from app.auth.tokens import cookie_name
from app.config import Settings
from app.db.models import AppUser, Household
from app.domain.enums import UserRole
from app.observability import API_SERVICE_NAME, get_observability
from app.observability.logs import build_formatter
from tests.domain.factories import make_household, make_user

BASE_URL = "https://app.test"
DEFAULT_IP = "198.51.100.10"
LINK_RE = re.compile(r"^https://app\.test/login/verify#token=([A-Za-z0-9_-]{43})$")
TOKEN_RE = re.compile(r"/login/verify#token=([A-Za-z0-9_-]{43})$")


def random_email(prefix: str = "user") -> str:
    return f"{prefix}-{secrets.token_hex(6)}@example.com"


def auth_settings(database_url: str, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "database_url": database_url,
        "app_env": "development",
        "app_base_url": BASE_URL,
        "mail_backend": "memory",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


@dataclass
class Api:
    app: FastAPI
    clock: FakeClock
    backend: Any
    settings: Settings
    ip: str = DEFAULT_IP
    base_url: str = BASE_URL
    extra_headers: dict[str, str] = field(default_factory=dict)

    @property
    def cookie_name(self) -> str:
        return cookie_name(self.settings.secure_cookies)

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        content: Any = None,
        cookie: str | None = None,
        ip: str | None = None,
        csrf: bool = True,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        sent: dict[str, str] = {"X-Requested-With": "belegbot"} if csrf else {}
        sent.update(self.extra_headers)
        sent.update(headers or {})
        if cookie is not None:
            sent["Cookie"] = f"{self.cookie_name}={cookie}"
        transport = httpx.ASGITransport(app=self.app, client=(ip or self.ip, 50000))
        async with httpx.AsyncClient(transport=transport, base_url=self.base_url) as client:
            return await client.request(method, path, json=json, content=content, headers=sent)

    async def get(self, path: str, **kw: Any) -> httpx.Response:
        return await self.request("GET", path, **kw)

    async def post(self, path: str, **kw: Any) -> httpx.Response:
        return await self.request("POST", path, **kw)

    @property
    def outbox(self) -> list[Any]:
        return list(self.backend.outbox)

    async def drain(self) -> None:
        await self.app.state.mailer.drain(timeout=10)

    async def request_link(self, email: str, next_path: str | None = None, **kw: Any) -> str:
        """Request a link and return the raw token from the newest mail."""
        before = len(self.outbox)
        body: dict[str, Any] = {"email": email}
        if next_path is not None:
            body["next"] = next_path
        response = await self.post("/auth/magic-link", json=body, **kw)
        assert response.status_code == 202, response.text
        await self.drain()
        assert len(self.outbox) == before + 1, "no mail sent"
        return token_from_mail(self.outbox[-1])

    async def login(self, email: str, **kw: Any) -> str:
        """Full login; returns the session cookie value."""
        token = await self.request_link(email)
        response = await self.post("/auth/verify", json={"token": token}, **kw)
        assert response.status_code == 200, response.text
        value = session_cookie_value(response, self.cookie_name)
        assert value
        return value


def token_from_mail(mail: Any) -> str:
    links = [line for line in mail.text.splitlines() if "/login/verify#token=" in line]
    assert len(links) == 1
    match = TOKEN_RE.search(links[0])
    assert match, "link format"
    return match.group(1)


def set_cookies(response: httpx.Response) -> list[str]:
    return response.headers.get_list("set-cookie")


def session_cookie_value(response: httpx.Response, name: str) -> str | None:
    for header in set_cookies(response):
        first = header.split(";", 1)[0]
        key, _, value = first.partition("=")
        if key.strip() == name:
            return value
    return None


def cookie_attributes(header: str) -> dict[str, str]:
    """`{"__name": "...", "__value": "...", "httponly": "", "max-age": "0", ...}`."""
    parts = [p.strip() for p in header.split(";")]
    key, _, value = parts[0].partition("=")
    attrs = {"__name": key, "__value": value}
    for part in parts[1:]:
        k, _, v = part.partition("=")
        attrs[k.lower()] = v
    return attrs


def connection_sessions(conn: AsyncConnection) -> Any:
    async def override() -> AsyncIterator[AsyncSession]:
        session = AsyncSession(
            bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
        )
        try:
            yield session
        finally:
            await session.close()

    return override


@asynccontextmanager
async def running_app(
    settings: Settings,
    *,
    clock: FakeClock | None = None,
    backend: MailBackend | None = None,
    conn: AsyncConnection | None = None,
    meter_provider: Any = None,
) -> AsyncIterator[Api]:
    the_clock = clock or FakeClock()
    the_backend = backend if backend is not None else MemoryBackend()
    app = create_app(
        settings, clock=the_clock, mail_backend=the_backend, meter_provider=meter_provider
    )
    if conn is not None:
        app.dependency_overrides[get_db] = connection_sessions(conn)
    async with app.router.lifespan_context(app):
        yield Api(app=app, clock=the_clock, backend=the_backend, settings=settings)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
async def api(
    migrated_database: str, db_connection: AsyncConnection, clock: FakeClock
) -> AsyncIterator[Api]:
    async with running_app(
        auth_settings(migrated_database), clock=clock, conn=db_connection
    ) as running:
        yield running


@dataclass
class Family:
    household: Household
    owner: AppUser
    member: AppUser


@pytest.fixture
async def family(db_session: AsyncSession) -> Family:
    household = await make_household(db_session, "Testhaushalt")
    owner = await make_user(db_session, household, email=random_email("owner"))
    member = await make_user(
        db_session, household, email=random_email("member"), role=UserRole.MEMBER
    )
    await db_session.commit()
    return Family(household, owner, member)


# --- telemetry capture (same setup as tests/test_observability.py) ---------------------


@pytest.fixture(scope="session")
def _auth_span_exporter() -> InMemorySpanExporter:
    observability = get_observability()
    assert observability is not None
    exporter = InMemorySpanExporter()
    observability.add_span_exporter(exporter)  # behind the scrubbing processor
    return exporter


@pytest.fixture
def spans(_auth_span_exporter: InMemorySpanExporter) -> Iterator[InMemorySpanExporter]:
    _auth_span_exporter.clear()
    yield _auth_span_exporter
    _auth_span_exporter.clear()


@pytest.fixture
def json_log() -> Iterator[io.StringIO]:
    """The exact JSON lines the stdout handler would print."""
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(build_formatter(API_SERVICE_NAME))
    root = logging.getLogger()
    root.addHandler(handler)
    previous = root.level
    root.setLevel(logging.DEBUG)
    try:
        yield buffer
    finally:
        root.removeHandler(handler)
        root.setLevel(previous)
