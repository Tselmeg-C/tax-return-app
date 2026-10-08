"""document.attention_reason (#9)

Self-contained on purpose: no imports from `app.*`. The values are frozen here
(`AttentionReason` in `app/domain/enums.py`, priority order).

Revision ID: c3d9a7e1f2b4
Revises: b7e4d2a91c3f
Create Date: 2026-10-07 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3d9a7e1f2b4"
down_revision: str | None = "b7e4d2a91c3f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ATTENTION_REASON = (
    "classification_failed",
    "extraction_failed",
    "unreadable",
    "multiple_documents",
    "doc_type_not_supported",
    "possible_duplicate",
    "foreign_currency",
    "implausible_amount",
    "implausible_date",
    "sum_mismatch",
    "sign_mismatch",
    "credit_note_35a",
    "labour_share_missing",
    "payment_method_unknown_35a",
    "multiple_categories",
    "asset_depreciation",
    "date_missing",
    "unsupported_year",
    "year_boundary_recurring",
    "low_confidence",
)


def upgrade() -> None:
    op.add_column("document", sa.Column("attention_reason", sa.String(64), nullable=True))
    allowed = ", ".join(f"'{v}'" for v in ATTENTION_REASON)
    op.create_check_constraint(
        op.f("ck_document_attention_reason"),
        "document",
        f"attention_reason IN ({allowed})",
    )
    op.create_check_constraint(
        op.f("ck_document_needs_attention_has_reason"),
        "document",
        "status <> 'needs_attention' OR attention_reason IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_document_needs_attention_has_reason"), "document", type_="check")
    op.drop_constraint(op.f("ck_document_attention_reason"), "document", type_="check")
    op.drop_column("document", "attention_reason")
