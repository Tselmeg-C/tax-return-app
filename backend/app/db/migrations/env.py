"""Alembic environment (async, psycopg 3). The URL comes from Settings, never from alembic.ini.

Migrations run only in Railway's pre-deploy step (`alembic upgrade head`), never at app or
worker startup. A Postgres advisory lock serialises overlapping runs (two quick merges, or
a manual run during a deploy), so they cannot race on `alembic_version`.
"""

import asyncio
import os

from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings, normalise_database_url
from app.db.base import metadata
from app.observability.logs import configure_logging

# Fixed, arbitrary key for pg_advisory_lock; only migrations use it.
MIGRATION_LOCK_KEY = 0x62656C6567  # "beleg"

config = context.config

if config.attributes.get("configure_logger", True):
    # Same JSON lines as the api/worker; no OTel export from the short-lived migration run.
    configure_logging("belegbot-migrations", level=os.environ.get("LOG_LEVEL", "INFO"))

target_metadata = metadata


def _database_url() -> str:
    override = config.attributes.get("database_url")
    if isinstance(override, str):
        return normalise_database_url(override)
    return get_settings().database_url.get_secret_value()


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_sync_migrations(connection: Connection) -> None:
    # Session-level lock: waits for any concurrent run, survives the commits below and is
    # released explicitly (or when the connection closes, e.g. if the process dies).
    connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": MIGRATION_LOCK_KEY})
    connection.commit()
    try:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
        connection.commit()
    finally:
        if connection.in_transaction():
            connection.rollback()
        connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": MIGRATION_LOCK_KEY})
        connection.commit()


async def run_migrations_online() -> None:
    engine = create_async_engine(_database_url(), poolclass=pool.NullPool)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_run_sync_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
