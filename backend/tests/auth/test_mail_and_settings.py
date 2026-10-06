"""Mail backends and auth settings (#5). Resend's HTTP API is mocked (`httpx.MockTransport`)."""

from __future__ import annotations

import io
import json
import secrets
import stat
import subprocess
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncConnection

from app.auth.mail import (
    LOGIN_SUBJECT,
    RESEND_URL,
    FileOutboxBackend,
    MailSendError,
    MemoryBackend,
    ResendBackend,
    backend_from_settings,
    login_mail,
)
from app.config import Settings, SettingsError, check_api_settings, load_settings
from tests.auth.conftest import auth_settings, random_email, running_app

REPO_ROOT = Path(__file__).resolve().parents[3]


def _link() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return token, f"https://app.test/login/verify#token={token}"


def test_mail_text() -> None:
    _, link = _link()
    mail = login_mail("someone@example.com", link, 15)
    assert mail.subject == LOGIN_SUBJECT == "Dein Anmeldelink für belegbot"
    for body in (mail.text, mail.html):
        assert "15 Minuten gültig, nur einmal verwendbar" in body
        assert "Öffne den Link am besten direkt im Browser" in body
        assert "Falls du das nicht angefordert hast, ignoriere diese E-Mail." in body
    assert link in mail.text
    assert link in mail.html
    assert "<img" not in mail.html.lower()


async def test_resend_backend_posts_the_mail() -> None:
    key = f"re_test_{secrets.token_hex(8)}"
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": "test-id"})

    token, link = _link()
    to = random_email()
    backend = ResendBackend(key, "belegbot <login@example.com>", httpx.MockTransport(handler))
    await backend.send(login_mail(to, link, 15))

    assert len(seen) == 1
    request = seen[0]
    assert request.method == "POST"
    assert str(request.url) == RESEND_URL == "https://api.resend.com/emails"
    assert request.headers["authorization"] == f"Bearer {key}"
    body = json.loads(request.content)
    assert set(body) == {"from", "to", "subject", "text", "html"}
    assert body["from"] == "belegbot <login@example.com>"
    assert body["to"] == [to]
    assert body["subject"] == LOGIN_SUBJECT
    assert link in body["text"] and link in body["html"]
    assert key not in repr(backend)


async def test_resend_error_message_has_status_only() -> None:
    key = f"re_test_{secrets.token_hex(8)}"
    to = random_email()
    token, link = _link()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"message": f"invalid recipient {to}"})

    backend = ResendBackend(key, "belegbot <login@example.com>", httpx.MockTransport(handler))
    with pytest.raises(MailSendError) as caught:
        await backend.send(login_mail(to, link, 15))
    message = str(caught.value)
    assert "422" in message
    for secret in (key, to, token):
        assert secret not in message
        assert secret not in repr(caught.value)


async def test_file_outbox_writes_0600_and_logs_no_token(
    tmp_path: Path, json_log: io.StringIO, caplog: pytest.LogCaptureFixture
) -> None:
    token, link = _link()
    outbox = tmp_path / "dev-mail"
    backend = FileOutboxBackend(outbox)
    await backend.send(login_mail(random_email(), link, 15))

    files = list(outbox.iterdir())
    assert len(files) == 1
    assert stat.S_IMODE(files[0].stat().st_mode) == 0o600
    assert link in files[0].read_text()  # the dev outbox is the one place the link goes
    logged = json_log.getvalue() + caplog.text
    assert "mail written to dev outbox" in logged
    assert token not in logged


def test_dev_mail_dir_is_git_ignored() -> None:
    result = subprocess.run(
        ["git", "check-ignore", "backend/.dev-mail/x"], cwd=REPO_ROOT, capture_output=True
    )
    assert result.returncode == 0


def test_backend_selection(migrated_database: str) -> None:
    assert isinstance(backend_from_settings(auth_settings(migrated_database)), MemoryBackend)
    dev = auth_settings(migrated_database, mail_backend=None)
    assert isinstance(backend_from_settings(dev), FileOutboxBackend)
    assert str(dev.dev_mail_dir).endswith("backend/.dev-mail")
    empty = auth_settings(migrated_database, dev_mail_dir="  ")
    assert empty.dev_mail_dir == dev.dev_mail_dir
    prod = auth_settings(
        migrated_database,
        app_env="production",
        mail_backend=None,
        resend_api_key="re_test_placeholder",
        resend_from_email="belegbot <login@example.com>",
    )
    assert isinstance(backend_from_settings(prod), ResendBackend)


# --- production settings --------------------------------------------------------------

SENTINEL_HOST = f"sentinel-{secrets.token_hex(4)}.example"


@pytest.fixture
def prod_env(monkeypatch: pytest.MonkeyPatch, migrated_database: str) -> pytest.MonkeyPatch:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    for name in ("MAIL_BACKEND", "RESEND_API_KEY", "RESEND_FROM_EMAIL", "APP_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DATABASE_URL", migrated_database)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("MAIL_BACKEND", "resend")
    monkeypatch.setenv("RESEND_API_KEY", f"re_{SENTINEL_HOST}")
    monkeypatch.setenv("RESEND_FROM_EMAIL", f"belegbot <login@{SENTINEL_HOST}>")
    monkeypatch.setenv("APP_BASE_URL", f"https://{SENTINEL_HOST}")
    return monkeypatch


def _startup_error() -> str:
    with pytest.raises(SettingsError) as caught:
        check_api_settings(load_settings())
    return str(caught.value)


def test_valid_production_settings_pass(prod_env: pytest.MonkeyPatch) -> None:
    check_api_settings(load_settings())


@pytest.mark.parametrize("backend", ["file", "memory"])
def test_production_rejects_non_resend_backends(prod_env: pytest.MonkeyPatch, backend: str) -> None:
    prod_env.setenv("MAIL_BACKEND", backend)
    message = _startup_error()
    assert "MAIL_BACKEND" in message
    assert SENTINEL_HOST not in message


@pytest.mark.parametrize("missing", ["RESEND_API_KEY", "RESEND_FROM_EMAIL"])
def test_production_needs_resend_settings(prod_env: pytest.MonkeyPatch, missing: str) -> None:
    prod_env.delenv(missing)
    message = _startup_error()
    assert missing in message
    assert SENTINEL_HOST not in message


def test_production_needs_https_base_url(prod_env: pytest.MonkeyPatch) -> None:
    prod_env.setenv("APP_BASE_URL", f"http://{SENTINEL_HOST}")
    message = _startup_error()
    assert "APP_BASE_URL" in message
    assert SENTINEL_HOST not in message


def test_invalid_base_url_names_variable_only(prod_env: pytest.MonkeyPatch) -> None:
    prod_env.setenv("APP_BASE_URL", f"ftp:/{SENTINEL_HOST}")
    with pytest.raises(SettingsError) as caught:
        load_settings()
    assert "APP_BASE_URL" in str(caught.value)
    assert SENTINEL_HOST not in str(caught.value)


async def test_api_startup_fails_in_production_with_file_backend(
    prod_env: pytest.MonkeyPatch, migrated_database: str, db_connection: AsyncConnection
) -> None:
    settings = auth_settings(
        migrated_database,
        app_env="production",
        mail_backend="file",
        app_base_url=f"https://{SENTINEL_HOST}",
    )
    with pytest.raises(SettingsError) as caught:
        async with running_app(settings, conn=db_connection):
            pass  # pragma: no cover
    assert "MAIL_BACKEND" in str(caught.value)
    assert SENTINEL_HOST not in str(caught.value)
