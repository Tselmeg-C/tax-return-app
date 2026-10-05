"""Dev seed (`python -m app.db.seed`), run against the test DB inside the per-test rollback."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import seed as seed_module
from app.db.seed import DEFAULT_OWNER_EMAIL, OWNER, seed

EXPECTED = {"household": 1, "app_user": 2, "person": 3, "document": 9, "tax_item": 9}


async def _counts(session: AsyncSession) -> dict[str, int]:
    counts = {}
    for table in [*EXPECTED, "audit_log", "extraction"]:
        result = await session.execute(text(f"SELECT count(*) FROM {table}"))
        counts[table] = int(result.scalar_one())
    return counts


async def test_seed_counts_and_idempotency(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await seed(db_session) == EXPECTED
    counts = await _counts(db_session)
    assert counts == {**EXPECTED, "audit_log": 0, "extraction": 0}

    irrelevant = await db_session.execute(
        text("SELECT count(*) FROM tax_item WHERE NOT is_relevant")
    )
    assert irrelevant.scalar_one() == 1
    overridden = await db_session.execute(
        text("SELECT count(*) FROM tax_item WHERE overridden_by_user")
    )
    assert overridden.scalar_one() == 1
    household_level = await db_session.execute(
        text("SELECT count(*) FROM tax_item WHERE person_id IS NULL")
    )
    assert household_level.scalar_one() >= 1
    no_steuer_id = await db_session.execute(
        text("SELECT count(*) FROM person WHERE steuer_id IS NOT NULL")
    )
    assert no_steuer_id.scalar_one() == 0

    assert await seed(db_session) == {}  # second run: nothing to do
    assert await _counts(db_session) == counts


async def test_seed_owner_email_override(db_session: AsyncSession) -> None:
    await seed(db_session, owner_email="Me@Example.org")
    result = await db_session.execute(
        text("SELECT email FROM app_user WHERE id = :id"), {"id": OWNER}
    )
    assert result.scalar_one() == "me@example.org"


def test_owner_email_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SEED_OWNER_EMAIL", raising=False)
    assert seed_module.owner_email_from_env() == DEFAULT_OWNER_EMAIL
    monkeypatch.setenv("SEED_OWNER_EMAIL", "me@example.org")
    assert seed_module.owner_email_from_env() == "me@example.org"


def test_refuses_production(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def no_engine(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("must not connect in production")

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setattr(seed_module, "create_engine", no_engine)
    get_settings.cache_clear()
    try:
        assert seed_module.main() != 0
    finally:
        get_settings.cache_clear()
    captured = capsys.readouterr()
    assert "production" in captured.err
    assert captured.out == ""
