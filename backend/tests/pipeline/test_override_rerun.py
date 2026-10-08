"""#10 "Override survives a re-run": a user PATCH through the api, then #9's handler again with
a new prompt version (FakeProvider) leaves the item byte for byte unchanged; without the
PATCH the same run replaces it."""

from __future__ import annotations

import io
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.clock import FakeClock
from app.auth.service import create_session
from app.db.models import AppUser, Extraction
from app.queue.handlers import JobContext
from tests.auth.conftest import auth_settings, running_app
from tests.pipeline import files
from tests.pipeline.helpers import World, classify_out, extract_out, make_world, scripted
from tests.pipeline.test_handler import _ctx

Sessions = async_sessionmaker[AsyncSession]


@pytest.fixture
async def world(committed: Sessions, tmp_path: Path, migrated_database: str) -> World:
    return await make_world(committed, tmp_path, migrated_database)


async def _row(world: World, item_id: uuid.UUID) -> object:
    async with world.sessions() as session:
        result = await session.execute(
            text("SELECT * FROM tax_item WHERE id = :id"), {"id": item_id}
        )
        return result.one_or_none()


async def _rerun(world: World, ctx: JobContext, doc_id: uuid.UUID) -> None:
    async with world.sessions() as session:  # a new prompt version: nothing is reused
        await session.execute(
            update(Extraction).where(Extraction.document_id == doc_id).values(prompt_version="v0")
        )
        await session.commit()
    again = scripted(classify_out(), extract_out(lines=[("99.00", "spenden", "not_applicable")]))
    settings = world.settings(pipeline_prompt_version="v1")
    await world.handler(again.router)(JobContext(ctx.job, world.sessions, world.volume, settings))
    assert again.calls == 2


@pytest.mark.parametrize("patched", [True, False])
async def test_override_survives_rerun(
    world: World,
    patched: bool,
    clock: FakeClock,
    tmp_path: Path,
    migrated_database: str,
    json_log: io.StringIO,
) -> None:
    doc = await world.upload(files.jpeg())
    ctx = await _ctx(world, doc.id)
    await world.handler(scripted(classify_out(), extract_out()).router)(ctx)
    (item,) = await world.items(doc.id)

    if patched:
        async with world.sessions() as session:
            user = await session.get_one(AppUser, world.user_id)
            cookie, _ = await create_session(
                session, user, now=clock.now(), max_age=timedelta(days=30)
            )
            await session.commit()
        settings = auth_settings(migrated_database, storage_path=tmp_path / "api-storage")
        async with running_app(settings, clock=clock) as api:
            r = await api.request(
                "PATCH",
                f"/tax-items/{item.id}",
                json={"version": 1, "category": "wk_fortbildung"},
                cookie=cookie,
            )
        assert r.status_code == 200, r.text
    before = await _row(world, item.id)

    await _rerun(world, ctx, doc.id)

    items = await world.items(doc.id)
    assert len(items) == 1
    if patched:
        assert await _row(world, item.id) == before
        assert items[0].version == 2
        assert '"pipeline.overrides_kept"' in json_log.getvalue()
    else:
        assert items[0].id != item.id and await _row(world, item.id) is None
        assert items[0].category.value == "spenden"
