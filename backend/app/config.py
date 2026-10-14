"""Application settings, read from the environment (and an optional `.env`).

Only the settings the skeleton needs live here; later features add their own.
"""

from __future__ import annotations

import os
import re
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
DEFAULT_STORAGE_PATH = Path("data") / "storage"
WEBHOOK_SECRET_RE = re.compile(r"[A-Za-z0-9_-]{16,256}")  # Telegram's limit for secret_token


def normalise_database_url(url: str) -> str:
    """Map plain `postgres://` / `postgresql://` URLs (e.g. Railway) onto the psycopg 3 driver."""
    for scheme in _PLAIN_SCHEMES:
        if url.startswith(scheme):
            return _PSYCOPG_SCHEME + url[len(scheme) :]
    return url


def _default_app_base_url() -> str:
    """The forwarded https URL in Codespaces (so dev-mail links open), else localhost."""
    name = os.environ.get("CODESPACE_NAME")
    domain = os.environ.get("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN")
    if name and domain:
        return f"https://{name}-3000.{domain}"
    return "http://localhost:3000"


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
        default_factory=_default_app_base_url,
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

    # --- storage, upload, queue and worker (#6) ------------------------------------------
    # Root directory for uploaded originals. Unset: `./data/storage` relative to the working
    # directory (backend/ locally). Must be set and absolute with APP_ENV=production.
    storage_path: Path | None = Field(
        default=None, validation_alias=AliasChoices("STORAGE_PATH", "storage_path")
    )
    max_upload_mb: int = Field(
        default=25, gt=0, validation_alias=AliasChoices("MAX_UPLOAD_MB", "max_upload_mb")
    )
    export_zip_max_mb: int = Field(  # #21: cap of the receipts ZIP (sum of the originals' sizes)
        default=500, gt=0, validation_alias=AliasChoices("EXPORT_ZIP_MAX_MB", "export_zip_max_mb")
    )
    worker_poll_interval_seconds: float = Field(
        default=2.0,
        gt=0,
        validation_alias=AliasChoices(
            "WORKER_POLL_INTERVAL_SECONDS", "worker_poll_interval_seconds"
        ),
    )
    worker_concurrency: int = Field(
        default=1,
        ge=1,
        validation_alias=AliasChoices("WORKER_CONCURRENCY", "worker_concurrency"),
    )
    # Must stay below honcho's KILL_WAIT (5 s): honcho SIGKILLs everything after that.
    worker_shutdown_grace_seconds: float = Field(
        default=3.0,
        ge=0,
        validation_alias=AliasChoices(
            "WORKER_SHUTDOWN_GRACE_SECONDS", "worker_shutdown_grace_seconds"
        ),
    )
    job_lease_seconds: float = Field(
        default=60.0, gt=0, validation_alias=AliasChoices("JOB_LEASE_SECONDS", "job_lease_seconds")
    )
    # #9: two LLM router calls per job (2 × LLM_DEADLINE_SECONDS + 120 must fit).
    job_timeout_seconds: float = Field(
        default=900.0,
        gt=0,
        validation_alias=AliasChoices("JOB_TIMEOUT_SECONDS", "job_timeout_seconds"),
    )
    reprocess_bulk_max: int = Field(  # #45: ids per bulk "Neu verarbeiten" request
        default=25,
        ge=1,
        le=200,
        validation_alias=AliasChoices("REPROCESS_BULK_MAX", "reprocess_bulk_max"),
    )
    job_max_attempts: int = Field(
        default=5,
        ge=1,
        le=100,
        validation_alias=AliasChoices("JOB_MAX_ATTEMPTS", "job_max_attempts"),
    )
    job_backoff_base_seconds: float = Field(
        default=30.0,
        ge=0,
        validation_alias=AliasChoices("JOB_BACKOFF_BASE_SECONDS", "job_backoff_base_seconds"),
    )
    job_backoff_max_seconds: float = Field(
        default=1800.0,
        ge=0,
        validation_alias=AliasChoices("JOB_BACKOFF_MAX_SECONDS", "job_backoff_max_seconds"),
    )

    # --- pipeline (#9) -------------------------------------------------------------------
    # Prompt set in app/pipeline/prompts/<version>/; empty = the highest version.
    pipeline_prompt_version: str = Field(
        default="",
        validation_alias=AliasChoices("PIPELINE_PROMPT_VERSION", "pipeline_prompt_version"),
    )
    # PDF pages / TIFF frames per document (never truncated); must be <= LLM_MAX_PDF_PAGES.
    pipeline_max_pages: int = Field(
        default=20,
        ge=1,
        validation_alias=AliasChoices("PIPELINE_MAX_PAGES", "pipeline_max_pages"),
    )
    # Characters of a PDF text layer sent to classify (extract always gets the whole PDF).
    pipeline_classify_max_chars: int = Field(
        default=20_000,
        ge=1,
        validation_alias=AliasChoices("PIPELINE_CLASSIFY_MAX_CHARS", "pipeline_classify_max_chars"),
    )

    # --- bot (#11) -----------------------------------------------------------------------
    # Never echoed, logged or put in a span. `replace-me` (.env.example) counts as unset.
    telegram_bot_token: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("TELEGRAM_BOT_TOKEN", "telegram_bot_token")
    )
    telegram_webhook_secret: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("TELEGRAM_WEBHOOK_SECRET", "telegram_webhook_secret"),
    )
    # Unset: `webhook` in production, else `polling` (`python -m app.bot.poll`).
    bot_mode: Literal["webhook", "polling"] | None = Field(
        default=None, validation_alias=AliasChoices("BOT_MODE", "bot_mode")
    )

    @property
    def resolved_bot_mode(self) -> str:
        if self.bot_mode is not None:
            return self.bot_mode
        return "webhook" if self.is_production else "polling"

    @property
    def resolved_storage_path(self) -> Path:
        """`STORAGE_PATH` as an absolute path (default `./data/storage` in the working dir)."""
        return (self.storage_path or DEFAULT_STORAGE_PATH).absolute()

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def export_zip_max_bytes(self) -> int:
        return self.export_zip_max_mb * 1024 * 1024

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
    def csrf_origins(self) -> tuple[str, ...]:
        """Accepted `Origin`s: `APP_BASE_URL`'s; outside production also localhost (#51)."""
        if self.is_production:
            return (self.app_origin,)
        port = urlsplit(self.app_base_url).port or 3000
        local = tuple(f"http://{h}:{port}" for h in ("localhost", "127.0.0.1"))
        return (self.app_origin, *local)

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

    @field_validator("telegram_bot_token", "telegram_webhook_secret", "bot_mode", mode="before")
    @classmethod
    def _empty_bot_value_is_unset(cls, value: object) -> object:
        if isinstance(value, SecretStr):
            value = value.get_secret_value()
        if isinstance(value, str):
            value = value.strip()
            if not value or value == "replace-me":
                return None
        return value

    @field_validator("telegram_webhook_secret", mode="after")
    @classmethod
    def _valid_webhook_secret(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not WEBHOOK_SECRET_RE.fullmatch(value.get_secret_value()):
            # No value in the message: it would end up in the startup error.
            raise ValueError("must match [A-Za-z0-9_-]{16,256}")
        return value

    @field_validator("storage_path", mode="before")
    @classmethod
    def _empty_storage_path_is_unset(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value.strip() if isinstance(value, str) else value

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


def storage_problems(settings: Settings) -> list[str]:
    """Production rules for `STORAGE_PATH` (api and worker); the path is not secret."""
    if not settings.is_production:
        return []
    if settings.storage_path is None:
        return ["STORAGE_PATH: must be set in production"]
    if not settings.storage_path.is_absolute():
        return ["STORAGE_PATH: must be an absolute path in production"]
    return []


def production_problems(settings: Settings) -> list[str]:
    """What breaks the production rules, as `VARIABLE: reason` (never a value)."""
    if not settings.is_production:
        return []
    problems: list[str] = storage_problems(settings)
    if settings.resolved_mail_backend != "resend":
        problems.append("MAIL_BACKEND: must be resend in production")
    else:
        if settings.resend_api_key is None:
            problems.append("RESEND_API_KEY: required when MAIL_BACKEND=resend")
        if not settings.resend_from_email:
            problems.append("RESEND_FROM_EMAIL: required when MAIL_BACKEND=resend")
    if not settings.secure_cookies:
        problems.append("APP_BASE_URL: must start with https:// in production")
    if settings.resolved_bot_mode == "webhook":
        if settings.telegram_bot_token is None:
            problems.append("TELEGRAM_BOT_TOKEN: required in production webhook mode")
        if settings.telegram_webhook_secret is None:
            problems.append("TELEGRAM_WEBHOOK_SECRET: required in production webhook mode")
    return problems


def check_api_settings(settings: Settings) -> None:
    """Fail api startup when production rules are broken (the CLI does not call this)."""
    problems = production_problems(settings)
    if problems:
        raise SettingsError("invalid settings: " + "; ".join(problems))


def check_worker_settings(settings: Settings) -> None:
    """Fail worker startup when the storage rules are broken."""
    problems = storage_problems(settings)
    if problems:
        raise SettingsError("invalid settings: " + "; ".join(problems))


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
