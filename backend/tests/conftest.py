"""Test setup: a separate Postgres test database (`belegbot_test` by default).

- URL: `TEST_DATABASE_URL`, else `DATABASE_URL` with the database name set to `belegbot_test`.
- Safety guard: the run aborts before any test unless the database name ends in `_test`.
- Session: create the test database if missing, then `alembic upgrade head` once.
- Per test: one connection with an outer transaction; the `AsyncSession` joins it with
  savepoints and everything is rolled back afterwards.
- An unreachable test database makes DB tests error (never skip).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession, create_async_engine

from app.config import normalise_database_url

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_TEST_DB_NAME = "belegbot_test"


def _test_database_url() -> URL:
    raw = os.environ.get("TEST_DATABASE_URL")
    if raw:
        return make_url(normalise_database_url(raw))
    base = os.environ.get("DATABASE_URL")
    if not base:
        pytest.exit("Set TEST_DATABASE_URL or DATABASE_URL to run the tests.", returncode=4)
    return make_url(normalise_database_url(base)).set(database=DEFAULT_TEST_DB_NAME)


def _url_str(url: URL) -> str:
    return url.render_as_string(hide_password=False)


def pytest_sessionstart(session: pytest.Session) -> None:
    db_name = _test_database_url().database or ""
    if not db_name.endswith("_test"):
        pytest.exit(
            f"Refusing to run: the test DB name must end in '_test' (got {db_name!r}). "
            "Set TEST_DATABASE_URL to a dedicated test database.",
            returncode=4,
        )


@pytest.fixture(scope="session")
def test_database_url() -> str:
    return _url_str(_test_database_url())


def _psycopg_conninfo(url: URL) -> str:
    """libpq URL (no SQLAlchemy driver suffix) for the maintenance connection."""
    return _url_str(url.set(drivername="postgresql"))


@pytest.fixture(scope="session")
def migrated_database(test_database_url: str) -> str:
    """Create the test DB if needed and migrate it to head (once per session)."""
    url = make_url(test_database_url)
    db_name = url.database
    assert db_name is not None and db_name.endswith("_test")
    admin_url = url.set(database="postgres")
    try:
        with psycopg.connect(
            _psycopg_conninfo(admin_url), autocommit=True, connect_timeout=5
        ) as conn:
            exists = conn.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (db_name,)
            ).fetchone()
            if not exists:
                conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))

        cfg = Config(str(BACKEND_DIR / "alembic.ini"))
        cfg.set_main_option("script_location", str(BACKEND_DIR / "app" / "db" / "migrations"))
        cfg.attributes["database_url"] = test_database_url
        cfg.attributes["configure_logger"] = False
        command.upgrade(cfg, "head")
    except Exception as exc:
        # Fail (never skip), and report only the class name: pytest's traceback would
        # otherwise print the conninfo argument, password included.
        pytest.fail(
            f"Test database {db_name!r} unreachable or not migratable ({type(exc).__name__}).",
            pytrace=False,
        )
    return test_database_url


@pytest.fixture(scope="session")
async def db_engine(migrated_database: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(migrated_database, pool_pre_ping=True)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def db_connection(db_engine: AsyncEngine) -> AsyncIterator[AsyncConnection]:
    async with db_engine.connect() as conn:
        outer = await conn.begin()
        try:
            yield conn
        finally:
            if outer.is_active:
                await outer.rollback()


@pytest.fixture
async def db_session(db_connection: AsyncConnection) -> AsyncIterator[AsyncSession]:
    session = AsyncSession(
        bind=db_connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    )
    try:
        yield session
    finally:
        await session.close()
