"""household profile (#13): tax_profile, employment, child_year

Self-contained on purpose: no imports from `app.*`. Enums are VARCHAR(64) + CHECK (values
frozen here). New tables only, so the previous app version keeps working.

Revision ID: f2a6c4d8e1b3
Revises: e5f1b8c2d6a9
Create Date: 2026-10-08 12:00:00.000000

"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f2a6c4d8e1b3"
down_revision: str | None = "e5f1b8c2d6a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ENUM_LENGTH = 64
FILING_STATUS = ("single", "joint")
BUNDESLAND = (
    "bw", "by", "be", "bb", "hb", "hh", "he", "mv",
    "ni", "nw", "rp", "sl", "sn", "st", "sh", "th",
)  # fmt: skip
STEUERKLASSE = ("1", "2", "3", "4", "5", "6")
ALLOWANCE_SHARE = ("full", "half")
YEAR_RANGE = "year BETWEEN 2000 AND 2100"


def _enum_check(table: str, column: str, values: Sequence[str]) -> sa.CheckConstraint:
    allowed = ", ".join(f"'{v}'" for v in values)
    return sa.CheckConstraint(f"{column} IN ({allowed})", name=op.f(f"ck_{table}_{column}"))


def _common(table: str) -> list[Any]:
    return [
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
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{table}")),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["household.id"],
            name=op.f(f"fk_{table}_household_id_household"),
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(YEAR_RANGE, name=op.f(f"ck_{table}_year_range")),
    ]


def _person_fk(table: str, column: str, ondelete: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], ["person.id"], name=op.f(f"fk_{table}_{column}_person"), ondelete=ondelete
    )


def upgrade() -> None:
    op.create_table(
        "tax_profile",
        *_common("tax_profile"),
        sa.Column("year", sa.SmallInteger(), nullable=False),
        sa.Column("filing_status", sa.String(length=ENUM_LENGTH), nullable=False),
        sa.Column("bundesland", sa.String(length=ENUM_LENGTH), nullable=False),
        sa.Column("taxpayer_person_id", sa.Uuid(), nullable=False),
        sa.Column("spouse_person_id", sa.Uuid(), nullable=True),
        _person_fk("tax_profile", "taxpayer_person_id", "RESTRICT"),
        _person_fk("tax_profile", "spouse_person_id", "RESTRICT"),
        sa.UniqueConstraint("household_id", "year", name=op.f("uq_tax_profile_household_id")),
        _enum_check("tax_profile", "filing_status", FILING_STATUS),
        _enum_check("tax_profile", "bundesland", BUNDESLAND),
        sa.CheckConstraint(
            "(filing_status = 'joint') = (spouse_person_id IS NOT NULL)",
            name=op.f("ck_tax_profile_spouse_iff_joint"),
        ),
        sa.CheckConstraint(
            "spouse_person_id IS NULL OR spouse_person_id <> taxpayer_person_id",
            name=op.f("ck_tax_profile_spouse_not_taxpayer"),
        ),
    )
    op.create_index(
        op.f("ix_tax_profile_taxpayer_person_id"), "tax_profile", ["taxpayer_person_id"]
    )
    op.create_index(op.f("ix_tax_profile_spouse_person_id"), "tax_profile", ["spouse_person_id"])

    op.create_table(
        "employment",
        *_common("employment"),
        sa.Column("person_id", sa.Uuid(), nullable=False),
        sa.Column("year", sa.SmallInteger(), nullable=False),
        sa.Column("employer_name", sa.String(length=200), nullable=False),
        sa.Column("steuerklasse", sa.String(length=ENUM_LENGTH), nullable=False),
        sa.Column("has_factor", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("commute_km", sa.SmallInteger(), nullable=True),
        sa.Column("office_days", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "homeoffice_days", sa.SmallInteger(), server_default=sa.text("0"), nullable=False
        ),
        _person_fk("employment", "person_id", "CASCADE"),
        _enum_check("employment", "steuerklasse", STEUERKLASSE),
        sa.CheckConstraint(
            "NOT has_factor OR steuerklasse = '4'", name=op.f("ck_employment_factor_requires_iv")
        ),
        sa.CheckConstraint(
            "commute_km IS NULL OR commute_km BETWEEN 0 AND 999",
            name=op.f("ck_employment_commute_km"),
        ),
        sa.CheckConstraint("office_days BETWEEN 0 AND 366", name=op.f("ck_employment_office_days")),
        sa.CheckConstraint(
            "homeoffice_days BETWEEN 0 AND 366", name=op.f("ck_employment_homeoffice_days")
        ),
    )
    op.create_index(op.f("ix_employment_household_id"), "employment", ["household_id"])
    op.create_index(op.f("ix_employment_person_id"), "employment", ["person_id", "year"])

    op.create_table(
        "child_year",
        *_common("child_year"),
        sa.Column("person_id", sa.Uuid(), nullable=False),
        sa.Column("year", sa.SmallInteger(), nullable=False),
        sa.Column("months", sa.SmallInteger(), nullable=False),
        sa.Column("allowance_share", sa.String(length=ENUM_LENGTH), nullable=False),
        sa.Column("in_household", sa.Boolean(), server_default=sa.true(), nullable=False),
        _person_fk("child_year", "person_id", "CASCADE"),
        sa.UniqueConstraint("person_id", "year", name=op.f("uq_child_year_person_id")),
        _enum_check("child_year", "allowance_share", ALLOWANCE_SHARE),
        sa.CheckConstraint("months BETWEEN 0 AND 12", name=op.f("ck_child_year_months_range")),
    )
    op.create_index(op.f("ix_child_year_household_id"), "child_year", ["household_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_child_year_household_id"), table_name="child_year")
    op.drop_table("child_year")
    op.drop_index(op.f("ix_employment_person_id"), table_name="employment")
    op.drop_index(op.f("ix_employment_household_id"), table_name="employment")
    op.drop_table("employment")
    op.drop_index(op.f("ix_tax_profile_spouse_person_id"), table_name="tax_profile")
    op.drop_index(op.f("ix_tax_profile_taxpayer_person_id"), table_name="tax_profile")
    op.drop_table("tax_profile")
