"""Application settings, read from the environment (and an optional `.env`).

Only the settings the skeleton needs live here; later features add their own.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import AliasChoices, Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_PSYCOPG_SCHEME = "postgresql+psycopg://"
_PLAIN_SCHEMES = ("postgres://", "postgresql://")


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


@lru_cache
def get_settings() -> Settings:
    return load_settings()
