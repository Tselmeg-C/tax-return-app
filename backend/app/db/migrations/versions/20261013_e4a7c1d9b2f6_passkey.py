"""passkey (#12)

Self-contained on purpose: no imports from `app.*`.

Revision ID: e4a7c1d9b2f6
Revises: d3f6b9c4e1a5
Create Date: 2026-10-13 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "e4a7c1d9b2f6"
down_revision: str | None = "d3f6b9c4e1a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "passkey",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("credential_id", sa.LargeBinary(), nullable=False),
        sa.Column("public_key", sa.LargeBinary(), nullable=False),
        sa.Column("sign_count", sa.Integer(), nullable=False),
        sa.Column("transports", postgresql.JSONB(none_as_null=True), nullable=True),
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_passkey")),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["household.id"],
            name=op.f("fk_passkey_household_id_household"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app_user.id"],
            name=op.f("fk_passkey_user_id_app_user"),
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("credential_id", name=op.f("uq_passkey_credential_id")),
    )
    op.create_index(op.f("ix_passkey_household_id"), "passkey", ["household_id"])
    op.create_index(op.f("ix_passkey_user_id"), "passkey", ["user_id"])


def downgrade() -> None:
    op.drop_table("passkey")
