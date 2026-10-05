"""Default locations of everything the eval harness reads and writes (overridable in tests)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

EVALS_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class EvalPaths:
    datasets: Path = field(default=EVALS_DIR / "datasets")
    datasets_private: Path = field(default=EVALS_DIR / "datasets_private")
    recordings: Path = field(default=EVALS_DIR / "recordings")
    baselines: Path = field(default=EVALS_DIR / "baselines")
    thresholds: Path = field(default=EVALS_DIR / "thresholds.yaml")
    reports: Path = field(default=EVALS_DIR / "reports")
    specs: Path = field(default=EVALS_DIR / "synth" / "specs")
