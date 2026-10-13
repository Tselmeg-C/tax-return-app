"""employment: manual Lohnsteuer and Vorsorge inputs (#17), until #18 reads the LStB.

Self-contained on purpose: no imports from `app.*`. Nullable columns only, so the previous app
version keeps working. NULL = not entered.

Revision ID: b1d4f7a2c9e3
Revises: a7c3e9d1b5f2
Create Date: 2026-10-12 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b1d4f7a2c9e3"
down_revision: str | None = "a7c3e9d1b5f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = (
    "gross_wage",
    "lohnsteuer",
    "soli_withheld",
    "kirchensteuer_withheld",
    "rv_employee",
    "rv_employer",
    "basisrente",
    "kv",
    "pv",
    "sonstige_vorsorge",
)


def upgrade() -> None:
    for name in COLUMNS:
        op.add_column("employment", sa.Column(name, sa.Numeric(12, 2), nullable=True))
        op.create_check_constraint(
            op.f(f"ck_employment_{name}_nonnegative"),
            "employment",
            f"{name} IS NULL OR {name} >= 0",
        )


def downgrade() -> None:
    for name in reversed(COLUMNS):
        op.drop_constraint(op.f(f"ck_employment_{name}_nonnegative"), "employment", type_="check")
        op.drop_column("employment", name)
