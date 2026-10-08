"""tax_item.version (#10): optimistic locking for user edits.

Self-contained on purpose: no imports from `app.*`. The server default keeps existing rows,
the seed and the pipeline's inserts valid (compatible with the previous app version).

Revision ID: e5f1b8c2d6a9
Revises: c3d9a7e1f2b4
Create Date: 2026-10-08 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5f1b8c2d6a9"
down_revision: str | None = "c3d9a7e1f2b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tax_item",
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
    )


def downgrade() -> None:
    op.drop_column("tax_item", "version")
