"""initial (empty): no tables yet; domain tables arrive in #4.

Revision ID: 065ad205c0c8
Revises:
Create Date: 2026-10-05 09:30:56.243413

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "065ad205c0c8"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
