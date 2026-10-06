"""Runner, predictors, recordings, skip path and privacy of the outputs."""

from __future__ import annotations

import asyncio
import json
import secrets
from pathlib import Path
from typing import Any

import pytest

from evals import run
from evals.dataset import EvalCase, load_dataset
from evals.paths import EVALS_DIR, EvalPaths
from evals.predictors import REGISTRY, PredictorOptions, PredictorSpec
from evals.predictors.heuristic import HeuristicPredictor
from evals.schema import Prediction
from tests.evals.conftest import DATASET

QUALITY = (
    "relevance_precision",
    "relevance_recall",
    "relevance_f1",
    "doc_type_accuracy",
    "category_accuracy",
    "category_group_accuracy",
    "gross_exact_match",
    "deductible_exact_match",
    "tax_year_accuracy",
    "payment_method_accuracy",
    "invoice_date_exact_match",
)
ERRORS = (
    "deductible_abs_error_eur_mean",
    "deductible_abs_error_eur_max",
    "deductible_abs_error_eur_sum",
    "overclaim_eur_sum",
    "underclaim_eur_sum",
    "labour_share_35a_abs_error_eur_mean",
)


def invoke(
    paths: EvalPaths, capsys: pytest.CaptureFixture[str], *args: str
) -> tuple[int, str, str]:
    code = run.main(["--dataset", DATASET, *args], paths)
    out = capsys.readouterr()
    return code, out.out, out.err


def reports(paths: EvalPaths) -> list[Path]:
    return (
        sorted(p for p in paths.reports.iterdir() if p.is_dir()) if paths.reports.exists() else []
    )


def last_report(paths: EvalPaths) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((reports(paths)[-1] / "report.json").read_text())
    return data


def without_latency(metrics: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in metrics.items() if not k.startswith("latency_")}


def test_oracle_gate_passes_with_perfect_scores(
    eval_paths: EvalPaths, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = invoke(eval_paths, capsys, "--predictor", "oracle", "--gate")
    assert code == 0
    assert "GATE PASSED" in out
    m = last_report(eval_paths)["metrics"]
    for key in QUALITY:
        assert m[key] == 1.0, key
    for key in ERRORS:
        assert m[key] == "0.00", key
    assert m["error_rate"] == 0.0
    assert m["n_errors"] == 0


def test_heuristic_matches_committed_baseline_and_fails_gate(
    eval_paths: EvalPaths, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = invoke(eval_paths, capsys, "--predictor", "heuristic")
    assert code == 0
    report_dir = reports(eval_paths)[-1]
    assert report_dir.name.endswith(f"-{DATASET}-heuristic")
    assert (report_dir / "report.json").is_file() and (report_dir / "report.md").is_file()
    report = last_report(eval_paths)
    baseline = json.loads((EVALS_DIR / "baselines" / DATASET / "heuristic.json").read_text())
    assert report["dataset"]["hash"] == baseline["dataset"]["hash"]
    assert without_latency(report["metrics"]) == without_latency(baseline["metrics"])

    code, out, _ = invoke(eval_paths, capsys, "--predictor", "heuristic", "--gate")
    assert code == 1
    md = (reports(eval_paths)[-1] / "report.md").read_text()
    assert "GATE FAILED" in md
    assert "| Relevance recall |" in md and "| fail |" in md
    assert last_report(eval_paths)["status"] == "gate_failed"


def test_replay_fixture_noisy_exact_metrics(
    eval_paths: EvalPaths, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = invoke(
        eval_paths, capsys, "--predictor", "replay", "--recording", "fixture-noisy"
    )
    assert code == 0
    report = last_report(eval_paths)
    assert report["recording_stale"] is False
    m = report["metrics"]
    # Hand-computed from the fixture's deliberate mistakes (see recordings/.../meta.json):
    # b001 wrong category (same group), b013 missed relevant case, b047 irrelevant predicted
    # relevant (+189.90), b029 wrong gross / deductible (+60.00), b036 labour share 736 vs 763,
    # b040 TimeoutError. 48 positives: TP 46, FN 2, FP 1.
    expected = {
        "n_cases": 60,
        "n_errors": 1,
        "error_rate": 0.0167,
        "relevance_precision": 0.9787,  # 46/47
        "relevance_recall": 0.9583,  # 46/48
        "relevance_f1": 0.9684,
        "doc_type_accuracy": 0.9833,  # 59/60
        "category_accuracy": 0.9286,  # 52/56
        "category_group_accuracy": 0.9464,  # 53/56
        "gross_exact_match": 0.9643,  # 54/56
        "deductible_exact_match": 0.9333,  # 56/60
        "deductible_abs_error_eur_mean": "144.29",  # 8657.17 / 60
        "deductible_abs_error_eur_max": "5223.00",
        "deductible_abs_error_eur_sum": "8657.17",
        "overclaim_eur_sum": "249.90",
        "underclaim_eur_sum": "8407.27",
        "labour_share_35a_abs_error_eur_mean": "3.38",  # 27 / 8
        "tax_year_accuracy": 0.9833,
        "payment_method_accuracy": 0.9833,
        "invoice_date_exact_match": 0.9833,
        "vendor_match": 0.9833,
        "person_hint_match": 0.9811,  # 52/53
        "cost_eur_total": "0.0948",  # 59 x 0.0016 + 0.0004
        "cost_eur_per_doc_mean": "0.0016",
        "cost_eur_per_doc_p95": "0.0016",
        "input_tokens_total": 136500,
        "output_tokens_total": 10050,
        "latency_ms_p50": 2800,  # rank 30: 400 (error case) then 1400 + 50 * i
        "latency_ms_p95": 4200,  # rank 57
    }
    for key, value in expected.items():
        assert m[key] == value, key
    assert report["predictor"]["usage"] == "recorded"


def _copy_recording(paths: EvalPaths, name: str) -> Path:
    import shutil

    src = paths.recordings / DATASET / "fixture-noisy"
    dst = paths.recordings / DATASET / name
    shutil.copytree(src, dst)
    return dst


def test_replay_missing_case_and_stale_recording(
    eval_paths: EvalPaths, capsys: pytest.CaptureFixture[str]
) -> None:
    rec = _copy_recording(eval_paths, "partial")
    lines = (rec / "predictions.jsonl").read_text().splitlines()
    kept = [ln for ln in lines if '"b047-kleidung"' not in ln]
    (rec / "predictions.jsonl").write_text("\n".join(kept) + "\n")
    code, out, _ = invoke(eval_paths, capsys, "--predictor", "replay", "--recording", "partial")
    assert code == 0
    case = next(c for c in last_report(eval_paths)["cases"] if c["id"] == "b047-kleidung")
    assert case["error_kind"] == "MissingRecording"
    assert "stale" not in out

    meta = json.loads((rec / "meta.json").read_text())
    meta["dataset_hash"] = "0" * 64
    (rec / "meta.json").write_text(json.dumps(meta))
    code, out, _ = invoke(eval_paths, capsys, "--predictor", "replay", "--recording", "partial")
    assert code == 0
    assert last_report(eval_paths)["recording_stale"] is True
    assert sum("WARNING" in line and "stale" in line for line in out.splitlines()) == 1


def test_record_then_replay_gives_identical_metrics(
    eval_paths: EvalPaths, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, _ = invoke(eval_paths, capsys, "--predictor", "heuristic", "--record", "t1")
    assert code == 0
    recorded = last_report(eval_paths)["metrics"]
    rec = eval_paths.recordings / DATASET / "t1"
    meta = json.loads((rec / "meta.json").read_text())
    assert meta["dataset_hash"] == load_dataset(DATASET, eval_paths).hash
    assert {"predictor", "git_sha", "recorded_at", "label_schema_version"} <= set(meta)
    ids = [json.loads(ln)["case_id"] for ln in (rec / "predictions.jsonl").read_text().splitlines()]
    assert ids == sorted(ids) and len(ids) == 60

    code, _, _ = invoke(eval_paths, capsys, "--predictor", "replay", "--recording", "t1")
    assert code == 0
    assert without_latency(last_report(eval_paths)["metrics"]) == without_latency(recorded)

    code, _, err = invoke(eval_paths, capsys, "--predictor", "heuristic", "--record", "t1")
    assert code == 2
    assert "exists" in err
    code, _, _ = invoke(eval_paths, capsys, "--predictor", "heuristic", "--record", "t1", "--force")
    assert code == 0


def test_record_refused_for_private_dataset(
    eval_paths: EvalPaths, capsys: pytest.CaptureFixture[str]
) -> None:
    import shutil

    shutil.copytree(eval_paths.datasets / DATASET, eval_paths.datasets_private / "private_v0")
    code = run.main(
        ["--dataset", "private_v0", "--predictor", "heuristic", "--record", "x"], eval_paths
    )
    assert code == 2
    assert "datasets_private" in capsys.readouterr().err


# --- skip path, secrets, label isolation --------------------------------------------------


class _Probe:
    """Test predictor: records what it sees; optional per-case behaviour."""

    name = "probe"
    seen: list[EvalCase] = []

    def __init__(self, behaviour: dict[str, str] | None = None) -> None:
        self.behaviour = behaviour or {}

    def describe(self) -> dict[str, str]:
        return {"name": self.name, "provider": "dummy", "model": "probe", "prompt_version": "-"}

    async def predict(self, case: EvalCase) -> Prediction:
        _Probe.seen.append(case)
        action = self.behaviour.get(case.id)
        if action == "sleep":
            await asyncio.sleep(5)
        if action is not None and action.startswith("raise:"):
            raise ValueError(action.removeprefix("raise:"))
        return Prediction(tax_relevant=False)


def _register(monkeypatch: pytest.MonkeyPatch, factory: Any, env: tuple[str, ...] = ()) -> None:
    monkeypatch.setitem(
        REGISTRY,
        "probe",
        PredictorSpec(
            name="probe", factory=factory, requires_env=lambda _o: env, default_provider="dummy"
        ),
    )


@pytest.mark.parametrize("value", [None, ""])
def test_missing_key_skips_without_building_predictor(
    eval_paths: EvalPaths,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    value: str | None,
) -> None:
    if value is None:
        monkeypatch.delenv("EVAL_TEST_DUMMY_KEY", raising=False)
    else:
        monkeypatch.setenv("EVAL_TEST_DUMMY_KEY", value)
    calls: list[PredictorOptions] = []

    def factory(options: PredictorOptions) -> _Probe:
        calls.append(options)
        return _Probe()

    _register(monkeypatch, factory, ("EVAL_TEST_DUMMY_KEY",))
    code, out, _ = invoke(eval_paths, capsys, "--predictor", "probe", "--gate")
    assert code == 0
    assert (
        "SKIPPED: predictor 'probe' (provider 'dummy') needs EVAL_TEST_DUMMY_KEY, "
        "which is not set. No real-provider eval was run." in out.splitlines()
    )
    assert last_report(eval_paths)["status"] == "skipped"
    assert calls == []


def test_key_set_runs_and_value_never_appears(
    eval_paths: EvalPaths, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    sentinel = f"sk-test-{secrets.token_hex(12)}"
    monkeypatch.setenv("EVAL_TEST_DUMMY_KEY", sentinel)
    _Probe.seen = []
    _register(monkeypatch, lambda _o: _Probe(), ("EVAL_TEST_DUMMY_KEY",))
    code, out, err = invoke(eval_paths, capsys, "--predictor", "probe")
    assert code == 0
    assert len(_Probe.seen) == 60
    for case in _Probe.seen:
        assert not hasattr(case, "expected")
    assert sentinel not in out + err
    for path in reports(eval_paths)[-1].iterdir():
        assert sentinel not in path.read_text()


def test_heuristic_receives_cases_without_labels(
    eval_paths: EvalPaths, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[EvalCase] = []
    original = HeuristicPredictor.predict

    async def spy(self: HeuristicPredictor, case: EvalCase) -> Prediction:
        seen.append(case)
        return await original(self, case)

    monkeypatch.setattr(HeuristicPredictor, "predict", spy)
    assert invoke(eval_paths, capsys, "--predictor", "heuristic")[0] == 0
    assert len(seen) == 60
    assert all(not hasattr(c, "expected") and not hasattr(c, "label") for c in seen)


def test_case_timeout_becomes_timeout_error(
    eval_paths: EvalPaths, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _register(monkeypatch, lambda _o: _Probe({"b005-fortbildung-sprachkurs": "sleep"}))
    code, _, _ = invoke(
        eval_paths, capsys, "--predictor", "probe", "--case-timeout", "1", "--concurrency", "8"
    )
    assert code == 0
    cases = {c["id"]: c for c in last_report(eval_paths)["cases"]}
    assert cases["b005-fortbildung-sprachkurs"]["error_kind"] == "TimeoutError"
    assert sum(1 for c in cases.values() if c["error_kind"]) == 1
    assert last_report(eval_paths)["metrics"]["n_cases"] == 60


def test_predictor_exception_message_never_leaks(
    eval_paths: EvalPaths, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    sentinel = f"leak-{secrets.token_hex(8)}"
    _register(monkeypatch, lambda _o: _Probe({"b047-kleidung": f"raise:{sentinel}"}))
    code, out, err = invoke(eval_paths, capsys, "--predictor", "probe")
    assert code == 0
    assert sentinel not in out + err
    report_dir = reports(eval_paths)[-1]
    for name in ("report.json", "report.md"):
        assert sentinel not in (report_dir / name).read_text()
    case = next(c for c in last_report(eval_paths)["cases"] if c["id"] == "b047-kleidung")
    assert case["error_kind"] == "ValueError"


def test_reports_contain_no_vendor_or_person_hint(
    eval_paths: EvalPaths, capsys: pytest.CaptureFixture[str]
) -> None:
    ds = load_dataset(DATASET, eval_paths)
    secrets_in_labels = {
        value
        for label in ds.labels.values()
        for value in (label.expected.vendor, label.expected.person_hint)
        if value
    }
    for args in (
        ("--predictor", "heuristic"),
        ("--predictor", "oracle", "--gate"),
        ("--predictor", "replay", "--recording", "fixture-noisy"),
    ):
        invoke(eval_paths, capsys, *args)
    for report_dir in reports(eval_paths):
        for name in ("report.json", "report.md"):
            text = (report_dir / name).read_text()
            for value in secrets_in_labels:
                assert value not in text, (report_dir.name, name)


@pytest.mark.parametrize(
    ("args", "needle"),
    [
        (["--dataset", "nope_v9", "--predictor", "oracle"], "unknown dataset"),
        (["--dataset", DATASET, "--predictor", "nope"], "registered: heuristic, oracle, replay"),
        (["--dataset", DATASET, "--predictor", "replay"], "--recording"),
        (["--dataset", DATASET, "--predictor", "oracle", "--gate", "--tags", "cash_35a"], "full"),
        (
            [
                "--dataset",
                DATASET,
                "--predictor",
                "oracle",
                "--save-baseline",
                "x",
                "--cases",
                "b001-arbeitsmittel-notebook",
            ],
            "full",
        ),
    ],
)
def test_usage_errors_exit_2_with_one_line(
    eval_paths: EvalPaths, capsys: pytest.CaptureFixture[str], args: list[str], needle: str
) -> None:
    code = run.main(args, eval_paths)
    err = capsys.readouterr().err
    assert code == 2
    assert needle in err
    assert len(err.strip().splitlines()) == 1


def test_unknown_threshold_metric_exits_2(
    eval_paths: EvalPaths, capsys: pytest.CaptureFixture[str]
) -> None:
    text = eval_paths.thresholds.read_text().replace("error_rate:", "error_ratio:")
    eval_paths.thresholds.write_text(text)
    code, _, err = invoke(eval_paths, capsys, "--predictor", "oracle", "--gate")
    assert code == 2
    assert "unknown metric name 'error_ratio'" in err


def test_gate_without_thresholds_entry_exits_2(
    eval_paths: EvalPaths, capsys: pytest.CaptureFixture[str]
) -> None:
    eval_paths.thresholds.write_text("other_v0:\n  status: proposed\n")
    code, _, err = invoke(eval_paths, capsys, "--predictor", "oracle", "--gate")
    assert code == 2
    assert "no thresholds entry" in err


def test_subset_run_by_tags(eval_paths: EvalPaths, capsys: pytest.CaptureFixture[str]) -> None:
    code, _, _ = invoke(eval_paths, capsys, "--predictor", "oracle", "--tags", "cash_35a")
    assert code == 0
    report = last_report(eval_paths)
    assert report["subset"] is True
    assert [c["id"] for c in report["cases"]] == ["b037-handwerker-bar"]
