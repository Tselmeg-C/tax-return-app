"""Baselines, regression rule, stale baselines and threshold handling."""

from __future__ import annotations

import json

import pytest
import yaml

from evals import run
from evals.paths import EvalPaths
from tests.evals.conftest import DATASET
from tests.evals.test_run import invoke, last_report


def _set_thresholds(paths: EvalPaths, **changes: object) -> None:
    data = yaml.safe_load(paths.thresholds.read_text())
    entry = data[DATASET]
    for key, value in changes.items():
        if key == "tolerance":
            entry["regression"]["tolerance"] = value
        elif key == "cost_required":
            entry["thresholds"]["cost_eur_per_doc_mean"]["required"] = value
        else:
            entry[key] = value
    paths.thresholds.write_text(yaml.safe_dump(data, sort_keys=False))


def test_save_baseline_refuses_overwrite_and_subsets(
    eval_paths: EvalPaths, capsys: pytest.CaptureFixture[str]
) -> None:
    target = eval_paths.baselines / DATASET
    (target / "heuristic.json").unlink()
    (target / "heuristic.md").unlink()
    code, _, _ = invoke(
        eval_paths, capsys, "--predictor", "heuristic", "--save-baseline", "heuristic"
    )
    assert code == 0
    assert (target / "heuristic.json").is_file() and (target / "heuristic.md").is_file()
    saved = json.loads((target / "heuristic.json").read_text())
    assert saved["dataset"]["hash"] == last_report(eval_paths)["dataset"]["hash"]

    code, _, err = invoke(
        eval_paths, capsys, "--predictor", "heuristic", "--save-baseline", "heuristic"
    )
    assert code == 2 and "exists" in err
    code, _, err = invoke(
        eval_paths,
        capsys,
        "--predictor",
        "heuristic",
        "--save-baseline",
        "h2",
        "--tags",
        "cash_35a",
    )
    assert code == 2 and "full dataset" in err


def test_compare_to_heuristic_shows_positive_delta_and_passes(
    eval_paths: EvalPaths, capsys: pytest.CaptureFixture[str]
) -> None:
    _set_thresholds(eval_paths, compare_to="heuristic")
    code, out, _ = invoke(eval_paths, capsys, "--predictor", "oracle", "--gate")
    assert code == 0
    assert "baseline: heuristic" in out
    row = next(ln for ln in out.splitlines() if ln.startswith("| Category accuracy |"))
    cells = [c.strip() for c in row.strip("|").split("|")]
    assert cells[3] != "" and cells[4].startswith("+") and cells[5] == "pass"


def test_regression_against_better_baseline_fails_unless_tolerated(
    eval_paths: EvalPaths, capsys: pytest.CaptureFixture[str]
) -> None:
    # Candidate: replay of fixture-noisy (category_accuracy 0.9286). Baseline: the same run
    # with category_accuracy raised to 0.97 (a 0.0414 gap); everything else equal.
    code, _, _ = invoke(
        eval_paths,
        capsys,
        "--predictor",
        "replay",
        "--recording",
        "fixture-noisy",
        "--save-baseline",
        "better",
    )
    assert code == 0
    path = eval_paths.baselines / DATASET / "better.json"
    data = json.loads(path.read_text())
    data["metrics"]["category_accuracy"] = 0.97
    path.write_text(json.dumps(data))
    # loosen absolute thresholds so only the regression rule can fail
    raw = yaml.safe_load(eval_paths.thresholds.read_text())
    raw[DATASET]["thresholds"] = {"error_rate": {"max": 0.5, "required": True}}
    raw[DATASET]["compare_to"] = "better"
    eval_paths.thresholds.write_text(yaml.safe_dump(raw))

    args = ("--predictor", "replay", "--recording", "fixture-noisy", "--gate")
    code, out, _ = invoke(eval_paths, capsys, *args)
    assert code == 1
    assert "GATE FAILED: category_accuracy" in out
    assert "| regression |" in out

    _set_thresholds(eval_paths, tolerance=0.05)
    code, out, _ = invoke(eval_paths, capsys, *args)
    assert code == 0, out


def test_stale_baseline_skips_regression_but_keeps_thresholds(
    eval_paths: EvalPaths, capsys: pytest.CaptureFixture[str]
) -> None:
    path = eval_paths.baselines / DATASET / "heuristic.json"
    data = json.loads(path.read_text())
    data["dataset"]["hash"] = "f" * 64
    data["metrics"]["category_accuracy"] = 1.5  # would fail the regression rule if applied
    path.write_text(json.dumps(data))
    _set_thresholds(eval_paths, compare_to="heuristic")
    code, out, _ = invoke(eval_paths, capsys, "--predictor", "oracle", "--gate")
    assert code == 0
    assert sum("WARNING" in ln and "stale" in ln for ln in out.splitlines()) == 1
    assert "baseline stale, re-run `--save-baseline`" in out
    # absolute thresholds still decide: heuristic fails them
    code, _, _ = invoke(eval_paths, capsys, "--predictor", "heuristic", "--gate")
    assert code == 1


def test_proposed_status_prints_note_without_changing_exit_code(
    eval_paths: EvalPaths, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = invoke(eval_paths, capsys, "--predictor", "oracle", "--gate")
    assert code == 0
    assert sum("not yet confirmed by the user" in ln for ln in out.splitlines()) == 1
    _set_thresholds(eval_paths, status="confirmed")
    code, out, _ = invoke(eval_paths, capsys, "--predictor", "oracle", "--gate")
    assert code == 0
    assert "not yet confirmed" not in out


def test_optional_na_metric_passes_required_na_fails(
    eval_paths: EvalPaths, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = invoke(eval_paths, capsys, "--predictor", "oracle", "--gate")
    assert code == 0
    row = next(ln for ln in out.splitlines() if ln.startswith("| Cost € per doc (mean) |"))
    assert "| n/a |" in row
    _set_thresholds(eval_paths, cost_required=True)
    code, out, _ = invoke(eval_paths, capsys, "--predictor", "oracle", "--gate")
    assert code == 1
    assert "GATE FAILED: cost_eur_per_doc_mean" in out


def test_compare_to_flag_overrides_config_without_gate(
    eval_paths: EvalPaths, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = invoke(eval_paths, capsys, "--predictor", "oracle", "--compare-to", "heuristic")
    assert code == 0
    assert "NOT GATED" in out and "baseline: heuristic" in out
    assert (
        run.main(
            ["--dataset", DATASET, "--predictor", "oracle", "--compare-to", "missing"], eval_paths
        )
        == 2
    )
