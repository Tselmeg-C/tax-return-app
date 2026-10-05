"""Audit helper: diffs, redaction of encrypted columns, transaction semantics."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.audit import REDACTED, Actor, record, snapshot
from app.domain.enums import ActorType, AuditAction
from tests.domain.conftest import steuer_id_sentinel
from tests.domain.factories import make_household, make_person, make_tax_item, make_user


async def _audit_count(session: AsyncSession) -> int:
    return int((await session.execute(text("SELECT count(*) FROM audit_log"))).scalar_one())


async def test_update_stores_only_changed_keys(db_session: AsyncSession) -> None:
    hh = await make_household(db_session)
    user = await make_user(db_session, hh)
    item = await make_tax_item(db_session, hh, deductible_amount=Decimal("100.00"))
    before = snapshot(item)
    item.deductible_amount = Decimal("80.00")
    await db_session.flush()

    row = await record(
        db_session,
        household_id=hh.id,
        entity="tax_item",
        entity_id=item.id,
        action=AuditAction.UPDATE,
        before=before,
        after=snapshot(item),
        actor=Actor.user(user.id),
    )
    assert row is not None
    # updated_at changes too on a real flush; compare only the business field here.
    assert row.before is not None and row.after is not None
    assert row.before["deductible_amount"] == "100.00"
    assert row.after["deductible_amount"] == "80.00"
    assert set(row.before) == set(row.after)
    assert "gross_amount" not in row.before

    result = await db_session.execute(
        text("SELECT before, after FROM audit_log WHERE id = :id"), {"id": row.id}
    )
    stored_before, stored_after = result.one()
    assert stored_before["deductible_amount"] == "100.00"
    assert stored_after["deductible_amount"] == "80.00"


async def test_update_exact_diff(db_session: AsyncSession) -> None:
    hh = await make_household(db_session)
    item = await make_tax_item(db_session, hh, deductible_amount=Decimal("100.00"))
    before = snapshot(item)
    after = {**before, "deductible_amount": "80.00"}
    row = await record(
        db_session,
        household_id=hh.id,
        entity="tax_item",
        entity_id=item.id,
        action=AuditAction.UPDATE,
        before=before,
        after=after,
        actor=Actor.system(),
    )
    assert row is not None
    assert row.before == {"deductible_amount": "100.00"}
    assert row.after == {"deductible_amount": "80.00"}
    assert await _audit_count(db_session) == 1


async def test_noop_update_writes_nothing(db_session: AsyncSession) -> None:
    hh = await make_household(db_session)
    item = await make_tax_item(db_session, hh)
    snap = snapshot(item)
    row = await record(
        db_session,
        household_id=hh.id,
        entity="tax_item",
        entity_id=item.id,
        action=AuditAction.UPDATE,
        before=snap,
        after=dict(snap),
        actor=Actor.system(),
    )
    assert row is None
    assert await _audit_count(db_session) == 0


async def test_encrypted_values_are_redacted(db_session: AsyncSession) -> None:
    s1, s2 = steuer_id_sentinel(), steuer_id_sentinel()
    hh = await make_household(db_session)
    person = await make_person(db_session, hh, steuer_id=s1)
    before = snapshot(person)
    assert before["steuer_id"] == s1  # snapshot holds plaintext; record redacts
    after = {**before, "steuer_id": s2}
    row = await record(
        db_session,
        household_id=hh.id,
        entity="person",
        entity_id=person.id,
        action=AuditAction.UPDATE,
        before=before,
        after=after,
        actor=Actor.system(),
    )
    assert row is not None
    result = await db_session.execute(
        text("SELECT before::text, after::text FROM audit_log WHERE id = :id"), {"id": row.id}
    )
    before_text, after_text = result.one()
    for stored in (before_text, after_text):
        assert f'"steuer_id": "{REDACTED}"' in stored
        assert s1 not in stored and s2 not in stored


async def test_rollback_leaves_no_audit_row(db_session: AsyncSession) -> None:
    hh = await make_household(db_session)
    item = await make_tax_item(db_session, hh)

    class Boom(Exception):
        pass

    with pytest.raises(Boom):
        async with db_session.begin_nested():
            await record(
                db_session,
                household_id=hh.id,
                entity="tax_item",
                entity_id=item.id,
                action=AuditAction.CREATE,
                before=None,
                after=snapshot(item),
                actor=Actor.system(),
            )
            assert await _audit_count(db_session) == 1
            raise Boom
    assert await _audit_count(db_session) == 0


async def test_unknown_entity_rejected(db_session: AsyncSession) -> None:
    hh = await make_household(db_session)
    with pytest.raises(ValueError):
        await record(
            db_session,
            household_id=hh.id,
            entity="no_such_table",
            entity_id=uuid.uuid4(),
            action=AuditAction.CREATE,
            before=None,
            after={},
            actor=Actor.system(),
        )


async def test_create_delete_and_system_actor_shapes(db_session: AsyncSession) -> None:
    hh = await make_household(db_session)
    user = await make_user(db_session, hh)
    item = await make_tax_item(db_session, hh)
    snap = snapshot(item)
    created = await record(
        db_session,
        household_id=hh.id,
        entity="tax_item",
        entity_id=item.id,
        action=AuditAction.CREATE,
        before=snap,  # ignored for create
        after=snap,
        actor=Actor.user(user.id),
    )
    deleted = await record(
        db_session,
        household_id=hh.id,
        entity="tax_item",
        entity_id=item.id,
        action=AuditAction.DELETE,
        before=snap,
        after=snap,  # ignored for delete
        actor=Actor.system(),
    )
    assert created is not None and deleted is not None
    rows = await db_session.execute(
        text(
            "SELECT id, action, before IS NULL, after IS NULL, actor_type, actor_user_id "
            "FROM audit_log"
        )
    )
    by_id = {r[0]: r[1:] for r in rows}
    assert by_id[created.id] == ("create", True, False, "user", user.id)
    assert by_id[deleted.id] == ("delete", False, True, "system", None)
    assert deleted.actor_type == ActorType.SYSTEM


def test_snapshot_is_json_ready() -> None:
    from datetime import date

    from app.db.models import TaxItem
    from app.domain.enums import Category

    item = TaxItem(
        household_id=uuid.uuid4(),
        year=2025,
        category=Category.SPENDEN,
        gross_amount=Decimal("10.50"),
        deductible_amount=Decimal("10.50"),
        invoice_date=date(2025, 1, 2),
        is_relevant=True,
    )
    snap = snapshot(item)
    assert snap["gross_amount"] == "10.50"
    assert snap["invoice_date"] == "2025-01-02"
    assert snap["category"] == "spenden" and type(snap["category"]) is str
    assert snap["id"] == str(item.id)
    assert snap["household_id"] == str(item.household_id)
