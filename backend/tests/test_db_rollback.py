"""The per-test fixtures roll back everything a test writes, even an explicit commit.

The two tests run in file order: the first writes a table and a row and commits,
the second checks from a fresh connection that neither exists.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

TABLE = "rollback_probe"


async def _table_exists(engine: AsyncEngine) -> bool:
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT to_regclass(:name) IS NOT NULL"), {"name": TABLE})
        return bool(result.scalar_one())


async def test_1_write_table_and_row(db_session: AsyncSession, db_engine: AsyncEngine) -> None:
    assert not await _table_exists(db_engine)
    await db_session.execute(text(f"CREATE TABLE {TABLE} (id int PRIMARY KEY)"))
    await db_session.execute(text(f"INSERT INTO {TABLE} (id) VALUES (1)"))
    await db_session.commit()  # releases only the savepoint; the outer transaction stays open
    count = await db_session.execute(text(f"SELECT count(*) FROM {TABLE}"))
    assert count.scalar_one() == 1
    # Invisible to other connections while the outer transaction is open.
    assert not await _table_exists(db_engine)


async def test_2_previous_writes_are_gone(db_session: AsyncSession, db_engine: AsyncEngine) -> None:
    assert not await _table_exists(db_engine)
    result = await db_session.execute(
        text("SELECT to_regclass(:name) IS NOT NULL"), {"name": TABLE}
    )
    assert result.scalar_one() is False


async def test_runs_against_test_database(db_session: AsyncSession) -> None:
    result = await db_session.execute(text("SELECT current_database()"))
    assert result.scalar_one().endswith("_test")
    version = await db_session.execute(text("SELECT count(*) FROM alembic_version"))
    assert version.scalar_one() == 1
