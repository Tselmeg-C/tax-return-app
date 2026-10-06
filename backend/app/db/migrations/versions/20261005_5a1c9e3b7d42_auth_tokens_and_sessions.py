"""auth: magic_link_token, user_session, app_user.disabled_at (#5)

Self-contained on purpose: no imports from `app.*`. Both tables store only `sha256(token)`
(64 lowercase hex chars); no IP address or user agent is stored.

Revision ID: 5a1c9e3b7d42
Revises: 727a2e042810
Create Date: 2026-10-05 12:00:00.000000

"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5a1c9e3b7d42"
down_revision: str | None = "727a2e042810"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TOKEN_HASH_HEX = "token_hash ~ '^[0-9a-f]{64}$'"


def _common(table: str) -> list[Any]:
    """id, household_id (FK RESTRICT), user_id (FK CASCADE), token_hash, created_at."""
    return [
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.CHAR(length=64), nullable=False),
        sa.Column(
            "created_at",
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
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app_user.id"],
            name=op.f(f"fk_{table}_user_id_app_user"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("token_hash", name=op.f(f"uq_{table}_token_hash")),
        sa.CheckConstraint(TOKEN_HASH_HEX, name=op.f(f"ck_{table}_token_hash_hex")),
    ]


def upgrade() -> None:
    op.add_column("app_user", sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "magic_link_token",
        *_common("magic_link_token"),
        sa.Column("redirect_path", sa.String(length=512), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "expires_at > created_at", name=op.f("ck_magic_link_token_expires_after_created")
        ),
    )
    op.create_index(op.f("ix_magic_link_token_household_id"), "magic_link_token", ["household_id"])
    op.create_index(
        "ix_magic_link_token_user_id_created_at", "magic_link_token", ["user_id", "created_at"]
    )

    op.create_table(
        "user_session",
        *_common("user_session"),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(op.f("ix_user_session_household_id"), "user_session", ["household_id"])
    op.create_index(op.f("ix_user_session_user_id"), "user_session", ["user_id"])


def downgrade() -> None:
    op.drop_table("user_session")
    op.drop_table("magic_link_token")
    op.drop_column("app_user", "disabled_at")
