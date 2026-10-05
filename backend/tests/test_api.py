import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest

from app.api.main import create_app
from app.config import Settings

DOWN_PASSWORD = "wrong-pw"
DOWN_URL = f"postgresql+psycopg://belegbot:{DOWN_PASSWORD}@127.0.0.1:1/belegbot"


@asynccontextmanager
async def client_for(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


def _settings(database_url: str, **overrides: str) -> Settings:
    return Settings(_env_file=None, database_url=database_url, **overrides)  # type: ignore[call-arg]


async def test_health_db_up(migrated_database: str) -> None:
    async with client_for(_settings(migrated_database)) as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ok"}


async def test_health_db_down_returns_503_without_credentials(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    async with client_for(_settings(DOWN_URL)) as client:
        started = time.monotonic()
        response = await client.get("/health")
        elapsed = time.monotonic() - started
        version = await client.get("/version")
    assert response.status_code == 503
    assert response.json() == {"status": "error", "db": "error"}
    assert elapsed < 3
    assert DOWN_PASSWORD not in response.text
    assert DOWN_URL not in response.text
    assert DOWN_PASSWORD not in caplog.text
    assert "127.0.0.1:1" not in caplog.text
    assert "health: db check failed" in caplog.text
    assert version.status_code == 200


async def test_version_without_git_sha(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GIT_SHA", raising=False)
    monkeypatch.delenv("RAILWAY_GIT_COMMIT_SHA", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    async with client_for(_settings(DOWN_URL)) as client:
        response = await client.get("/version")
    assert response.status_code == 200
    assert response.json() == {"version": "0.1.0", "commit": "unknown", "env": "development"}


async def test_version_with_git_sha(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_SHA", "abc123")
    monkeypatch.setenv("APP_ENV", "production")
    # Production refuses to start without these (#5); test-only placeholder values.
    monkeypatch.setenv("MAIL_BACKEND", "resend")
    monkeypatch.setenv("RESEND_API_KEY", "re_test_placeholder")
    monkeypatch.setenv("RESEND_FROM_EMAIL", "belegbot <login@example.com>")
    monkeypatch.setenv("APP_BASE_URL", "https://app.test")
    async with client_for(_settings(DOWN_URL)) as client:
        response = await client.get("/version")
    assert response.status_code == 200
    assert response.json() == {"version": "0.1.0", "commit": "abc123", "env": "production"}
