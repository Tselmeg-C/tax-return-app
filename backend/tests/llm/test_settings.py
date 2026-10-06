"""LLM settings: nothing required at startup; the key never shows up in reprs or errors."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from app.config import LLMSettings, SettingsError, load_llm_settings

from .helpers import llm_settings

_FORBIDDEN_IMPORT = re.compile(
    r"^\s*(from|import)\s+(app\.queue|evals|app\.db|sqlalchemy)\b", re.MULTILINE
)
BACKEND_DIR = Path(__file__).resolve().parents[2]


def test_defaults_without_env() -> None:
    settings = llm_settings()
    assert settings.openai_api_key is None
    assert settings.openai_base_url == "https://api.openai.com/v1"
    assert settings.llm_max_attempts == 3
    assert settings.llm_deadline_seconds == 300
    assert settings.llm_max_pdf_pages == 20
    assert settings.llm_classify_model == settings.llm_fallback_model == ""


def test_empty_key_is_unset_and_key_is_hidden(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(LLMSettings.model_config, "env_file", None)
    monkeypatch.setenv("OPENAI_API_KEY", "  ")
    assert load_llm_settings().openai_api_key is None
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-hidden-runtime-value")
    settings = load_llm_settings()
    assert settings.openai_api_key is not None
    assert "sk-test-hidden-runtime-value" not in repr(settings)
    assert "sk-test-hidden-runtime-value" not in str(settings.model_dump())


def test_invalid_values_name_the_variable_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(LLMSettings.model_config, "env_file", None)
    monkeypatch.setenv("LLM_MAX_ATTEMPTS", "zero-sentinel")
    with pytest.raises(SettingsError) as info:
        load_llm_settings()
    assert "LLM_MAX_ATTEMPTS" in str(info.value)
    assert "zero-sentinel" not in str(info.value)


@pytest.mark.parametrize("key", [None, ""])
def test_app_and_worker_import_without_key(key: str | None) -> None:
    env = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
    if key is not None:
        env["OPENAI_API_KEY"] = key
    completed = subprocess.run(
        [sys.executable, "-c", "import app.api.main, app.worker, app.llm"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]


def test_smoke_without_key_prints_skipped() -> None:
    env = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
    env["OPENAI_API_KEY"] = ""
    completed = subprocess.run(
        [sys.executable, "-m", "app.llm.smoke"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0
    assert completed.stdout == "SKIPPED: OPENAI_API_KEY is not set\n"


def test_llm_package_has_no_forbidden_imports() -> None:
    for path in (BACKEND_DIR / "app" / "llm").rglob("*.py"):
        source = path.read_text("utf-8")
        assert not _FORBIDDEN_IMPORT.search(source), f"{path.name} has a forbidden import"
        assert "max_retries=0" in source or "AsyncOpenAI(" not in source
