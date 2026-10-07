"""`pipeline` predictor (#9): the perfect-reader gate run offline, the key-missing skip."""

from __future__ import annotations

import pytest

from evals.fakes.perfect_reader import load_replies
from evals.paths import EvalPaths
from evals.predictors.pipeline import parse_models
from tests.evals.conftest import DATASET
from tests.evals.test_run import invoke, last_report

# Cases that cannot reach 1.0 by design (listed in the PR): the Jahressteuerbescheinigung is
# only classified in v1 (#9 Decision 5, extraction in #19), so it has no category / gross.
BY_DESIGN = {"b044-jahressteuerbescheinigung": {"category", "category_group", "gross_amount"}}
PERFECT = (
    "relevance_precision",
    "relevance_recall",
    "doc_type_accuracy",
    "deductible_exact_match",
    "tax_year_accuracy",
)


def test_perfect_reader_passes_the_gate(
    eval_paths: EvalPaths, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    code, out, _ = invoke(
        eval_paths, capsys, "--predictor", "pipeline", "--provider", "fake", "--gate"
    )
    assert code == 0, out
    report = last_report(eval_paths)
    m = report["metrics"]
    for name in PERFECT:
        assert m[name] == 1.0, name
    assert m["deductible_abs_error_eur_mean"] == "0.00"
    assert m["labour_share_35a_abs_error_eur_mean"] == "0.00"
    assert m["n_errors"] == 0
    for case in report["cases"]:
        wrong = {k for k, v in case["match"].items() if v is False} - {
            "payment_method",
            "invoice_date",
        }
        assert wrong == BY_DESIGN.get(case["id"], set()), case["id"]
    assert report["predictor"]["prompt_version"] == "v2"
    assert len(report["predictor"]["prompt_sha256"]) == 64


def test_openai_without_key_is_skipped(
    eval_paths: EvalPaths, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    code, out, _ = invoke(
        eval_paths, capsys, "--predictor", "pipeline", "--provider", "openai", "--gate"
    )
    assert code == 0
    assert out.startswith(
        "SKIPPED: predictor 'pipeline' (provider 'openai') needs OPENAI_API_KEY, which is not set."
    )


def test_unknown_prompt_version_is_a_usage_error(
    eval_paths: EvalPaths, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = invoke(
        eval_paths,
        capsys,
        "--predictor",
        "pipeline",
        "--provider",
        "fake",
        "--prompt-version",
        "v9",
    )
    assert code == 2 and "pipeline" in err


def test_parse_models() -> None:
    assert parse_models(None) == {}
    assert parse_models("openai:gpt-4.1") == {
        "classify": "openai:gpt-4.1",
        "extract": "openai:gpt-4.1",
    }
    assert parse_models("classify=openai:a,extract=openai:b") == {
        "classify": "openai:a",
        "extract": "openai:b",
    }
    with pytest.raises(ValueError):
        parse_models("foo=openai:a")


def test_perfect_reader_covers_every_case(eval_paths: EvalPaths) -> None:
    replies = load_replies(eval_paths.specs / f"{DATASET}.yaml")
    assert len(replies) == 60
