"""Recordings: `evals/recordings/<dataset>/<name>/{meta.json, predictions.jsonl}`.

A recording holds `Prediction` fields only (codes, amounts, dates, vendor / person hint
copied from a synthetic document, usage). Never raw LLM output, prompts or document text.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from evals.schema import Prediction

RECORDING_NAME_RE = r"^[a-z0-9][a-z0-9._-]{0,63}$"


class RecordingError(Exception):
    """Safe-to-print message (no recorded values)."""


@dataclass
class Recording:
    name: str
    meta: dict[str, Any]
    predictions: dict[str, Prediction]


def recording_dir(recordings_root: Path, dataset: str, name: str) -> Path:
    return recordings_root / dataset / name


def write_recording(target: Path, meta: dict[str, Any], predictions: dict[str, Prediction]) -> None:
    target.mkdir(parents=True, exist_ok=True)
    (target / "meta.json").write_text(
        json.dumps(meta, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    lines = []
    for case_id in sorted(predictions):
        pred = predictions[case_id].model_dump(mode="json")
        lines.append(json.dumps({"case_id": case_id, "prediction": pred}, sort_keys=True))
    (target / "predictions.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_recording(path: Path) -> Recording:
    meta_path = path / "meta.json"
    pred_path = path / "predictions.jsonl"
    if not meta_path.is_file() or not pred_path.is_file():
        raise RecordingError(
            f"recording {path.name!r} not found (needs meta.json + predictions.jsonl)"
        )
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise RecordingError(f"recording {path.name!r}: meta.json is not valid JSON") from None
    predictions: dict[str, Prediction] = {}
    for n, line in enumerate(pred_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            predictions[str(row["case_id"])] = Prediction.model_validate(row["prediction"])
        except (json.JSONDecodeError, KeyError, TypeError, ValidationError) as exc:
            raise RecordingError(
                f"recording {path.name!r}: line {n} invalid ({type(exc).__name__})"
            ) from None
    return Recording(path.name, meta, predictions)
