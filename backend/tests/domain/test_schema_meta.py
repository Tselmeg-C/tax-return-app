"""Schema rules that every table must follow, now and in later issues (see CLAUDE.md)."""

from __future__ import annotations

import re

import pytest
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Base
from app.db.types import EncryptedType

# Tables that may lack household_id, with the reason.
HOUSEHOLD_ID_EXEMPT = {
    "household": "it is the tenant itself",
}

CORE_TABLES = {
    "alembic_version",
    "app_user",
    "audit_log",
    "document",
    "extraction",
    "household",
    "job",  # #6
    "magic_link_token",  # #5
    "person",
    "tax_item",
    "user_session",  # #5
}

TABLES = sorted(Base.metadata.tables.values(), key=lambda t: t.name)


def _enum_columns() -> list[tuple[sa.Table, sa.Column[object], sa.Enum]]:
    found = []
    for table in TABLES:
        for column in table.columns:
            if isinstance(column.type, sa.Enum):
                found.append((table, column, column.type))
    return found


@pytest.mark.parametrize("table", TABLES, ids=lambda t: t.name)
def test_every_table_has_household_id(table: sa.Table) -> None:
    if table.name in HOUSEHOLD_ID_EXEMPT:
        return
    assert "household_id" in table.c, f"{table.name} lacks household_id"
    column = table.c.household_id
    assert column.nullable is False
    targets = {fk.target_fullname for fk in column.foreign_keys}
    assert targets == {"household.id"}


@pytest.mark.parametrize("table", TABLES, ids=lambda t: t.name)
def test_every_fk_column_leads_an_index(table: sa.Table) -> None:
    leading = {idx.columns[0].name for idx in table.indexes}
    leading |= {
        list(c.columns)[0].name
        for c in table.constraints
        if isinstance(c, sa.UniqueConstraint | sa.PrimaryKeyConstraint) and len(c.columns)
    }
    for fk in table.foreign_key_constraints:
        first = list(fk.columns)[0].name
        assert first in leading, f"{table.name}.{first} (FK) is not the first column of an index"


@pytest.mark.parametrize("table", TABLES, ids=lambda t: t.name)
def test_encrypted_column_labels_match(table: sa.Table) -> None:
    for column in table.columns:
        if isinstance(column.type, EncryptedType):
            assert column.type.label == f"{table.name}.{column.name}"


@pytest.mark.parametrize("table", TABLES, ids=lambda t: t.name)
def test_primary_key_is_uuid_without_server_default(table: sa.Table) -> None:
    pk = list(table.primary_key.columns)
    assert [c.name for c in pk] == ["id"]
    assert isinstance(pk[0].type, sa.Uuid)
    assert pk[0].server_default is None


async def test_exact_tables_after_migration(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    )
    assert set(result.scalars()) == CORE_TABLES


async def test_no_native_enums(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        text(
            "SELECT count(*) FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace "
            "WHERE n.nspname = 'public' AND t.typtype = 'e'"
        )
    )
    assert result.scalar_one() == 0


@pytest.mark.parametrize(
    ("table", "column", "enum_type"),
    _enum_columns(),
    ids=lambda v: getattr(v, "name", None) or str(v),
)
async def test_db_enum_check_matches_python_enum(
    db_session: AsyncSession, table: sa.Table, column: sa.Column[object], enum_type: sa.Enum
) -> None:
    name = f"ck_{table.name}_{enum_type.name}"
    result = await db_session.execute(
        text(
            "SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c "
            "JOIN pg_class r ON r.oid = c.conrelid WHERE c.conname = :name AND r.relname = :table"
        ),
        {"name": name, "table": table.name},
    )
    definition = result.scalar_one()
    assert f"({column.name})::text" in definition
    # Postgres renders a one-value IN list as `= '...'::text`.
    db_values = re.findall(r"'([^']*)'::(?:character varying|text)", definition)
    assert enum_type.enum_class is not None
    py_values = [member.value for member in enum_type.enum_class]
    assert sorted(db_values) == sorted(py_values)
    assert len(db_values) == len(set(db_values))
