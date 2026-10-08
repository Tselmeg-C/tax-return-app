"""CHECK, UNIQUE and ON DELETE behaviour of the core tables (raw SQL where the ORM normalises)."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from psycopg import errors as pg
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError, StatementError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AppUser, Document, TaxItem
from tests.domain.factories import (
    World,
    make_document,
    make_extraction,
    make_household,
    make_person,
    make_tax_item,
    make_user,
    make_world,
    random_sha256,
)


async def expect_violation(
    session: AsyncSession,
    sql: str,
    params: dict[str, Any],
    error: type[Exception],
    constraint: str,
) -> None:
    with pytest.raises(DBAPIError) as info:
        async with session.begin_nested():
            await session.execute(text(sql), params)
    assert isinstance(info.value.orig, error), type(info.value.orig)
    assert info.value.orig.diag.constraint_name == constraint


@pytest.fixture
async def world(db_session: AsyncSession) -> World:
    return await make_world(db_session)


# --- CHECK constraints ------------------------------------------------------------------


async def test_child_requires_dob(db_session: AsyncSession, world: World) -> None:
    await expect_violation(
        db_session,
        "UPDATE person SET kind = 'child', dob = NULL WHERE id = :id",
        {"id": world.person.id},
        pg.CheckViolation,
        "ck_person_child_requires_dob",
    )


@pytest.mark.parametrize("grade", [15, 105, 55])
async def test_disability_grade_rejected(
    db_session: AsyncSession, world: World, grade: int
) -> None:
    await expect_violation(
        db_session,
        "UPDATE person SET disability_grade = :g WHERE id = :id",
        {"g": grade, "id": world.person.id},
        pg.CheckViolation,
        "ck_person_disability_grade_range",
    )


@pytest.mark.parametrize("grade", [20, 50, 100])
async def test_disability_grade_accepted(
    db_session: AsyncSession, world: World, grade: int
) -> None:
    await db_session.execute(
        text("UPDATE person SET disability_grade = :g WHERE id = :id"),
        {"g": grade, "id": world.person.id},
    )


async def test_uppercase_email_rejected(db_session: AsyncSession, world: World) -> None:
    await expect_violation(
        db_session,
        "UPDATE app_user SET email = 'Upper@Example.com' WHERE id = :id",
        {"id": world.user.id},
        pg.CheckViolation,
        "ck_app_user_email_lowercase",
    )


async def test_orm_lowercases_and_trims_email(db_session: AsyncSession) -> None:
    hh = await make_household(db_session)
    user = await make_user(db_session, hh, email="  Mixed.Case@Example.COM ")
    assert user.email == "mixed.case@example.com"


@pytest.mark.parametrize("bad", ["ABCDEF" + "0" * 58, "z" * 64, "a" * 63 + " "])
async def test_bad_sha256_rejected(db_session: AsyncSession, world: World, bad: str) -> None:
    await expect_violation(
        db_session,
        "UPDATE document SET sha256 = :s WHERE id = :id",
        {"s": bad, "id": world.document.id},
        pg.CheckViolation,
        "ck_document_sha256_hex",
    )


async def test_size_bytes_zero_rejected(db_session: AsyncSession, world: World) -> None:
    await expect_violation(
        db_session,
        "UPDATE document SET size_bytes = 0 WHERE id = :id",
        {"id": world.document.id},
        pg.CheckViolation,
        "ck_document_size_bytes_positive",
    )


async def test_page_count_zero_rejected(db_session: AsyncSession, world: World) -> None:
    await expect_violation(
        db_session,
        "UPDATE document SET page_count = 0 WHERE id = :id",
        {"id": world.document.id},
        pg.CheckViolation,
        "ck_document_page_count_positive",
    )


async def test_year_1999_rejected(db_session: AsyncSession, world: World) -> None:
    await expect_violation(
        db_session,
        "UPDATE tax_item SET year = 1999 WHERE id = :id",
        {"id": world.tax_item.id},
        pg.CheckViolation,
        "ck_tax_item_year_range",
    )


async def test_irrelevant_item_must_not_be_deductible(
    db_session: AsyncSession, world: World
) -> None:
    await expect_violation(
        db_session,
        "UPDATE tax_item SET is_relevant = false, deductible_amount = 1 WHERE id = :id",
        {"id": world.tax_item.id},
        pg.CheckViolation,
        "ck_tax_item_irrelevant_not_deductible",
    )
    # Irrelevant with 0 deductible is fine; negative amounts (credit notes) are allowed.
    await make_tax_item(
        db_session,
        world.household,
        is_relevant=False,
        gross_amount=Decimal("-12.50"),
        deductible_amount=Decimal("0"),
    )


async def test_negative_labour_share_rejected(db_session: AsyncSession, world: World) -> None:
    await expect_violation(
        db_session,
        "UPDATE tax_item SET labour_share_35a = -1 WHERE id = :id",
        {"id": world.tax_item.id},
        pg.CheckViolation,
        "ck_tax_item_labour_share_35a_nonnegative",
    )


@pytest.mark.parametrize("table", ["tax_item", "extraction"])
async def test_confidence_above_one_rejected(
    db_session: AsyncSession, world: World, table: str
) -> None:
    row_id = world.tax_item.id if table == "tax_item" else world.extraction.id
    await expect_violation(
        db_session,
        f"UPDATE {table} SET confidence = 1.5 WHERE id = :id",
        {"id": row_id},
        pg.CheckViolation,
        f"ck_{table}_confidence_range",
    )


@pytest.mark.parametrize(
    ("column", "constraint"),
    [
        ("input_tokens", "ck_extraction_input_tokens_nonnegative"),
        ("output_tokens", "ck_extraction_output_tokens_nonnegative"),
        ("cost_eur", "ck_extraction_cost_eur_nonnegative"),
        ("latency_ms", "ck_extraction_latency_ms_nonnegative"),
    ],
)
async def test_extraction_counters_nonnegative(
    db_session: AsyncSession, world: World, column: str, constraint: str
) -> None:
    await expect_violation(
        db_session,
        f"UPDATE extraction SET {column} = -1 WHERE id = :id",
        {"id": world.extraction.id},
        pg.CheckViolation,
        constraint,
    )


async def test_user_actor_requires_user_id(db_session: AsyncSession, world: World) -> None:
    await expect_violation(
        db_session,
        "UPDATE audit_log SET actor_type = 'user', actor_user_id = NULL WHERE id = :id",
        {"id": world.audit.id},
        pg.CheckViolation,
        "ck_audit_log_actor_user_id_matches_type",
    )


async def test_system_actor_must_not_have_user_id(db_session: AsyncSession, world: World) -> None:
    await expect_violation(
        db_session,
        "UPDATE audit_log SET actor_type = 'system', actor_user_id = :u WHERE id = :id",
        {"u": uuid.uuid4(), "id": world.audit.id},
        pg.CheckViolation,
        "ck_audit_log_actor_user_id_matches_type",
    )


# --- enums ------------------------------------------------------------------------------


async def test_unknown_enum_value_rejected_by_db(db_session: AsyncSession, world: World) -> None:
    await expect_violation(
        db_session,
        "UPDATE tax_item SET category = 'foo' WHERE id = :id",
        {"id": world.tax_item.id},
        pg.CheckViolation,
        "ck_tax_item_category",
    )


async def test_unknown_enum_value_rejected_by_orm(db_session: AsyncSession, world: World) -> None:
    item = TaxItem(
        household_id=world.household.id,
        year=2025,
        category="foo",
        gross_amount=Decimal("1.00"),
        deductible_amount=Decimal("1.00"),
        is_relevant=True,
    )
    db_session.add(item)
    with pytest.raises(StatementError) as info:
        await db_session.flush()
    assert isinstance(info.value.orig, LookupError)
    assert not isinstance(info.value, DBAPIError)  # rejected before reaching the DB
    await db_session.rollback()


async def test_enum_stores_values_not_names(db_session: AsyncSession, world: World) -> None:
    raw = await db_session.execute(
        text("SELECT category FROM tax_item WHERE id = :id"), {"id": world.tax_item.id}
    )
    assert raw.scalar_one() == "wk_arbeitsmittel"


# --- uniqueness -------------------------------------------------------------------------


async def test_sha256_unique_per_household(db_session: AsyncSession) -> None:
    a = await make_household(db_session, "A")
    b = await make_household(db_session, "B")
    user_a = await make_user(db_session, a)
    user_b = await make_user(db_session, b)
    digest = random_sha256()
    await make_document(db_session, a, user_a, sha256=digest)
    await make_document(db_session, b, user_b, sha256=digest)  # other household: allowed
    with pytest.raises(IntegrityError) as info:
        async with db_session.begin_nested():
            await make_document(db_session, a, user_a, sha256=digest)
    assert isinstance(info.value.orig, pg.UniqueViolation)
    assert info.value.orig.diag.constraint_name == "uq_document_household_id_sha256"


async def test_email_unique_case_insensitive(db_session: AsyncSession) -> None:
    a = await make_household(db_session, "A")
    b = await make_household(db_session, "B")
    await make_user(db_session, a, email="A@Example.com")
    with pytest.raises(IntegrityError) as info:
        async with db_session.begin_nested():
            await make_user(db_session, b, email="a@example.com")
    assert isinstance(info.value.orig, pg.UniqueViolation)
    assert info.value.orig.diag.constraint_name == "uq_app_user_email"


# --- ON DELETE --------------------------------------------------------------------------


async def _count(session: AsyncSession, table: str, where: str, **params: Any) -> int:
    result = await session.execute(text(f"SELECT count(*) FROM {table} WHERE {where}"), params)
    return int(result.scalar_one())


async def test_deleting_document_cascades(db_session: AsyncSession, world: World) -> None:
    doc_id = world.document.id
    await db_session.execute(text("DELETE FROM document WHERE id = :id"), {"id": doc_id})
    assert await _count(db_session, "extraction", "document_id = :d", d=doc_id) == 0
    assert await _count(db_session, "tax_item", "document_id = :d", d=doc_id) == 0
    assert await _count(db_session, "tax_item", "id = :i", i=world.tax_item.id) == 0


async def test_deleting_person_with_items_rejected(db_session: AsyncSession, world: World) -> None:
    await expect_violation(
        db_session,
        "DELETE FROM person WHERE id = :id",
        {"id": world.person.id},
        pg.ForeignKeyViolation,
        "fk_tax_item_person_id_person",
    )


async def test_deleting_household_with_rows_rejected(
    db_session: AsyncSession, world: World
) -> None:
    with pytest.raises(IntegrityError) as info:
        async with db_session.begin_nested():
            await db_session.execute(
                text("DELETE FROM household WHERE id = :id"), {"id": world.household.id}
            )
    assert isinstance(info.value.orig, pg.ForeignKeyViolation)


async def test_deleting_extraction_nulls_tax_item_link(
    db_session: AsyncSession, world: World
) -> None:
    await db_session.execute(
        text("DELETE FROM extraction WHERE id = :id"), {"id": world.extraction.id}
    )
    result = await db_session.execute(
        text("SELECT extraction_id FROM tax_item WHERE id = :id"), {"id": world.tax_item.id}
    )
    assert result.scalar_one() is None


async def test_deleting_person_nulls_user_link(db_session: AsyncSession) -> None:
    hh = await make_household(db_session)
    person = await make_person(db_session, hh)
    user = await make_user(db_session, hh, person_id=person.id)
    await db_session.execute(text("DELETE FROM person WHERE id = :id"), {"id": person.id})
    result = await db_session.execute(
        text("SELECT person_id FROM app_user WHERE id = :id"), {"id": user.id}
    )
    assert result.scalar_one() is None


async def test_person_linked_to_one_user_only(db_session: AsyncSession, world: World) -> None:
    with pytest.raises(IntegrityError) as info:
        async with db_session.begin_nested():
            await make_user(db_session, world.household, person_id=world.person.id)
    assert info.value.orig is not None
    assert info.value.orig.diag.constraint_name == "uq_app_user_person_id"  # type: ignore[attr-defined]


# --- types ------------------------------------------------------------------------------


async def test_money_round_trips_exactly(db_session: AsyncSession) -> None:
    hh = await make_household(db_session)
    item = await make_tax_item(
        db_session,
        hh,
        gross_amount=Decimal("1312.40"),
        deductible_amount=Decimal("720.00"),
        labour_share_35a=Decimal("0.01"),
    )
    db_session.expunge_all()
    loaded = (await db_session.execute(select(TaxItem).where(TaxItem.id == item.id))).scalar_one()
    assert loaded.gross_amount == Decimal("1312.40")
    assert isinstance(loaded.gross_amount, Decimal)
    assert str(loaded.gross_amount) == "1312.40"
    assert str(loaded.labour_share_35a) == "0.01"


async def test_extraction_cost_has_six_decimals(db_session: AsyncSession) -> None:
    hh = await make_household(db_session)
    user = await make_user(db_session, hh)
    doc = await make_document(db_session, hh, user)
    ext = await make_extraction(db_session, hh, doc, cost_eur=Decimal("0.001234"))
    db_session.expunge_all()
    raw = await db_session.execute(
        text("SELECT cost_eur FROM extraction WHERE id = :id"), {"id": ext.id}
    )
    assert raw.scalar_one() == Decimal("0.001234")


async def test_defaults(db_session: AsyncSession) -> None:
    hh = await make_household(db_session)
    person = await make_person(db_session, hh)
    user = await make_user(db_session, hh)
    doc = await make_document(db_session, hh, user)
    item = await make_tax_item(db_session, hh)
    assert person.religion == "none"
    assert doc.status == "queued"
    assert doc.doc_type is None
    assert item.payment_method == "unknown"
    assert item.overridden_by_user is False
    assert person.created_at is not None and person.updated_at is not None
    assert isinstance(user, AppUser) and isinstance(doc, Document)


# --- document.attention_reason (#9) -----------------------------------------------------


async def test_needs_attention_requires_reason(db_session: AsyncSession, world: World) -> None:
    await expect_violation(
        db_session,
        "UPDATE document SET status = 'needs_attention', attention_reason = NULL WHERE id = :id",
        {"id": world.document.id},
        pg.CheckViolation,
        "ck_document_needs_attention_has_reason",
    )


async def test_attention_reason_must_be_known(db_session: AsyncSession, world: World) -> None:
    await expect_violation(
        db_session,
        "UPDATE document SET attention_reason = 'foo' WHERE id = :id",
        {"id": world.document.id},
        pg.CheckViolation,
        "ck_document_attention_reason",
    )


async def test_needs_attention_with_reason_accepted(db_session: AsyncSession, world: World) -> None:
    await db_session.execute(
        text(
            "UPDATE document SET status = 'needs_attention', attention_reason = 'sum_mismatch' "
            "WHERE id = :id"
        ),
        {"id": world.document.id},
    )
