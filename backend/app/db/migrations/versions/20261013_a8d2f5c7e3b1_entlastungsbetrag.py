"""tax_profile.shares_household_with_adult (#81)

Self-contained on purpose: no imports from `app.*`.

Revision ID: a8d2f5c7e3b1
Revises: 9c2e5b8d1f47
Create Date: 2026-10-13 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a8d2f5c7e3b1"
down_revision: str | None = "9c2e5b8d1f47"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tax_profile",
        sa.Column(
            "shares_household_with_adult",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("tax_profile", "shares_household_with_adult")
