"""Application settings, read from the environment (and an optional `.env`).

Only the settings the skeleton needs live here; later features add their own.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import AliasChoices, Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_PSYCOPG_SCHEME = "postgresql+psycopg://"
_PLAIN_SCHEMES = ("postgres://", "postgresql://")
# `backend/.dev-mail/` (git-ignored): the `file` mail backend's outbox.
DEFAULT_DEV_MAIL_DIR = Path(__file__).resolve().parent.parent / ".dev-mail"


def normalise_database_url(url: str) -> str:
    """Map plain `postgres://` / `postgresql://` URLs (e.g. Railway) onto the psycopg 3 driver."""
    for scheme in _PLAIN_SCHEMES:
        if url.startswith(scheme):
            return _PSYCOPG_SCHEME + url[len(scheme) :]
    return url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    database_url: SecretStr = Field(validation_alias=AliasChoices("DATABASE_URL", "database_url"))
    app_env: str = Field(default="development", validation_alias=AliasChoices("APP_ENV", "app_env"))
    log_level: str = Field(default="INFO", validation_alias=AliasChoices("LOG_LEVEL", "log_level"))
    git_sha: str = Field(
        default="unknown",
        validation_alias=AliasChoices("GIT_SHA", "RAILWAY_GIT_COMMIT_SHA", "git_sha"),
    )
    # Comma-separated Fernet keys: the first encrypts, all decrypt (rotation). Optional, so
    # the app and Alembic start without it; app.db.crypto raises on first use instead.
    field_encryption_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("FIELD_ENCRYPTION_KEY", "field_encryption_key"),
    )

    # --- auth (#5) ---------------------------------------------------------------------
    # Public origin of the web app: magic links, the CSRF Origin check, the cookie name
    # (`__Host-` prefix + Secure with https).
    app_base_url: str = Field(
        default="http://localhost:3000",
        validation_alias=AliasChoices("APP_BASE_URL", "app_base_url"),
    )
    # resend | file | memory. Unset: `resend` in production, else `file`.
    mail_backend: Literal["resend", "file", "memory"] | None = Field(
        default=None, validation_alias=AliasChoices("MAIL_BACKEND", "mail_backend")
    )
    dev_mail_dir: Path = Field(
        default=DEFAULT_DEV_MAIL_DIR, validation_alias=AliasChoices("DEV_MAIL_DIR", "dev_mail_dir")
    )
    resend_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("RESEND_API_KEY", "resend_api_key")
    )
    resend_from_email: str | None = Field(
        default=None, validation_alias=AliasChoices("RESEND_FROM_EMAIL", "resend_from_email")
    )
    magic_link_ttl_minutes: int = Field(
        default=15,
        gt=0,
        validation_alias=AliasChoices("MAGIC_LINK_TTL_MINUTES", "magic_link_ttl_minutes"),
    )
    session_idle_days: int = Field(
        default=7, gt=0, validation_alias=AliasChoices("SESSION_IDLE_DAYS", "session_idle_days")
    )
    session_max_days: int = Field(
        default=30, gt=0, validation_alias=AliasChoices("SESSION_MAX_DAYS", "session_max_days")
    )

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def resolved_mail_backend(self) -> str:
        if self.mail_backend is not None:
            return self.mail_backend
        return "resend" if self.is_production else "file"

    @property
    def app_origin(self) -> str:
        """`scheme://host[:port]` of `APP_BASE_URL` (what browsers send as `Origin`)."""
        parts = urlsplit(self.app_base_url)
        return f"{parts.scheme}://{parts.netloc}".lower()

    @property
    def secure_cookies(self) -> bool:
        return self.app_base_url.lower().startswith("https://")

    @field_validator("resend_api_key", mode="before")
    @classmethod
    def _empty_resend_key_is_unset(cls, value: object) -> object:
        if isinstance(value, SecretStr):
            value = value.get_secret_value()
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("resend_from_email", "mail_backend", mode="before")
    @classmethod
    def _empty_is_unset(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value.strip() if isinstance(value, str) else value

    @field_validator("dev_mail_dir", mode="before")
    @classmethod
    def _empty_dir_is_default(cls, value: object) -> object:
        if value is None or (isinstance(value, str) and not value.strip()):
            return DEFAULT_DEV_MAIL_DIR
        return value

    @field_validator("app_base_url", mode="after")
    @classmethod
    def _valid_base_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            # No value in the message: it would end up in the startup error.
            raise ValueError("must be an absolute http(s) URL")
        return value

    @field_validator("database_url", mode="before")
    @classmethod
    def _normalise_database_url(cls, value: object) -> object:
        if isinstance(value, SecretStr):
            value = value.get_secret_value()
        if isinstance(value, str):
            return SecretStr(normalise_database_url(value))
        return value

    @field_validator("field_encryption_key", mode="before")
    @classmethod
    def _empty_key_is_unset(cls, value: object) -> object:
        if isinstance(value, SecretStr):
            value = value.get_secret_value()
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("git_sha", mode="after")
    @classmethod
    def _default_git_sha(cls, value: str) -> str:
        # An empty GIT_SHA counts as unset: fall back to Railway's commit SHA.
        value = value.strip()
        if not value:
            value = os.environ.get("RAILWAY_GIT_COMMIT_SHA", "").strip()
        return value or "unknown"


class SettingsError(RuntimeError):
    """Settings could not be loaded; the message names the variables, never their values."""


def load_settings() -> Settings:
    """Build `Settings`, turning validation errors into a message that names the env vars only.

    Pydantic's own message can echo the input value (e.g. a malformed URL incl. password),
    so it is not re-raised.
    """
    try:
        return Settings()  # values come from the environment
    except ValidationError as exc:
        problems = sorted(
            {
                f"{'.'.join(str(part) for part in err['loc']).upper()}: {err['msg']}"
                for err in exc.errors()
            }
        )
        raise SettingsError("invalid settings: " + "; ".join(problems)) from None


def production_problems(settings: Settings) -> list[str]:
    """What breaks the production rules, as `VARIABLE: reason` (never a value)."""
    if not settings.is_production:
        return []
    problems: list[str] = []
    if settings.resolved_mail_backend != "resend":
        problems.append("MAIL_BACKEND: must be resend in production")
    else:
        if settings.resend_api_key is None:
            problems.append("RESEND_API_KEY: required when MAIL_BACKEND=resend")
        if not settings.resend_from_email:
            problems.append("RESEND_FROM_EMAIL: required when MAIL_BACKEND=resend")
    if not settings.secure_cookies:
        problems.append("APP_BASE_URL: must start with https:// in production")
    return problems


def check_api_settings(settings: Settings) -> None:
    """Fail api startup when production rules are broken (the CLI does not call this)."""
    problems = production_problems(settings)
    if problems:
        raise SettingsError("invalid settings: " + "; ".join(problems))


@lru_cache
def get_settings() -> Settings:
    return load_settings()
