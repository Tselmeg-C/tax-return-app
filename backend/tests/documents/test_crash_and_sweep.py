"""Crash consistency of the upload and the sweep (#6 Decision 7, 11)."""

from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.db.models import Document, Job
from app.domain.enums import JobKind, JobStatus
from app.storage.sweep import sweep
from tests.documents import files
from tests.documents.conftest import Docs
from tests.domain.factories import make_document, make_household, make_user

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Boom(RuntimeError):
    pass


async def _rows(docs: Docs) -> tuple[int, int]:
    d = (await docs.session.execute(select(func.count()).select_from(Document))).scalar_one()
    j = (await docs.session.execute(select(func.count()).select_from(Job))).scalar_one()
    return int(d), int(j)


async def test_failure_after_rename_before_commit(
    docs: Docs, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, cookie = await docs.user()
    rows = await _rows(docs)
    moved = False
    original_commit = docs.storage.commit

    async def commit_then_flag(staged: Any, key: str) -> None:
        nonlocal moved
        await original_commit(staged, key)
        moved = True

    real_db_commit = AsyncSession.commit

    async def failing_db_commit(self: AsyncSession) -> None:
        if moved:
            raise Boom()
        await real_db_commit(self)

    monkeypatch.setattr(docs.storage, "commit", commit_then_flag)
    monkeypatch.setattr(AsyncSession, "commit", failing_db_commit)
    with pytest.raises(Boom):
        await docs.upload(files.png(), cookie)
    monkeypatch.undo()
    assert moved
    assert await _rows(docs) == rows
    assert [p for p in docs.root.rglob("original")] == []


async def test_failure_after_insert_before_rename(
    docs: Docs, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, cookie = await docs.user()
    rows = await _rows(docs)

    async def failing(staged: Any, key: str) -> None:
        raise Boom()

    monkeypatch.setattr(docs.storage, "commit", failing)
    with pytest.raises(Boom):
        await docs.upload(files.png(), cookie)
    assert await _rows(docs) == rows
    assert [p for p in docs.root.rglob("original")] == []
    assert list((docs.root / "tmp").iterdir()) == []


def _age(path: Path, seconds: float) -> None:
    when = time.time() - seconds
    for p in [path, *path.rglob("*")] if path.is_dir() else [path]:
        os.utime(p, (when, when))


async def test_sweep(docs: Docs, db_connection: AsyncConnection) -> None:
    volume = docs.storage
    volume.probe()
    hh = await make_household(docs.session)
    user = await make_user(docs.session, hh)
    kept_doc = await make_document(docs.session, hh, user)
    old_job, new_job = (
        Job(
            household_id=hh.id,
            kind=JobKind.PROCESS_DOCUMENT,
            document_id=kept_doc.id,
            max_attempts=5,
            status=JobStatus.SUCCEEDED,
            finished_at=func.now() - days,
        )
        for days in (timedelta(days=31), timedelta(days=29))
    )
    docs.session.add_all([old_job, new_job])
    await docs.session.commit()

    def doc_dir(document_id: uuid.UUID) -> Path:
        d = docs.root / "households" / str(hh.id) / "documents" / str(document_id)
        d.mkdir(parents=True)
        (d / "original").write_bytes(b"x")
        return d

    old_tmp, new_tmp = docs.root / "tmp" / ("a" * 32), docs.root / "tmp" / ("b" * 32)
    old_tmp.write_bytes(b"x")
    new_tmp.write_bytes(b"x")
    _age(old_tmp, 2 * 3600)
    _age(new_tmp, 5 * 60)
    orphan_old, orphan_new = uuid.uuid4(), uuid.uuid4()
    old_dir, new_dir = doc_dir(orphan_old), doc_dir(orphan_new)
    _age(old_dir, 2 * 3600)
    _age(new_dir, 5 * 60)
    kept_dir = doc_dir(kept_doc.id)
    _age(kept_dir, 2 * 3600)

    def sessions() -> AsyncSession:
        return AsyncSession(bind=db_connection, join_transaction_mode="create_savepoint")

    dry = await sweep(volume, sessions, dry_run=True)
    assert (dry.tmp_files, dry.orphan_documents, dry.old_jobs) == (1, [orphan_old], 1)
    assert old_tmp.exists() and old_dir.exists()

    result = await sweep(volume, sessions)
    assert (result.tmp_files, result.orphan_documents, result.old_jobs) == (1, [orphan_old], 1)
    assert not old_tmp.exists() and new_tmp.exists()
    assert not old_dir.exists() and new_dir.exists() and kept_dir.exists()
    docs.session.expunge_all()
    assert await docs.session.get(Job, old_job.id) is None
    assert await docs.session.get(Job, new_job.id) is not None


def test_sweep_cli_dry_run(test_database_url: str, tmp_path: Path, migrated_database: str) -> None:
    root = tmp_path / "storage"
    orphan = uuid.uuid4()
    d = root / "households" / str(uuid.uuid4()) / "documents" / str(orphan)
    d.mkdir(parents=True)
    (d / "original").write_bytes(b"x")
    _age(d, 2 * 3600)
    env = {k: v for k, v in os.environ.items() if not k.startswith("OTEL_")}
    env.update(DATABASE_URL=migrated_database, STORAGE_PATH=str(root))
    out = subprocess.run(
        [sys.executable, "-m", "app.storage.sweep", "--dry-run"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    ).stdout
    printed = [line for line in out.splitlines() if not line.startswith("{")]
    assert "would delete: 1 orphan document dir(s)" in printed
    assert f"  {orphan}" in printed
    assert d.exists()
