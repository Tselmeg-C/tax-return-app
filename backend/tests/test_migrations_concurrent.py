"""Two `alembic upgrade head` runs started at the same moment both succeed (advisory lock)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from psycopg import sql
from sqlalchemy.engine import make_url

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _conninfo(url: str, database: str) -> str:
    return (
        make_url(url)
        .set(drivername="postgresql", database=database)
        .render_as_string(hide_password=False)
    )


@pytest.fixture
def fresh_database(test_database_url: str) -> Iterator[str]:
    """A new, empty database whose name ends in `_test`; dropped afterwards."""
    name = f"belegbot_mig_{uuid.uuid4().hex[:8]}_test"
    admin = _conninfo(test_database_url, "postgres")
    with psycopg.connect(admin, autocommit=True, connect_timeout=5) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield make_url(test_database_url).set(database=name).render_as_string(hide_password=False)
    finally:
        with psycopg.connect(admin, autocommit=True, connect_timeout=5) as conn:
            conn.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )


def _alembic(database_url: str, *args: str) -> subprocess.Popen[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("OTEL_")}
    env["DATABASE_URL"] = database_url
    return subprocess.Popen(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


@pytest.mark.parametrize("attempt", [1, 2])
def test_concurrent_upgrade_head(fresh_database: str, attempt: int) -> None:
    runs = [_alembic(fresh_database, "upgrade", "head") for _ in range(2)]
    outputs = [run.communicate(timeout=120)[0] for run in runs]
    for run, output in zip(runs, outputs, strict=True):
        # Only the exit code is reported; output could contain connection details.
        assert run.returncode == 0, f"alembic exited {run.returncode}"
        for line in output.splitlines():
            json.loads(line)  # migration logs are JSON lines too

    dbname = make_url(fresh_database).database or ""
    with psycopg.connect(_conninfo(fresh_database, dbname), connect_timeout=5) as conn:
        rows = conn.execute("SELECT version_num FROM alembic_version").fetchall()
    assert len(rows) == 1

    check = _alembic(fresh_database, "check")
    check.communicate(timeout=120)
    assert check.returncode == 0
