"""lohnersatz (#17): Elterngeld / ALG I / Kurzarbeitergeld of a person and year (manual input).

Self-contained on purpose: no imports from `app.*`. New table only.

Revision ID: c2e5a8b3d0f4
Revises: b1d4f7a2c9e3
Create Date: 2026-10-12 09:01:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c2e5a8b3d0f4"
down_revision: str | None = "b1d4f7a2c9e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lohnersatz",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("person_id", sa.Uuid(), nullable=False),
        sa.Column("year", sa.SmallInteger(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lohnersatz")),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["household.id"],
            name=op.f("fk_lohnersatz_household_id_household"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["person.id"],
            name=op.f("fk_lohnersatz_person_id_person"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("person_id", "year", name=op.f("uq_lohnersatz_person_id")),
        sa.CheckConstraint("year BETWEEN 2000 AND 2100", name=op.f("ck_lohnersatz_year_range")),
        sa.CheckConstraint("amount >= 0", name=op.f("ck_lohnersatz_amount_nonnegative")),
    )
    op.create_index(op.f("ix_lohnersatz_household_id"), "lohnersatz", ["household_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_lohnersatz_household_id"), table_name="lohnersatz")
    op.drop_table("lohnersatz")
