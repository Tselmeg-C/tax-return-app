"""Fixtures for the domain model tests (#4).

- `keys`: every test runs with a fresh throwaway Fernet key generated at runtime (never
  committed); `keys.use(...)` switches keys, `keys.unset()` removes it. A local `.env` and
  any `FIELD_ENCRYPTION_KEY` from the environment are ignored.
- Sentinels (Steuer-ID-like numbers, strings) are generated at runtime as well.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterator

import pytest
from cryptography.fernet import Fernet

from app.config import Settings, get_settings


def new_key() -> str:
    return Fernet.generate_key().decode()


def steuer_id_sentinel() -> str:
    """11 random digits, generated per test run (never a real or committed number)."""
    return str(secrets.randbelow(9 * 10**10) + 10**10)


def text_sentinel(prefix: str = "sentinel") -> str:
    return f"{prefix}-{secrets.token_hex(8)}"


class KeyControl:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._mp = monkeypatch

    def use(self, *keys: str) -> None:
        self._mp.setenv("FIELD_ENCRYPTION_KEY", ",".join(keys))
        get_settings.cache_clear()

    def unset(self) -> None:
        self._mp.delenv("FIELD_ENCRYPTION_KEY", raising=False)
        get_settings.cache_clear()


@pytest.fixture(autouse=True)
def keys(monkeypatch: pytest.MonkeyPatch) -> Iterator[KeyControl]:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    control = KeyControl(monkeypatch)
    control.use(new_key())
    yield control
    monkeypatch.undo()
    get_settings.cache_clear()
