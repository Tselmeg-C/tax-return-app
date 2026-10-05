"""core domain tables: household, app_user, person, document, extraction, tax_item, audit_log (#4)

Self-contained on purpose: no imports from `app.*`, so later model changes cannot break this
migration. Enums are VARCHAR(64) + CHECK (values frozen here); encrypted columns are plain
`bytea` holding a Fernet token.

Revision ID: 727a2e042810
Revises: 065ad205c0c8
Create Date: 2026-10-05 09:50:00.396405

"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "727a2e042810"
down_revision: str | None = "065ad205c0c8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ENUM_LENGTH = 64

USER_ROLE = ("owner", "member")
PERSON_KIND = ("adult", "child")
RELIGION = ("none", "ev", "rk", "other")
CHANNEL = ("web", "telegram")
DOCUMENT_STATUS = ("queued", "processing", "done", "needs_attention", "failed")
DOC_TYPE = (
    "generic_bill",
    "lohnsteuerbescheinigung",
    "jahressteuerbescheinigung",
    "nebenkostenabrechnung",
    "kindergeld_bescheid",
    "elterngeld_bescheid",
    "alg_bescheid",
    "other",
)
EXTRACTION_STEP = ("classify", "extract")
ANLAGE = (
    "hauptvordruck",
    "n",
    "vorsorgeaufwand",
    "av",
    "sonderausgaben",
    "agb",
    "haushaltsnahe_aufwendungen",
    "kind",
    "kap",
    "v",
)
PAYMENT_METHOD = ("cash", "bank_transfer", "direct_debit", "card", "paypal", "other", "unknown")
AUDIT_ACTION = ("create", "update", "delete")
ACTOR_TYPE = ("user", "system")
CATEGORY = (
    "wk_arbeitsmittel",
    "wk_fortbildung",
    "wk_fahrtkosten",
    "wk_homeoffice",
    "wk_arbeitszimmer",
    "wk_bewerbung",
    "wk_kontofuehrung",
    "wk_berufsverband",
    "wk_doppelte_haushaltsfuehrung",
    "steuerberatung",
    "vorsorge_kv_pv",
    "vorsorge_rv",
    "vorsorge_riester",
    "vorsorge_ruerup",
    "vorsorge_sonstige",
    "spenden",
    "kirchensteuer",
    "kinderbetreuung",
    "schulgeld",
    "krankheitskosten",
    "pflege",
    "behinderung",
    "haushaltsnahe_dienstleistung",
    "handwerkerleistung",
    "v_afa",
    "v_schuldzinsen",
    "v_erhaltung",
    "v_nebenkosten",
    "kapital_bescheinigung",
    "irrelevant",
)


def _enum_check(table: str, column: str, values: Sequence[str]) -> sa.CheckConstraint:
    allowed = ", ".join(f"'{v}'" for v in values)
    return sa.CheckConstraint(f"{column} IN ({allowed})", name=op.f(f"ck_{table}_{column}"))


def _id() -> sa.Column[Any]:
    return sa.Column("id", sa.Uuid(), nullable=False)


def _household_id(table: str) -> tuple[sa.Column[Any], sa.ForeignKeyConstraint]:
    return (
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["household.id"],
            name=op.f(f"fk_{table}_household_id_household"),
            ondelete="RESTRICT",
        ),
    )


def _created_at() -> sa.Column[Any]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def _updated_at() -> sa.Column[Any]:
    return sa.Column(
        "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def _confidence_check(table: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(
        "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
        name=op.f(f"ck_{table}_confidence_range"),
    )


def upgrade() -> None:
    op.create_table(
        "household",
        _id(),
        sa.Column("name", sa.String(length=100), nullable=False),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_household")),
    )

    op.create_table(
        "person",
        _id(),
        *_household_id("person"),
        sa.Column("kind", sa.String(length=ENUM_LENGTH), nullable=False),
        sa.Column("first_name", sa.String(length=100), nullable=False),
        sa.Column("last_name", sa.String(length=100), nullable=True),
        sa.Column("dob", sa.Date(), nullable=True),
        sa.Column("steuer_id", sa.LargeBinary(), nullable=True),  # encrypted (Fernet)
        sa.Column("religion", sa.String(length=ENUM_LENGTH), server_default="none", nullable=False),
        sa.Column("disability_grade", sa.SmallInteger(), nullable=True),
        _created_at(),
        _updated_at(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_person")),
        _enum_check("person", "kind", PERSON_KIND),
        _enum_check("person", "religion", RELIGION),
        sa.CheckConstraint(
            "kind <> 'child' OR dob IS NOT NULL", name=op.f("ck_person_child_requires_dob")
        ),
        sa.CheckConstraint(
            "disability_grade IS NULL OR "
            "(disability_grade BETWEEN 20 AND 100 AND disability_grade % 10 = 0)",
            name=op.f("ck_person_disability_grade_range"),
        ),
    )
    op.create_index(op.f("ix_person_household_id"), "person", ["household_id"])

    op.create_table(
        "app_user",
        _id(),
        *_household_id("app_user"),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("role", sa.String(length=ENUM_LENGTH), nullable=False),
        sa.Column("person_id", sa.Uuid(), nullable=True),
        _created_at(),
        _updated_at(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_app_user")),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["person.id"],
            name=op.f("fk_app_user_person_id_person"),
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("email", name=op.f("uq_app_user_email")),
        sa.UniqueConstraint("person_id", name=op.f("uq_app_user_person_id")),
        _enum_check("app_user", "role", USER_ROLE),
        sa.CheckConstraint("email = lower(email)", name=op.f("ck_app_user_email_lowercase")),
    )
    op.create_index(op.f("ix_app_user_household_id"), "app_user", ["household_id"])

    op.create_table(
        "document",
        _id(),
        *_household_id("document"),
        sa.Column("uploaded_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.String(length=ENUM_LENGTH), nullable=False),
        sa.Column("sha256", sa.CHAR(length=64), nullable=False),
        sa.Column("mime_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("page_count", sa.SmallInteger(), nullable=True),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=ENUM_LENGTH), server_default="queued", nullable=False),
        sa.Column("doc_type", sa.String(length=ENUM_LENGTH), nullable=True),
        sa.Column("error_kind", sa.String(length=100), nullable=True),
        _created_at(),
        _updated_at(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document")),
        sa.ForeignKeyConstraint(
            ["uploaded_by_user_id"],
            ["app_user.id"],
            name=op.f("fk_document_uploaded_by_user_id_app_user"),
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("household_id", "sha256", name=op.f("uq_document_household_id_sha256")),
        sa.UniqueConstraint("storage_key", name=op.f("uq_document_storage_key")),
        _enum_check("document", "channel", CHANNEL),
        _enum_check("document", "status", DOCUMENT_STATUS),
        _enum_check("document", "doc_type", DOC_TYPE),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_document_sha256_hex")),
        sa.CheckConstraint("size_bytes > 0", name=op.f("ck_document_size_bytes_positive")),
        sa.CheckConstraint(
            "page_count IS NULL OR page_count >= 1", name=op.f("ck_document_page_count_positive")
        ),
    )
    op.create_index(
        op.f("ix_document_household_id_created_at"), "document", ["household_id", "created_at"]
    )
    op.create_index(op.f("ix_document_uploaded_by_user_id"), "document", ["uploaded_by_user_id"])

    op.create_table(
        "extraction",
        _id(),
        *_household_id("extraction"),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("step", sa.String(length=ENUM_LENGTH), nullable=False),
        sa.Column("doc_type", sa.String(length=ENUM_LENGTH), nullable=True),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("prompt_version", sa.String(length=50), nullable=False),
        sa.Column("raw_json", sa.LargeBinary(), nullable=True),  # encrypted JSON (Fernet)
        sa.Column("confidence", sa.Numeric(precision=4, scale=3), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_eur", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("error_kind", sa.String(length=100), nullable=True),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_extraction")),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["document.id"],
            name=op.f("fk_extraction_document_id_document"),
            ondelete="CASCADE",
        ),
        _enum_check("extraction", "step", EXTRACTION_STEP),
        _enum_check("extraction", "doc_type", DOC_TYPE),
        _confidence_check("extraction"),
        sa.CheckConstraint(
            "input_tokens >= 0", name=op.f("ck_extraction_input_tokens_nonnegative")
        ),
        sa.CheckConstraint(
            "output_tokens >= 0", name=op.f("ck_extraction_output_tokens_nonnegative")
        ),
        sa.CheckConstraint("cost_eur >= 0", name=op.f("ck_extraction_cost_eur_nonnegative")),
        sa.CheckConstraint("latency_ms >= 0", name=op.f("ck_extraction_latency_ms_nonnegative")),
    )
    op.create_index(op.f("ix_extraction_household_id"), "extraction", ["household_id"])
    op.create_index(op.f("ix_extraction_document_id"), "extraction", ["document_id"])

    op.create_table(
        "tax_item",
        _id(),
        *_household_id("tax_item"),
        sa.Column("document_id", sa.Uuid(), nullable=True),
        sa.Column("extraction_id", sa.Uuid(), nullable=True),
        sa.Column("person_id", sa.Uuid(), nullable=True),
        sa.Column("year", sa.SmallInteger(), nullable=False),
        sa.Column("category", sa.String(length=ENUM_LENGTH), nullable=False),
        sa.Column("anlage", sa.String(length=ENUM_LENGTH), nullable=True),
        sa.Column("zeile", sa.String(length=20), nullable=True),
        sa.Column("gross_amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("deductible_amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("labour_share_35a", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("vendor", sa.String(length=200), nullable=True),
        sa.Column("invoice_date", sa.Date(), nullable=True),
        sa.Column("payment_date", sa.Date(), nullable=True),
        sa.Column(
            "payment_method",
            sa.String(length=ENUM_LENGTH),
            server_default="unknown",
            nullable=False,
        ),
        sa.Column("is_relevant", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Numeric(precision=4, scale=3), nullable=True),
        sa.Column(
            "overridden_by_user", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        _created_at(),
        _updated_at(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tax_item")),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["document.id"],
            name=op.f("fk_tax_item_document_id_document"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["extraction_id"],
            ["extraction.id"],
            name=op.f("fk_tax_item_extraction_id_extraction"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["person.id"],
            name=op.f("fk_tax_item_person_id_person"),
            ondelete="RESTRICT",
        ),
        _enum_check("tax_item", "category", CATEGORY),
        _enum_check("tax_item", "anlage", ANLAGE),
        _enum_check("tax_item", "payment_method", PAYMENT_METHOD),
        _confidence_check("tax_item"),
        sa.CheckConstraint("year BETWEEN 2000 AND 2100", name=op.f("ck_tax_item_year_range")),
        sa.CheckConstraint(
            "is_relevant OR deductible_amount = 0",
            name=op.f("ck_tax_item_irrelevant_not_deductible"),
        ),
        sa.CheckConstraint(
            "labour_share_35a IS NULL OR labour_share_35a >= 0",
            name=op.f("ck_tax_item_labour_share_35a_nonnegative"),
        ),
    )
    op.create_index(op.f("ix_tax_item_household_id_year"), "tax_item", ["household_id", "year"])
    op.create_index(op.f("ix_tax_item_document_id"), "tax_item", ["document_id"])
    op.create_index(op.f("ix_tax_item_extraction_id"), "tax_item", ["extraction_id"])
    op.create_index(op.f("ix_tax_item_person_id"), "tax_item", ["person_id"])

    op.create_table(
        "audit_log",
        _id(),
        *_household_id("audit_log"),
        sa.Column("entity", sa.String(length=63), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),  # no FK: outlives the entity
        sa.Column("action", sa.String(length=ENUM_LENGTH), nullable=False),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("actor_type", sa.String(length=ENUM_LENGTH), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),  # no FK: outlives the user
        _created_at(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
        _enum_check("audit_log", "action", AUDIT_ACTION),
        _enum_check("audit_log", "actor_type", ACTOR_TYPE),
        sa.CheckConstraint(
            "(actor_type = 'user') = (actor_user_id IS NOT NULL)",
            name=op.f("ck_audit_log_actor_user_id_matches_type"),
        ),
    )
    op.create_index(
        op.f("ix_audit_log_household_id_entity_entity_id"),
        "audit_log",
        ["household_id", "entity", "entity_id"],
    )
    op.create_index(
        op.f("ix_audit_log_household_id_created_at"), "audit_log", ["household_id", "created_at"]
    )


def downgrade() -> None:
    # Reverse dependency order; dropping a table drops its indexes and constraints.
    op.drop_table("audit_log")
    op.drop_table("tax_item")
    op.drop_table("extraction")
    op.drop_table("document")
    op.drop_table("app_user")
    op.drop_table("person")
    op.drop_table("household")
