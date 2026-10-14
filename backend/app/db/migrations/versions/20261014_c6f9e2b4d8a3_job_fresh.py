"""job.fresh (#45): "Neu verarbeiten" with `fresh` ignores outputs stored before the job.

Self-contained on purpose: no imports from `app.*`. One nullable-free column with a default.

Revision ID: c6f9e2b4d8a3
Revises: b5e8d1a3c7f2
Create Date: 2026-10-14 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c6f9e2b4d8a3"
down_revision: str | None = "b5e8d1a3c7f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("job", sa.Column("fresh", sa.Boolean(), server_default="false", nullable=False))


def downgrade() -> None:
    op.drop_column("job", "fresh")
