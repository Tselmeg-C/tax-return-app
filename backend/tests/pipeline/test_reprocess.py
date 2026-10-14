"""#45 end to end with the FakeProvider: the api queues, the handler reuses or ignores the
stored step results (`fresh`), and a document with an override is never run."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.clock import FakeClock
from app.auth.service import create_session
from app.db.models import AppUser, Extraction, TaxItem
from app.domain.enums import DocumentStatus, JobStatus
from app.queue.handlers import JobContext
from app.queue.runner import succeed
from tests.auth.conftest import auth_settings, running_app
from tests.pipeline import files
from tests.pipeline.helpers import World, classify_out, extract_out, make_world, scripted
from tests.pipeline.test_handler import _ctx

Sessions = async_sessionmaker[AsyncSession]


@pytest.fixture
async def world(committed: Sessions, tmp_path: Path, migrated_database: str) -> World:
    return await make_world(committed, tmp_path, migrated_database)


async def _post(
    world: World, clock: FakeClock, tmp_path: Path, url: str, path: str, body: dict[str, object]
) -> int:
    async with world.sessions() as session:
        user = await session.get_one(AppUser, world.user_id)
        cookie, _ = await create_session(session, user, now=clock.now(), max_age=timedelta(days=30))
        await session.commit()
    settings = auth_settings(url, storage_path=tmp_path / "api-storage")
    async with running_app(settings, clock=clock) as api:
        return (await api.post(path, json=body, cookie=cookie)).status_code


@pytest.mark.parametrize("fresh", [False, True])
async def test_reprocess_reuses_or_ignores_cached_steps(
    world: World, clock: FakeClock, tmp_path: Path, migrated_database: str, fresh: bool
) -> None:
    doc = await world.upload(files.jpeg())
    ctx = await _ctx(world, doc.id)
    first = scripted(classify_out(), extract_out())
    await world.handler(first.router)(ctx)
    (old,) = await world.items(doc.id)
    # the runner would mark the job and document finished; do the same by hand
    assert await succeed(world.sessions, ctx.job)

    status = await _post(
        world,
        clock,
        tmp_path,
        migrated_database,
        f"/documents/{doc.id}/reprocess",
        {"fresh": fresh},
    )
    assert status == 202
    again = scripted(classify_out(), extract_out(lines=[("99.00", "spenden", "not_applicable")]))
    ctx2 = await _ctx(world, doc.id)
    assert ctx2.job.fresh_since is not None or not fresh
    await world.handler(again.router)(
        JobContext(ctx2.job, world.sessions, world.volume, ctx.settings)
    )

    (new,) = await world.items(doc.id)
    assert new.id != old.id  # replaced
    assert again.calls == (2 if fresh else 0)
    assert new.category.value == ("spenden" if fresh else "wk_arbeitsmittel")
    assert len(await world.rows(Extraction, doc.id)) == (4 if fresh else 2)  # append-only log


async def test_override_document_is_never_queued(
    world: World, clock: FakeClock, tmp_path: Path, migrated_database: str
) -> None:
    doc = await world.upload(files.jpeg())
    ctx = await _ctx(world, doc.id)
    s = scripted(classify_out(), extract_out())
    await world.handler(s.router)(ctx)
    assert await succeed(world.sessions, ctx.job)
    async with world.sessions() as session:
        await session.execute(
            update(TaxItem).where(TaxItem.document_id == doc.id).values(overridden_by_user=True)
        )
        await session.commit()
    (item,) = await world.items(doc.id)
    status = await _post(
        world,
        clock,
        tmp_path,
        migrated_database,
        f"/documents/{doc.id}/reprocess",
        {"fresh": True},
    )
    assert status == 409
    assert (await world.doc(doc.id)).status is DocumentStatus.DONE
    assert (await world.items(doc.id))[0].id == item.id
    assert (await world.job(doc.id)).status is JobStatus.SUCCEEDED
