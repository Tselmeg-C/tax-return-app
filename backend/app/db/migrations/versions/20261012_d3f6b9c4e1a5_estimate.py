"""estimate (#17): one cached result per household, year and inputs hash.

Self-contained on purpose: no imports from `app.*`. New table only.

Revision ID: d3f6b9c4e1a5
Revises: c2e5a8b3d0f4
Create Date: 2026-10-12 09:02:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d3f6b9c4e1a5"
down_revision: str | None = "c2e5a8b3d0f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "estimate",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("year", sa.SmallInteger(), nullable=False),
        sa.Column("params_version", sa.String(length=64), nullable=False),
        sa.Column("inputs_hash", sa.CHAR(length=64), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_estimate")),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["household.id"],
            name=op.f("fk_estimate_household_id_household"),
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "household_id", "year", "inputs_hash", name=op.f("uq_estimate_household_id")
        ),
        sa.CheckConstraint("year BETWEEN 2000 AND 2100", name=op.f("ck_estimate_year_range")),
        sa.CheckConstraint(
            "inputs_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_estimate_inputs_hash_hex")
        ),
    )


def downgrade() -> None:
    op.drop_table("estimate")
