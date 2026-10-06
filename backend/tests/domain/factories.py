"""Builders for valid rows (fictional data only). Each `make_*` adds and flushes."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.exc import StatementError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.audit import Actor, record, snapshot
from app.db.models import (
    AppUser,
    AuditLog,
    Document,
    Extraction,
    Household,
    MagicLinkToken,
    Person,
    TaxItem,
    UserSession,
)
from app.domain.enums import (
    AuditAction,
    Category,
    Channel,
    ExtractionStep,
    PersonKind,
    UserRole,
)


def unwrap(exc: BaseException) -> BaseException:
    """SQLAlchemy wraps errors raised while binding parameters in `StatementError`."""
    if isinstance(exc, StatementError) and exc.orig is not None:
        return exc.orig
    return exc


def random_sha256() -> str:
    return hashlib.sha256(secrets.token_bytes(16)).hexdigest()


async def make_household(session: AsyncSession, name: str = "Testhaushalt") -> Household:
    hh = Household(name=name)
    session.add(hh)
    await session.flush()
    return hh


async def make_person(session: AsyncSession, household: Household, **kw: Any) -> Person:
    values: dict[str, Any] = {"kind": PersonKind.ADULT, "first_name": "Testperson"}
    values.update(kw)
    person = Person(household_id=household.id, **values)
    session.add(person)
    await session.flush()
    return person


async def make_user(session: AsyncSession, household: Household, **kw: Any) -> AppUser:
    values: dict[str, Any] = {
        "email": f"user-{secrets.token_hex(4)}@example.com",
        "role": UserRole.OWNER,
    }
    values.update(kw)
    user = AppUser(household_id=household.id, **values)
    session.add(user)
    await session.flush()
    return user


async def make_document(
    session: AsyncSession, household: Household, user: AppUser, **kw: Any
) -> Document:
    values: dict[str, Any] = {
        "channel": Channel.WEB,
        "sha256": random_sha256(),
        "mime_type": "application/pdf",
        "size_bytes": 1234,
        "storage_key": f"test/{uuid.uuid4()}",
    }
    values.update(kw)
    doc = Document(household_id=household.id, uploaded_by_user_id=user.id, **values)
    session.add(doc)
    await session.flush()
    return doc


async def make_extraction(
    session: AsyncSession, household: Household, document: Document, **kw: Any
) -> Extraction:
    values: dict[str, Any] = {
        "step": ExtractionStep.EXTRACT,
        "provider": "test",
        "model": "test-model",
        "prompt_version": "v0",
        "input_tokens": 10,
        "output_tokens": 5,
        "cost_eur": Decimal("0.000123"),
        "latency_ms": 42,
    }
    values.update(kw)
    ext = Extraction(household_id=household.id, document_id=document.id, **values)
    session.add(ext)
    await session.flush()
    return ext


async def make_tax_item(session: AsyncSession, household: Household, **kw: Any) -> TaxItem:
    values: dict[str, Any] = {
        "year": 2025,
        "category": Category.WK_ARBEITSMITTEL,
        "gross_amount": Decimal("100.00"),
        "deductible_amount": Decimal("100.00"),
        "is_relevant": True,
    }
    values.update(kw)
    item = TaxItem(household_id=household.id, **values)
    session.add(item)
    await session.flush()
    return item


async def make_link_token(
    session: AsyncSession, household: Household, user: AppUser, **kw: Any
) -> MagicLinkToken:
    now = datetime.now(UTC)
    values: dict[str, Any] = {
        "token_hash": random_sha256(),
        "created_at": now,
        "expires_at": now + timedelta(minutes=15),
    }
    values.update(kw)
    token = MagicLinkToken(household_id=household.id, user_id=user.id, **values)
    session.add(token)
    await session.flush()
    return token


async def make_user_session(
    session: AsyncSession, household: Household, user: AppUser, **kw: Any
) -> UserSession:
    now = datetime.now(UTC)
    values: dict[str, Any] = {
        "token_hash": random_sha256(),
        "created_at": now,
        "last_seen_at": now,
        "expires_at": now + timedelta(days=30),
    }
    values.update(kw)
    row = UserSession(household_id=household.id, user_id=user.id, **values)
    session.add(row)
    await session.flush()
    return row


@dataclass
class World:
    household: Household
    person: Person
    user: AppUser
    document: Document
    extraction: Extraction
    tax_item: TaxItem
    audit: AuditLog
    link_token: MagicLinkToken
    user_session: UserSession

    def by_model(self) -> dict[type[Any], Any]:
        return {
            Household: self.household,
            Person: self.person,
            AppUser: self.user,
            Document: self.document,
            Extraction: self.extraction,
            TaxItem: self.tax_item,
            AuditLog: self.audit,
            MagicLinkToken: self.link_token,
            UserSession: self.user_session,
        }


async def make_world(session: AsyncSession, name: str = "Testhaushalt") -> World:
    """One household holding one row of every household-owned table."""
    hh = await make_household(session, name)
    person = await make_person(session, hh)
    user = await make_user(session, hh, person_id=person.id)
    doc = await make_document(session, hh, user)
    ext = await make_extraction(session, hh, doc)
    item = await make_tax_item(
        session, hh, document_id=doc.id, extraction_id=ext.id, person_id=person.id
    )
    audit = await record(
        session,
        household_id=hh.id,
        entity="tax_item",
        entity_id=item.id,
        action=AuditAction.CREATE,
        before=None,
        after=snapshot(item),
        actor=Actor.user(user.id),
    )
    assert audit is not None
    link_token = await make_link_token(session, hh, user)
    user_session = await make_user_session(session, hh, user)
    return World(hh, person, user, doc, ext, item, audit, link_token, user_session)
