"""Encrypted columns, key handling and PII-free errors. Sentinels are generated at runtime."""

from __future__ import annotations

import traceback

import pytest
from psycopg import errors as pg
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.crypto import DecryptionError, EncryptionKeyError
from app.db.models import Extraction, Person
from app.db.session import create_engine
from tests.domain.conftest import KeyControl, new_key, steuer_id_sentinel, text_sentinel
from tests.domain.factories import (
    make_document,
    make_extraction,
    make_household,
    make_person,
    make_user,
    unwrap,
)

HIDDEN = "[SQL parameters hidden due to hide_parameters=True]"


async def _raw(session: AsyncSession, sql: str, **params: object) -> bytes | None:
    value = (await session.execute(text(sql), params)).scalar_one()
    return None if value is None else bytes(value)


async def _read_steuer_id(session: AsyncSession, person: Person) -> str | None:
    return await session.scalar(select(Person.steuer_id).where(Person.id == person.id))


def _everything(exc: BaseException) -> str:
    return "\n".join([str(exc), repr(exc), "".join(traceback.format_exception(exc))])


async def test_steuer_id_round_trip_and_ciphertext(db_session: AsyncSession) -> None:
    sentinel = steuer_id_sentinel()
    hh = await make_household(db_session)
    person = await make_person(db_session, hh, steuer_id=sentinel)
    db_session.expunge_all()

    assert await _read_steuer_id(db_session, person) == sentinel
    raw = await _raw(db_session, "SELECT steuer_id FROM person WHERE id = :id", id=person.id)
    assert raw is not None
    assert raw.startswith(b"gAAAAA")
    assert sentinel.encode() not in raw


async def test_raw_json_round_trip_and_ciphertext(db_session: AsyncSession) -> None:
    sentinel = text_sentinel("doc-content")
    payload = {"fields": {"name": sentinel, "lines": [1, 2.5, None, True]}, "ok": True}
    hh = await make_household(db_session)
    user = await make_user(db_session, hh)
    doc = await make_document(db_session, hh, user)
    ext = await make_extraction(db_session, hh, doc, raw_json=payload)
    db_session.expunge_all()

    loaded = await db_session.scalar(select(Extraction.raw_json).where(Extraction.id == ext.id))
    assert loaded == payload
    raw = await _raw(db_session, "SELECT raw_json FROM extraction WHERE id = :id", id=ext.id)
    assert raw is not None and raw.startswith(b"gAAAAA")
    assert sentinel.encode() not in raw


async def test_same_plaintext_different_ciphertexts(db_session: AsyncSession) -> None:
    sentinel = steuer_id_sentinel()
    hh = await make_household(db_session)
    p1 = await make_person(db_session, hh, steuer_id=sentinel)
    p2 = await make_person(db_session, hh, steuer_id=sentinel)
    c1 = await _raw(db_session, "SELECT steuer_id FROM person WHERE id = :id", id=p1.id)
    c2 = await _raw(db_session, "SELECT steuer_id FROM person WHERE id = :id", id=p2.id)
    assert c1 != c2


async def test_null_is_sql_null_without_key(db_session: AsyncSession, keys: KeyControl) -> None:
    keys.unset()
    hh = await make_household(db_session)
    person = await make_person(db_session, hh, steuer_id=None)
    db_session.expunge_all()
    assert (
        await _raw(db_session, "SELECT steuer_id FROM person WHERE id = :id", id=person.id) is None
    )
    assert await _read_steuer_id(db_session, person) is None


async def test_missing_key_raises_on_write(db_session: AsyncSession, keys: KeyControl) -> None:
    keys.unset()
    sentinel = steuer_id_sentinel()
    hh = await make_household(db_session)
    with pytest.raises(StatementError) as info:
        async with db_session.begin_nested():
            await make_person(db_session, hh, steuer_id=sentinel)
    error = unwrap(info.value)
    assert isinstance(error, EncryptionKeyError)
    assert str(error) == "FIELD_ENCRYPTION_KEY is not set"
    assert sentinel not in _everything(info.value)


async def test_invalid_key_raises(db_session: AsyncSession, keys: KeyControl) -> None:
    bad_key = text_sentinel("not-a-fernet-key")
    keys.use(bad_key)
    sentinel = steuer_id_sentinel()
    hh = await make_household(db_session)
    with pytest.raises(StatementError) as info:
        async with db_session.begin_nested():
            await make_person(db_session, hh, steuer_id=sentinel)
    error = unwrap(info.value)
    assert isinstance(error, EncryptionKeyError)
    assert str(error) == "FIELD_ENCRYPTION_KEY is not a valid Fernet key"
    text_all = _everything(info.value)
    assert bad_key not in text_all
    assert sentinel not in text_all


async def test_wrong_key_raises_decryption_error(
    db_session: AsyncSession, keys: KeyControl
) -> None:
    k1, k2 = new_key(), new_key()
    keys.use(k1)
    sentinel = steuer_id_sentinel()
    hh = await make_household(db_session)
    person = await make_person(db_session, hh, steuer_id=sentinel)
    ciphertext = await _raw(db_session, "SELECT steuer_id FROM person WHERE id = :id", id=person.id)
    assert ciphertext is not None
    db_session.expunge_all()

    keys.use(k2)
    with pytest.raises(Exception) as info:
        await _read_steuer_id(db_session, person)
    error = unwrap(info.value)
    assert isinstance(error, DecryptionError)
    assert str(error) == "cannot decrypt person.steuer_id"
    assert error.__cause__ is None and error.__suppress_context__
    text_all = _everything(info.value)
    assert sentinel not in text_all
    assert ciphertext.decode() not in text_all
    assert k1 not in text_all and k2 not in text_all


async def test_key_rotation(db_session: AsyncSession, keys: KeyControl) -> None:
    k1, k2 = new_key(), new_key()
    keys.use(k1)
    old = steuer_id_sentinel()
    hh = await make_household(db_session)
    person = await make_person(db_session, hh, steuer_id=old)
    db_session.expunge_all()

    keys.use(k2, k1)  # new primary key, old one still decrypts
    assert await _read_steuer_id(db_session, person) == old

    new = steuer_id_sentinel()
    await db_session.execute(
        Person.__table__.update().where(Person.id == person.id).values(steuer_id=new)
    )
    keys.use(k2)
    assert await _read_steuer_id(db_session, person) == new
    keys.use(k1)
    with pytest.raises(Exception) as info:
        await _read_steuer_id(db_session, person)
    assert isinstance(unwrap(info.value), DecryptionError)


async def test_db_errors_hide_parameters(db_session: AsyncSession) -> None:
    sentinel = steuer_id_sentinel()
    hh = await make_household(db_session)
    with pytest.raises(IntegrityError) as info:
        async with db_session.begin_nested():
            await make_person(db_session, hh, steuer_id=sentinel, disability_grade=15)
    assert isinstance(info.value.orig, pg.CheckViolation)
    message = str(info.value)
    assert HIDDEN in message
    # Postgres' DETAIL may list plain column values, but the Steuer-ID only as ciphertext.
    assert sentinel not in _everything(info.value)
    assert sentinel not in (info.value.orig.diag.message_detail or "")


def test_app_engine_hides_parameters() -> None:
    settings = Settings(database_url="postgresql+psycopg://u:p@localhost:1/x_test")
    engine = create_engine(settings)
    assert engine.sync_engine.hide_parameters is True


def test_settings_key_optional_and_blank_is_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "postgresql+psycopg://u:p@localhost:1/x_test"
    monkeypatch.delenv("FIELD_ENCRYPTION_KEY", raising=False)
    assert Settings(database_url=url).field_encryption_key is None
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", "  ")
    assert Settings(database_url=url).field_encryption_key is None
    key = new_key()
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", key)
    settings = Settings(database_url=url)
    assert settings.field_encryption_key is not None
    assert key not in repr(settings)
