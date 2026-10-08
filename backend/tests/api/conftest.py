"""Fixtures for the tax-item api tests (#10): fictional households, documents and items on
the rolled-back test connection, an in-memory metric reader and request helpers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from opentelemetry.sdk.metrics import MeterProvider
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.auth.clock import FakeClock
from app.auth.service import create_session
from app.db.models import AppUser, AuditLog, Document, Household, TaxItem
from app.domain.enums import DocumentStatus, UserRole
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
from tests.documents.conftest import (  # noqa: F401 (fixtures)
    committed,
    meter_provider,
    metric_reader,
)
from tests.domain.factories import make_document, make_household, make_tax_item, make_user


@dataclass
class Home:
    household: Household
    user: AppUser
    cookie: str


@dataclass
class T:
    api: Api
    session: AsyncSession
    clock: FakeClock

    async def home(self, household: Household | None = None) -> Home:
        hh = household or await make_household(self.session, "Testhaushalt")
        user = await make_user(self.session, hh, email=random_email(), role=UserRole.OWNER)
        token, _ = await create_session(
            self.session, user, now=self.clock.now(), max_age=timedelta(days=30)
        )
        await self.session.commit()
        return Home(hh, user, token)

    async def doc(self, home: Home, **kw: Any) -> Document:
        kw.setdefault("status", DocumentStatus.DONE)
        doc = await make_document(self.session, home.household, home.user, **kw)
        await self.session.commit()
        return doc

    async def item(self, home: Home, doc: Document | None = None, **kw: Any) -> TaxItem:
        if doc is None and "document_id" not in kw:
            doc = await self.doc(home)
        if doc is not None:
            kw["document_id"] = doc.id
        item = await make_tax_item(self.session, home.household, **kw)
        await self.session.commit()
        return item

    async def get(self, home: Home | None, path: str) -> httpx.Response:
        return await self.api.get(path, cookie=home.cookie if home else None)

    async def patch(self, home: Home, item_id: Any, body: dict[str, Any]) -> httpx.Response:
        return await self.api.request(
            "PATCH", f"/tax-items/{item_id}", json=body, cookie=home.cookie
        )

    async def post_item(self, home: Home, doc_id: Any, body: dict[str, Any]) -> httpx.Response:
        return await self.api.post(f"/documents/{doc_id}/tax-items", json=body, cookie=home.cookie)

    async def audit(self, entity: str | None = None) -> list[AuditLog]:
        stmt = select(AuditLog).order_by(AuditLog.created_at, AuditLog.id)
        if entity:
            stmt = stmt.where(AuditLog.entity == entity)
        return list((await self.session.execute(stmt)).scalars().all())

    async def fresh(self, model: Any, ident: Any) -> Any:
        stmt = select(model).where(model.id == ident).execution_options(populate_existing=True)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def count(self, model: Any) -> int:
        return int((await self.session.execute(select(func.count()).select_from(model))).scalar())


@pytest.fixture
async def t(
    migrated_database: str,
    db_connection: AsyncConnection,
    db_session: AsyncSession,
    clock: FakeClock,  # noqa: F811
    tmp_path: Path,
    meter_provider: MeterProvider,  # noqa: F811
) -> AsyncIterator[T]:
    settings = auth_settings(migrated_database, storage_path=tmp_path / "storage")
    async with running_app(
        settings, clock=clock, conn=db_connection, meter_provider=meter_provider
    ) as api:
        yield T(api=api, session=db_session, clock=clock)
