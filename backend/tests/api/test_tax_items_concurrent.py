"""#10 concurrent edits: two family members PATCH the same item with `version = 1` at once
(real connections, committed and truncated afterwards)."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.clock import FakeClock
from app.db.models import AppUser, AuditLog, Household, TaxItem
from app.domain.enums import AttentionReason, DocumentStatus
from tests.auth.conftest import auth_settings, running_app
from tests.documents.conftest import committed_user
from tests.domain.factories import make_document, make_tax_item


async def test_concurrent_patches_conflict_without_deadlock(
    committed: async_sessionmaker[AsyncSession],
    migrated_database: str,
    clock: FakeClock,
    tmp_path: Path,
) -> None:
    owner = await committed_user(committed, clock, None)
    member = await committed_user(committed, clock, owner.household_id)
    async with committed() as session:
        hh = await session.get(Household, owner.household_id)
        assert hh is not None
        user = await session.get_one(AppUser, owner.user_id)
        doc = await make_document(
            session,
            hh,
            user,
            status=DocumentStatus.NEEDS_ATTENTION,
            attention_reason=AttentionReason.LOW_CONFIDENCE,
        )
        item = await make_tax_item(session, hh, document_id=doc.id)
        await session.commit()

    settings = auth_settings(migrated_database, storage_path=tmp_path / "storage")
    async with running_app(settings, clock=clock) as api:
        transport = httpx.ASGITransport(app=api.app)

        async def patch(cookie: str, amount: str) -> httpx.Response:
            headers = {
                "X-Requested-With": "belegbot",
                "Cookie": f"{api.cookie_name}={cookie}",
            }
            async with httpx.AsyncClient(transport=transport, base_url=api.base_url) as client:
                return await client.patch(
                    f"/tax-items/{item.id}",
                    json={"version": 1, "deductible_amount": amount},
                    headers=headers,
                )

        first, second = await asyncio.wait_for(
            asyncio.gather(patch(owner.cookie, "80.00"), patch(member.cookie, "90.00")), 30
        )
    statuses = sorted([first.status_code, second.status_code])
    assert statuses == [200, 409], (first.text, second.text)
    winner, loser = (first, second) if first.status_code == 200 else (second, first)
    assert loser.json()["detail"] == "version_conflict"
    assert loser.json()["tax_item"]["version"] == 2
    assert loser.json()["tax_item"]["deductible_amount"] == winner.json()["deductible_amount"]
    async with committed() as session:
        stored = await session.get_one(TaxItem, item.id)
        assert stored.version == 2
        assert stored.deductible_amount == Decimal(winner.json()["deductible_amount"])
        n_item_rows = await session.scalar(
            select(func.count()).select_from(AuditLog).where(AuditLog.entity == "tax_item")
        )
        assert n_item_rows == 1
