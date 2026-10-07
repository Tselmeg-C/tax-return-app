"""job queue table, document.original_filename (#6)

Self-contained on purpose: no imports from `app.*`. Enums are VARCHAR(64) + CHECK (values
frozen here). The job row stores ids only (never file contents, names or paths);
`document.original_filename` is plain-text display metadata (user decision, #6 Decision 15).

Revision ID: b7e4d2a91c3f
Revises: 5a1c9e3b7d42
Create Date: 2026-10-06 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7e4d2a91c3f"
down_revision: str | None = "5a1c9e3b7d42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ENUM_LENGTH = 64
JOB_KIND = ("process_document",)
JOB_STATUS = ("queued", "running", "succeeded", "failed")


def _enum_check(table: str, column: str, values: Sequence[str]) -> sa.CheckConstraint:
    allowed = ", ".join(f"'{v}'" for v in values)
    return sa.CheckConstraint(f"{column} IN ({allowed})", name=op.f(f"ck_{table}_{column}"))


def upgrade() -> None:
    op.add_column("document", sa.Column("original_filename", sa.String(255), nullable=True))
    op.create_check_constraint(
        op.f("ck_document_original_filename_length"),
        "document",
        "original_filename IS NULL OR octet_length(original_filename) BETWEEN 1 AND 255",
    )

    op.create_table(
        "job",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=ENUM_LENGTH), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=ENUM_LENGTH), server_default="queued", nullable=False),
        sa.Column("attempts", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_attempts", sa.SmallInteger(), nullable=False),
        sa.Column(
            "run_after", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("locked_by", sa.String(length=100), nullable=True),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_kind", sa.String(length=100), nullable=True),
        sa.Column("trace_context", sa.String(length=200), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job")),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["household.id"],
            name=op.f("fk_job_household_id_household"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["document.id"],
            name=op.f("fk_job_document_id_document"),
            ondelete="CASCADE",
        ),
        _enum_check("job", "kind", JOB_KIND),
        _enum_check("job", "status", JOB_STATUS),
        sa.CheckConstraint(
            "kind <> 'process_document' OR document_id IS NOT NULL",
            name=op.f("ck_job_process_document_needs_document"),
        ),
        sa.CheckConstraint(
            "attempts >= 0 AND max_attempts >= 1", name=op.f("ck_job_attempts_range")
        ),
        sa.CheckConstraint(
            "(status = 'running') = (locked_until IS NOT NULL)",
            name=op.f("ck_job_running_has_lease"),
        ),
    )
    op.create_index(
        "uq_job_kind_document_id_active",
        "job",
        ["kind", "document_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_index(
        "ix_job_run_after_queued",
        "job",
        ["run_after"],
        postgresql_where=sa.text("status = 'queued'"),
    )
    op.create_index(
        "ix_job_locked_until_running",
        "job",
        ["locked_until"],
        postgresql_where=sa.text("status = 'running'"),
    )
    op.create_index(op.f("ix_job_household_id"), "job", ["household_id"])
    op.create_index(op.f("ix_job_document_id"), "job", ["document_id"])


def downgrade() -> None:
    op.drop_table("job")
    op.drop_constraint(op.f("ck_document_original_filename_length"), "document", type_="check")
    op.drop_column("document", "original_filename")
