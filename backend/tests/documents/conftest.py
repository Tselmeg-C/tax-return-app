"""Fixtures for the documents / storage / queue tests (#6).

- `docs`: the app with `STORAGE_PATH=<tmp_path>/storage` on the rolled-back test connection
  (like `tests/auth`'s `api`), plus helpers to sign in, upload and count rows and files.
- `committed`: a session factory on the real test engine for tests that need several
  connections (concurrent uploads, the worker). Everything it commits is truncated afterwards.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession, async_sessionmaker

from app.auth.clock import FakeClock
from app.auth.service import create_session
from app.db.models import AppUser, AuditLog, Document, Household, Job
from app.domain.enums import UserRole
from app.storage import LocalVolume
from tests.auth.conftest import (  # noqa: F401 (fixtures)
    Api,
    _auth_span_exporter,
    auth_settings,
    clock,
    json_log,
    random_email,
    running_app,
    spans,
)
from tests.domain.factories import make_household, make_user

ALL_TABLES = (
    "job, audit_log, tax_item, extraction, document, user_session, magic_link_token, "
    "app_user, person, household"
)


@dataclass
class Docs:
    api: Api
    root: Path
    session: AsyncSession
    clock: FakeClock

    @property
    def storage(self) -> LocalVolume:
        storage: LocalVolume = self.api.app.state.storage
        return storage

    async def user(
        self, household: Household | None = None, role: UserRole = UserRole.OWNER
    ) -> tuple[AppUser, str]:
        """A new user (in a new household unless given) and a session cookie for them."""
        hh = household or await make_household(self.session, "Testhaushalt")
        user = await make_user(self.session, hh, email=random_email(), role=role)
        token, _ = await create_session(
            self.session, user, now=self.clock.now(), max_age=timedelta(days=30)
        )
        await self.session.commit()
        return user, token

    async def upload(
        self,
        content: Any,
        cookie: str | None,
        *,
        filename: str | None = None,
        headers: dict[str, str] | None = None,
        csrf: bool = True,
    ) -> httpx.Response:
        sent = {"Content-Type": "application/octet-stream"}
        if filename is not None:
            sent["X-Filename"] = filename
        sent.update(headers or {})
        return await self.api.request(
            "POST", "/documents", content=content, cookie=cookie, headers=sent, csrf=csrf
        )

    async def counts(self) -> tuple[int, int, int]:
        """(documents, jobs, audit rows) visible on the test connection."""
        values = []
        for model in (Document, Job, AuditLog):
            result = await self.session.execute(select(func.count()).select_from(model))
            values.append(int(result.scalar_one()))
        return values[0], values[1], values[2]

    def files(self) -> set[str]:
        return {str(p.relative_to(self.root)) for p in self.root.rglob("*") if p.is_file()}


@pytest.fixture
async def docs(
    migrated_database: str,
    db_connection: AsyncConnection,
    db_session: AsyncSession,
    clock: FakeClock,  # noqa: F811
    tmp_path: Path,
) -> AsyncIterator[Docs]:
    root = tmp_path / "storage"
    settings = auth_settings(migrated_database, storage_path=root)
    async with running_app(settings, clock=clock, conn=db_connection) as api:
        yield Docs(api=api, root=root, session=db_session, clock=clock)


async def _truncate(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {ALL_TABLES} CASCADE"))


@pytest.fixture
async def committed(db_engine: AsyncEngine) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    await _truncate(db_engine)
    try:
        yield async_sessionmaker(db_engine, expire_on_commit=False)
    finally:
        await _truncate(db_engine)


@dataclass
class CommittedUser:
    household_id: uuid.UUID
    user_id: uuid.UUID
    cookie: str


async def committed_user(
    sessions: async_sessionmaker[AsyncSession], at: FakeClock, household_id: uuid.UUID | None
) -> CommittedUser:
    async with sessions() as session:
        if household_id is None:
            hh = await make_household(session, "Testhaushalt")
            household_id = hh.id
        else:
            hh = await session.get(Household, household_id)
            assert hh is not None
        user = await make_user(session, hh, email=random_email())
        token, _ = await create_session(session, user, now=at.now(), max_age=timedelta(days=30))
        await session.commit()
        return CommittedUser(household_id, user.id, token)


@pytest.fixture
def metric_reader() -> InMemoryMetricReader:
    return InMemoryMetricReader()


@pytest.fixture
def meter_provider(metric_reader: InMemoryMetricReader) -> Iterator[MeterProvider]:
    provider = MeterProvider(metric_readers=[metric_reader])
    yield provider
    provider.shutdown()


def metric_points(reader: InMemoryMetricReader) -> dict[str, list[Any]]:
    """`{metric name: [data points]}` from the in-memory reader."""
    found: dict[str, list[Any]] = {}
    data = reader.get_metrics_data()
    if data is None:
        return found
    for resource in data.resource_metrics:
        for scope in resource.scope_metrics:
            for metric in scope.metrics:
                found.setdefault(metric.name, []).extend(metric.data.data_points)
    return found
