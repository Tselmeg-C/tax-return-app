"""Storage settings at api startup and the queue ops CLI (#6 Settings, CLI criteria)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker

from app.auth.clock import FakeClock
from app.config import Settings, SettingsError, check_api_settings
from app.db.models import Document, Job
from app.domain.enums import DocumentStatus, JobStatus
from app.storage import LocalVolume, StorageUnavailable
from tests.auth.conftest import auth_settings, running_app
from tests.documents.conftest import committed_user
from tests.documents.test_worker import doc_of, job_of, upload

BACKEND_DIR = Path(__file__).resolve().parents[2]

PROD = {
    "app_env": "production",
    "mail_backend": "resend",
    "resend_api_key": "re_test_placeholder",
    "resend_from_email": "belegbot <login@example.com>",
    "app_base_url": "https://app.test",
}


@pytest.mark.parametrize("path", [None, "relative/storage"])
def test_production_needs_absolute_storage_path(
    migrated_database: str, monkeypatch: pytest.MonkeyPatch, path: str | None
) -> None:
    monkeypatch.delenv("STORAGE_PATH", raising=False)
    settings = auth_settings(migrated_database, storage_path=path, **PROD)
    with pytest.raises(SettingsError) as caught:
        check_api_settings(settings)
    assert "STORAGE_PATH" in str(caught.value)


def test_default_storage_path_is_absolute(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STORAGE_PATH", raising=False)
    settings = Settings(_env_file=None, database_url="postgresql://x@h/db")  # type: ignore[call-arg]
    assert settings.resolved_storage_path.is_absolute()
    assert settings.resolved_storage_path.parts[-2:] == ("data", "storage")


async def test_api_startup_fails_on_read_only_storage(
    migrated_database: str, db_connection: AsyncConnection, tmp_path: Path
) -> None:
    root = tmp_path / "ro"
    root.mkdir()
    root.chmod(0o500)
    try:
        with pytest.raises(StorageUnavailable) as caught:
            async with running_app(
                auth_settings(migrated_database, storage_path=root), conn=db_connection
            ):
                pass  # pragma: no cover
    finally:
        root.chmod(0o700)
    assert "STORAGE_PATH" in str(caught.value)


def _cli(database_url: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("OTEL_")}
    env["DATABASE_URL"] = database_url
    return subprocess.run(
        [sys.executable, "-m", "app.queue.cli", *args],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


async def test_queue_cli(
    committed: async_sessionmaker[AsyncSession],
    clock: FakeClock,
    migrated_database: str,
    tmp_path: Path,
) -> None:
    volume = LocalVolume(tmp_path / "storage")
    volume.probe()
    owner = await committed_user(committed, clock, None)
    failed_doc = await upload(committed, volume, owner)
    done_doc = await upload(committed, volume, owner)
    async with committed() as session:
        await session.execute(
            update(Job)
            .where(Job.document_id == failed_doc.id)
            .values(status=JobStatus.FAILED, attempts=5, last_error_kind="ValueError")
        )
        await session.execute(
            update(Document)
            .where(Document.id == failed_doc.id)
            .values(status=DocumentStatus.FAILED, error_kind="ValueError")
        )
        await session.execute(
            update(Job).where(Job.document_id == done_doc.id).values(status=JobStatus.SUCCEEDED)
        )
        await session.commit()

    stats = _cli(migrated_database, "stats")
    assert stats.returncode == 0, stats.stderr
    assert "process_document failed 1" in stats.stdout
    assert "process_document succeeded 1" in stats.stdout

    failed_job = await job_of(committed, failed_doc.id)
    retried = _cli(migrated_database, "retry", str(failed_job.id))
    assert retried.returncode == 0, retried.stderr
    job = await job_of(committed, failed_doc.id)
    assert (job.status, job.attempts) == (JobStatus.QUEUED, 0)
    assert (await doc_of(committed, failed_doc.id)).status is DocumentStatus.QUEUED

    done_job = await job_of(committed, done_doc.id)
    assert _cli(migrated_database, "retry", str(done_job.id)).returncode != 0
