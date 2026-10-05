import pytest

from app.config import Settings, SettingsError, load_settings

PASSWORD = "s3cr3t-pw"
PSYCOPG_URL = f"postgresql+psycopg://belegbot:{PASSWORD}@db:5432/belegbot"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("DATABASE_URL", "APP_ENV", "LOG_LEVEL", "GIT_SHA", "RAILWAY_GIT_COMMIT_SHA"):
        monkeypatch.delenv(name, raising=False)
    # Ignore any local .env so tests only see what they set.
    monkeypatch.setitem(Settings.model_config, "env_file", None)


@pytest.mark.parametrize(
    "url",
    [
        PSYCOPG_URL,
        f"postgresql://belegbot:{PASSWORD}@db:5432/belegbot",
        f"postgres://belegbot:{PASSWORD}@db:5432/belegbot",
    ],
)
def test_database_url_is_normalised_to_psycopg(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    monkeypatch.setenv("DATABASE_URL", url)
    assert Settings().database_url.get_secret_value() == PSYCOPG_URL


def test_database_url_normalised_when_passed_directly() -> None:
    settings = Settings(database_url=f"postgres://belegbot:{PASSWORD}@db:5432/belegbot")
    assert settings.database_url.get_secret_value() == PSYCOPG_URL


def test_repr_and_str_do_not_contain_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", PSYCOPG_URL)
    settings = Settings()
    assert PASSWORD not in repr(settings)
    assert PASSWORD not in str(settings)
    assert PASSWORD not in repr(settings.model_dump())


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", PSYCOPG_URL)
    settings = Settings()
    assert settings.app_env == "development"
    assert settings.log_level == "INFO"
    assert settings.git_sha == "unknown"


def test_git_sha_prefers_git_sha_then_railway(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", PSYCOPG_URL)
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "railway123")
    assert Settings().git_sha == "railway123"
    monkeypatch.setenv("GIT_SHA", "abc123")
    assert Settings().git_sha == "abc123"


def test_missing_database_url_names_the_variable() -> None:
    with pytest.raises(SettingsError, match="DATABASE_URL"):
        load_settings()


def test_empty_git_sha_falls_back_to_railway(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", PSYCOPG_URL)
    monkeypatch.setenv("GIT_SHA", "")
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "railway123")
    assert Settings().git_sha == "railway123"
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "")
    assert Settings().git_sha == "unknown"
