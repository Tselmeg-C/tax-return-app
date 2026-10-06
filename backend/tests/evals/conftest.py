"""Eval harness tests: no DB, no network (outgoing sockets raise)."""

from __future__ import annotations

import shutil
import socket
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from evals.paths import EVALS_DIR, EvalPaths

DATASET = "bills_v0"


class NetworkBlocked(RuntimeError):
    pass


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    def guard(*_: Any, **__: Any) -> None:
        raise NetworkBlocked("network access is not allowed in eval tests")

    monkeypatch.setattr(socket.socket, "connect", guard)
    monkeypatch.setattr(socket.socket, "connect_ex", guard)
    monkeypatch.setattr(socket, "create_connection", guard)
    yield


@pytest.fixture
def eval_paths(tmp_path: Path) -> EvalPaths:
    """A private copy of the committed dataset, recordings, baselines and thresholds."""
    root = tmp_path / "evals"
    shutil.copytree(EVALS_DIR / "datasets" / DATASET, root / "datasets" / DATASET)
    shutil.copytree(EVALS_DIR / "recordings" / DATASET, root / "recordings" / DATASET)
    shutil.copytree(EVALS_DIR / "baselines" / DATASET, root / "baselines" / DATASET)
    shutil.copy(EVALS_DIR / "thresholds.yaml", root / "thresholds.yaml")
    return EvalPaths(
        datasets=root / "datasets",
        datasets_private=root / "datasets_private",
        recordings=root / "recordings",
        baselines=root / "baselines",
        thresholds=root / "thresholds.yaml",
        reports=root / "reports",
        specs=EVALS_DIR / "synth" / "specs",
    )
