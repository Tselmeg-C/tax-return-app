"""person.merkzeichen_h_bl_tbl (#76): Merkzeichen H, Bl or TBl (7.400 EUR Pauschbetrag).

Self-contained on purpose: no imports from `app.*`. The server default keeps existing rows and
the previous app version's inserts valid.

Revision ID: a7c3e9d1b5f2
Revises: f2a6c4d8e1b3
Create Date: 2026-10-09 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7c3e9d1b5f2"
down_revision: str | None = "f2a6c4d8e1b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "person",
        sa.Column(
            "merkzeichen_h_bl_tbl", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_column("person", "merkzeichen_h_bl_tbl")
