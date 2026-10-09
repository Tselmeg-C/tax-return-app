"""`python -m app.worker` as a process: startup, JSON logs, graceful shutdown (#6 Decision 10)."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.clock import FakeClock
from app.domain.enums import DocumentStatus, JobStatus
from app.storage import LocalVolume
from tests.documents.conftest import committed_user
from tests.documents.test_worker import doc_of, job_of, make_worker, upload

BACKEND_DIR = Path(__file__).resolve().parents[2]


def _env(database_url: str, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("OTEL_")}
    env.update(DATABASE_URL=database_url, WORKER_POLL_INTERVAL_SECONDS="0.1", **extra)
    return env


def _start(database_url: str, root: Path, **extra: str) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-m", "app.worker"],
        cwd=BACKEND_DIR,
        env=_env(database_url, STORAGE_PATH=str(root), **extra),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _wait_for_line(proc: subprocess.Popen[str], needle: str, lines: list[str]) -> None:
    assert proc.stdout is not None
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if not line:
            break
        lines.append(line)
        if needle in line:
            return
    raise AssertionError(f"never saw {needle!r}")


def _stop(proc: subprocess.Popen[str], lines: list[str]) -> float:
    """SIGTERM, wait for exit; returns how long the exit took."""
    started = time.monotonic()
    proc.send_signal(signal.SIGTERM)
    rest, _ = proc.communicate(timeout=10)
    took = time.monotonic() - started
    lines.extend(rest.splitlines())
    return took


def test_idle_worker_logs_json_and_stops(migrated_database: str, tmp_path: Path) -> None:
    proc = _start(migrated_database, tmp_path / "storage")
    lines: list[str] = []
    _wait_for_line(proc, "worker started", lines)
    took = _stop(proc, lines)
    assert proc.returncode == 0
    assert took < 2
    records = [json.loads(line) for line in lines if line.strip()]
    for record in records:
        assert {"timestamp", "level", "event", "service"} <= set(record)
        assert record["service"] == "belegbot-worker"
    events = [r["event"] for r in records]
    assert events.count("worker started") == 1 and events.count("worker stopped") == 1


def test_read_only_storage_fails_startup(migrated_database: str, tmp_path: Path) -> None:
    root = tmp_path / "ro"
    root.mkdir()
    root.chmod(0o500)
    try:
        proc = _start(migrated_database, root)
        out, _ = proc.communicate(timeout=30)
    finally:
        root.chmod(0o700)
    assert proc.returncode == 1
    assert "STORAGE_PATH" in out


@pytest.mark.parametrize("value", ["", "relative/storage"])
def test_production_storage_path_rules(migrated_database: str, value: str) -> None:
    out = subprocess.run(
        [sys.executable, "-m", "app.worker"],
        cwd=BACKEND_DIR,
        env=_env(migrated_database, APP_ENV="production", STORAGE_PATH=value),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert out.returncode == 1
    assert "STORAGE_PATH" in out.stdout


async def test_sigterm_during_a_job_releases_it(
    committed: async_sessionmaker[AsyncSession],
    clock: FakeClock,
    migrated_database: str,
    tmp_path: Path,
) -> None:
    root = tmp_path / "storage"
    volume = LocalVolume(root)
    volume.probe()
    owner = await committed_user(committed, clock, None)
    doc = await upload(committed, volume, owner)
    proc = _start(
        migrated_database,
        root,
        WORKER_TEST_SLEEP_SECONDS="10",
        WORKER_SHUTDOWN_GRACE_SECONDS="1",
    )
    lines: list[str] = []
    try:
        async with asyncio.timeout(30):
            while (await job_of(committed, doc.id)).status is not JobStatus.RUNNING:
                await asyncio.sleep(0.1)
        took = await asyncio.to_thread(_stop, proc, lines)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert proc.returncode == 0
    assert took < 3
    job = await job_of(committed, doc.id)
    assert (job.status, job.attempts, job.locked_until) == (JobStatus.QUEUED, 0, None)
    assert any('"job.released"' in line for line in lines)

    worker = make_worker(committed, volume, migrated_database)
    assert await worker.run_once()
    assert (await doc_of(committed, doc.id)).status is DocumentStatus.DONE


def test_stop_handler_only_sets_a_flag(
    committed: async_sessionmaker[AsyncSession], migrated_database: str, tmp_path: Path
) -> None:
    """#52: `Worker.stop` is the SIGTERM/SIGINT handler; it must not log or do I/O (a log
    line from a signal handler can interleave with the main thread's stdout write)."""
    from structlog.testing import capture_logs

    worker = make_worker(committed, LocalVolume(tmp_path), migrated_database)
    with capture_logs() as logs:
        worker.stop()
    assert worker.stopping.is_set()
    assert logs == []
