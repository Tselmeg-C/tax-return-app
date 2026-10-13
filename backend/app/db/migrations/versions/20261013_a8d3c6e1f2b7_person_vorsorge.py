"""person_vorsorge (#98): manual Vorsorge amounts of a person without employment.

Self-contained on purpose: no imports from `app.*`. New table only.

Revision ID: a8d3c6e1f2b7
Revises: 9c2e5b8d1f47
Create Date: 2026-10-13 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a8d3c6e1f2b7"
down_revision: str | None = "9c2e5b8d1f47"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "person_vorsorge",
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
        sa.Column("rv_employee", sa.Numeric(12, 2), nullable=True),
        sa.Column("rv_employer", sa.Numeric(12, 2), nullable=True),
        sa.Column("basisrente", sa.Numeric(12, 2), nullable=True),
        sa.Column("kv", sa.Numeric(12, 2), nullable=True),
        sa.Column("pv", sa.Numeric(12, 2), nullable=True),
        sa.Column("sonstige_vorsorge", sa.Numeric(12, 2), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_person_vorsorge")),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["household.id"],
            name=op.f("fk_person_vorsorge_household_id_household"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["person.id"],
            name=op.f("fk_person_vorsorge_person_id_person"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("person_id", "year", name=op.f("uq_person_vorsorge_person_id")),
        sa.CheckConstraint(
            "year BETWEEN 2000 AND 2100", name=op.f("ck_person_vorsorge_year_range")
        ),
        sa.CheckConstraint(
            "rv_employee IS NULL OR rv_employee >= 0",
            name=op.f("ck_person_vorsorge_rv_employee_nonnegative"),
        ),
        sa.CheckConstraint(
            "rv_employer IS NULL OR rv_employer >= 0",
            name=op.f("ck_person_vorsorge_rv_employer_nonnegative"),
        ),
        sa.CheckConstraint(
            "basisrente IS NULL OR basisrente >= 0",
            name=op.f("ck_person_vorsorge_basisrente_nonnegative"),
        ),
        sa.CheckConstraint("kv IS NULL OR kv >= 0", name=op.f("ck_person_vorsorge_kv_nonnegative")),
        sa.CheckConstraint("pv IS NULL OR pv >= 0", name=op.f("ck_person_vorsorge_pv_nonnegative")),
        sa.CheckConstraint(
            "sonstige_vorsorge IS NULL OR sonstige_vorsorge >= 0",
            name=op.f("ck_person_vorsorge_sonstige_vorsorge_nonnegative"),
        ),
    )
    op.create_index(op.f("ix_person_vorsorge_household_id"), "person_vorsorge", ["household_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_person_vorsorge_household_id"), table_name="person_vorsorge")
    op.drop_table("person_vorsorge")
