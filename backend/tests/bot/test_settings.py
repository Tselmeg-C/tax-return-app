"""Bot settings (#11): webhook secret charset, production webhook rules, `replace-me`."""

from __future__ import annotations

import pytest

from app.config import (
    Settings,
    SettingsError,
    check_api_settings,
    load_settings,
    production_problems,
)


def make(**kw: object) -> Settings:
    return Settings(_env_file=None, database_url="postgresql://u:p@h/d", **kw)  # type: ignore[call-arg]


def test_placeholder_and_empty_count_as_unset() -> None:
    s = make(telegram_bot_token="replace-me", telegram_webhook_secret="", bot_mode="")
    assert s.telegram_bot_token is None and s.telegram_webhook_secret is None
    assert s.resolved_bot_mode == "polling"


@pytest.mark.parametrize(
    "secret", ["short", "x" * 257, "has space 0123456789", "ümlaut0123456789ab"]
)
def test_secret_charset_is_telegrams(secret: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", secret)
    with pytest.raises(SettingsError) as caught:
        load_settings()
    assert "TELEGRAM_WEBHOOK_SECRET" in str(caught.value) and secret not in str(caught.value)


def test_production_webhook_needs_token_and_secret() -> None:
    base = {
        "app_env": "production",
        "app_base_url": "https://app.example.org",
        "mail_backend": "resend",
        "resend_api_key": "re_test_placeholder",
        "resend_from_email": "belegbot <login@example.com>",
        "storage_path": "/tmp/x",
    }
    problems = production_problems(make(**base))
    assert "TELEGRAM_BOT_TOKEN: required in production webhook mode" in problems
    assert "TELEGRAM_WEBHOOK_SECRET: required in production webhook mode" in problems
    with pytest.raises(SettingsError):
        check_api_settings(make(**base))
    ok = make(**base, telegram_bot_token="1:abc", telegram_webhook_secret="s" * 24)
    assert production_problems(ok) == []
    polling = make(**base, bot_mode="polling")
    assert not [p for p in production_problems(polling) if "TELEGRAM" in p]
