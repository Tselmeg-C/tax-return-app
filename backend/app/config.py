"""Application settings, read from the environment (and an optional `.env`).

Only the settings the skeleton needs live here; later features add their own.
"""

from __future__ import annotations

import os
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


@lru_cache
def get_settings() -> Settings:
    return load_settings()


def _env_alias(name: str) -> AliasChoices:
    return AliasChoices(name, name.lower())


class LLMSettings(BaseSettings):
    """Settings of the LLM layer (`app/llm/`). Nothing here is required at startup.

    Kept apart from `Settings` so the LLM layer (smoke CLI, evals) works without
    `DATABASE_URL`. A missing `OPENAI_API_KEY` only fails the first real OpenAI call.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    app_env: str = Field(default="development", validation_alias=_env_alias("APP_ENV"))
    openai_api_key: SecretStr | None = Field(
        default=None, validation_alias=_env_alias("OPENAI_API_KEY")
    )
    openai_base_url: str = Field(
        default="https://api.openai.com/v1", validation_alias=_env_alias("OPENAI_BASE_URL")
    )
    # Per-task model overrides / fallback override ("provider:model"; empty = routing.yaml).
    llm_classify_model: str = Field(default="", validation_alias=_env_alias("LLM_CLASSIFY_MODEL"))
    llm_extract_model: str = Field(default="", validation_alias=_env_alias("LLM_EXTRACT_MODEL"))
    llm_fallback_model: str = Field(default="", validation_alias=_env_alias("LLM_FALLBACK_MODEL"))
    llm_max_attempts: int = Field(default=3, ge=1, validation_alias=_env_alias("LLM_MAX_ATTEMPTS"))
    llm_backoff_base_seconds: float = Field(
        default=1.0, ge=0, validation_alias=_env_alias("LLM_BACKOFF_BASE_SECONDS")
    )
    llm_backoff_max_seconds: float = Field(
        default=20.0, ge=0, validation_alias=_env_alias("LLM_BACKOFF_MAX_SECONDS")
    )
    llm_retry_after_max_seconds: float = Field(
        default=30.0, ge=0, validation_alias=_env_alias("LLM_RETRY_AFTER_MAX_SECONDS")
    )
    llm_deadline_seconds: float = Field(
        default=300.0, gt=0, validation_alias=_env_alias("LLM_DEADLINE_SECONDS")
    )
    llm_max_image_px: int = Field(
        default=2048, ge=1, validation_alias=_env_alias("LLM_MAX_IMAGE_PX")
    )
    llm_max_image_pixels: int = Field(
        default=50_000_000, ge=1, validation_alias=_env_alias("LLM_MAX_IMAGE_PIXELS")
    )
    llm_max_image_bytes: int = Field(
        default=8_000_000, ge=1, validation_alias=_env_alias("LLM_MAX_IMAGE_BYTES")
    )
    llm_max_pdf_pages: int = Field(
        default=20, ge=1, validation_alias=_env_alias("LLM_MAX_PDF_PAGES")
    )
    llm_max_images: int = Field(default=20, ge=0, validation_alias=_env_alias("LLM_MAX_IMAGES"))
    llm_max_request_mb: float = Field(
        default=20.0, gt=0, validation_alias=_env_alias("LLM_MAX_REQUEST_MB")
    )

    @field_validator("openai_api_key", mode="before")
    @classmethod
    def _empty_key_is_unset(cls, value: object) -> object:
        if isinstance(value, SecretStr):
            value = value.get_secret_value()
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("llm_classify_model", "llm_extract_model", "llm_fallback_model", mode="after")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


def load_llm_settings() -> LLMSettings:
    """Build `LLMSettings`; like `load_settings`, errors name the variables, never values."""
    try:
        return LLMSettings()
    except ValidationError as exc:
        problems = sorted(
            {
                f"{'.'.join(str(part) for part in err['loc']).upper()}: {err['msg']}"
                for err in exc.errors()
            }
        )
        raise SettingsError("invalid LLM settings: " + "; ".join(problems)) from None


@lru_cache
def get_llm_settings() -> LLMSettings:
    return load_llm_settings()
