"""magic_link_token.new_email (#34)

Self-contained on purpose: no imports from `app.*`.

Revision ID: b5e8d1a3c7f2
Revises: a8d3c6e1f2b7
Create Date: 2026-10-14 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b5e8d1a3c7f2"
down_revision: str | None = "a8d3c6e1f2b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("magic_link_token", sa.Column("new_email", sa.String(length=320), nullable=True))


def downgrade() -> None:
    op.drop_column("magic_link_token", "new_email")
