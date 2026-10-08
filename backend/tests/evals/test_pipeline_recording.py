"""Replay of the real pipeline recording with `--gate` (#9 merge blocker).

Skipped until the user's real run is committed as `recordings/bills_v0/pipeline-openai-<v>/`.
The recording must have been made with the committed prompt set (`prompt_sha256` = LOCK).
"""

from __future__ import annotations

import json

import pytest

from app.pipeline.prompts import available_versions, read_lock
from evals.paths import EVALS_DIR, EvalPaths
from tests.evals.conftest import DATASET
from tests.evals.test_run import invoke

VERSION = available_versions()[-1]
NAME = f"pipeline-openai-{VERSION}"
RECORDING = EVALS_DIR / "recordings" / DATASET / NAME

pytestmark = pytest.mark.skipif(
    not (RECORDING / "meta.json").is_file(),
    reason="eval gate pending: no real pipeline recording yet (see #9 User input needed)",
)


def test_recording_matches_the_prompt_lock() -> None:
    meta = json.loads((RECORDING / "meta.json").read_text(encoding="utf-8"))
    assert meta["predictor"]["prompt_version"] == VERSION
    assert meta["predictor"]["prompt_sha256"] == read_lock()[VERSION], (
        "prompts changed since the recording: run the real eval again"
    )


def test_recording_passes_the_gate(
    eval_paths: EvalPaths, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = invoke(
        eval_paths, capsys, "--predictor", "replay", "--recording", NAME, "--gate"
    )
    assert code == 0, out
