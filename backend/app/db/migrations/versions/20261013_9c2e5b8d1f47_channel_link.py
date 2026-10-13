"""channel_link, channel_link_code (#11)

Self-contained on purpose: no imports from `app.*`. `channel_link.external_id` is the
encrypted chat id (`bytea`); `external_id_hash` is its lookup key. `channel_link_code` holds
only `sha256(code)`.

Revision ID: 9c2e5b8d1f47
Revises: d3f6b9c4e1a5
Create Date: 2026-10-13 09:00:00.000000

"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9c2e5b8d1f47"
down_revision: str | None = "d3f6b9c4e1a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _created_at() -> sa.Column[Any]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "channel_link",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        _created_at(),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.String(length=64), nullable=False),
        sa.Column("external_id", sa.LargeBinary(), nullable=False),
        sa.Column("external_id_hash", sa.CHAR(length=64), nullable=False),
        sa.Column("year", sa.SmallInteger(), nullable=True),
        sa.Column("last_update_id", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_channel_link")),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["household.id"],
            name=op.f("fk_channel_link_household_id_household"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app_user.id"],
            name=op.f("fk_channel_link_user_id_app_user"),
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "channel", "external_id_hash", name=op.f("uq_channel_link_channel_external_id_hash")
        ),
        sa.UniqueConstraint("user_id", "channel", name=op.f("uq_channel_link_user_id_channel")),
        sa.CheckConstraint(
            "external_id_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_channel_link_external_id_hash_hex")
        ),
        sa.CheckConstraint("channel IN ('web', 'telegram')", name=op.f("ck_channel_link_channel")),
    )
    op.create_index(op.f("ix_channel_link_household_id"), "channel_link", ["household_id"])

    op.create_table(
        "channel_link_code",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        _created_at(),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("code_hash", sa.CHAR(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_channel_link_code")),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["household.id"],
            name=op.f("fk_channel_link_code_household_id_household"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app_user.id"],
            name=op.f("fk_channel_link_code_user_id_app_user"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("code_hash", name=op.f("uq_channel_link_code_code_hash")),
        sa.CheckConstraint(
            "code_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_channel_link_code_code_hash_hex")
        ),
    )
    op.create_index(
        op.f("ix_channel_link_code_household_id"), "channel_link_code", ["household_id"]
    )
    op.create_index(op.f("ix_channel_link_code_user_id"), "channel_link_code", ["user_id"])


def downgrade() -> None:
    op.drop_table("channel_link_code")
    op.drop_table("channel_link")
