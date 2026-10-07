"""Eval runner CLI.

    uv run python -m evals.run --dataset bills_v0 --predictor {oracle|heuristic|replay|<registered>}
        [--provider P] [--model M] [--prompt-version V] [--recording NAME] [--record NAME [--force]]
        [--gate] [--thresholds evals/thresholds.yaml] [--compare-to BASELINE]
        [--save-baseline NAME [--force]] [--out evals/reports]
        [--concurrency 4] [--case-timeout 120] [--cases ID,ID | --tags TAG,TAG]

Exit codes: 0 ok (or skipped: a required env var is not set), 1 gate failed,
2 usage / config / dataset error. Output never contains document text, vendor, person
hint, exception messages or env var values.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from evals.dataset import Dataset, DatasetError, EvalCase, UnknownDatasetError, load_dataset
from evals.gate import (
    Baseline,
    ConfigError,
    baseline_path,
    evaluate,
    load_baseline,
    load_thresholds,
)
from evals.metrics import Scored, case_result, compute_metrics
from evals.paths import EVALS_DIR, EvalPaths
from evals.predictors import REGISTRY, Predictor, PredictorOptions
from evals.predictors.replay import ReplayPredictor
from evals.recording import RECORDING_NAME_RE, RecordingError, recording_dir, write_recording
from evals.report import to_markdown, write_report
from evals.schema import LABEL_SCHEMA_VERSION, DatasetInfo, Prediction, Report, ReportStatus

_NAME_RE = re.compile(RECORDING_NAME_RE)


class UsageError(Exception):
    """Exit 2 with one line. The message is written by us and is safe to print."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        raise UsageError(message)


def _csv(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def build_parser() -> argparse.ArgumentParser:
    p = _Parser(prog="python -m evals.run", description="Run an eval and write a report.")
    p.add_argument("--dataset", required=True)
    p.add_argument("--predictor", required=True)
    p.add_argument("--provider")
    p.add_argument("--model")
    p.add_argument("--prompt-version")
    p.add_argument("--recording", help="recording to replay (--predictor replay)")
    p.add_argument("--record", help="write this run as recording NAME")
    p.add_argument("--force", action="store_true", help="overwrite --record / --save-baseline")
    p.add_argument("--gate", action="store_true", help="enforce thresholds.yaml (exit 1 on fail)")
    p.add_argument("--thresholds", type=Path)
    p.add_argument("--compare-to", help="baseline name (overrides compare_to)")
    p.add_argument("--save-baseline", help="save this report as baseline NAME")
    p.add_argument("--out", type=Path, help="report directory (default evals/reports)")
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--case-timeout", type=float, default=120.0)
    sel = p.add_mutually_exclusive_group()
    sel.add_argument("--cases", type=_csv, help="comma-separated case ids")
    sel.add_argument("--tags", type=_csv, help="comma-separated tags")
    return p


def git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=EVALS_DIR,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return os.environ.get("GIT_SHA") or "unknown"


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-") or "x"


def report_dir(
    out: Path, started: datetime, dataset: str, options: PredictorOptions, name: str
) -> Path:
    parts = [started.strftime("%Y%m%dT%H%M%SZ"), dataset, name]
    if options.model:
        parts.append(options.model)
    if options.prompt_version:
        parts.append(options.prompt_version)
    base = out / "-".join(_slug(p) for p in parts)
    candidate, n = base, 2
    while candidate.exists():
        candidate = base.with_name(f"{base.name}-{n}")
        n += 1
    return candidate


async def _run_case(
    predictor: Predictor, case: EvalCase, timeout: float, sem: asyncio.Semaphore
) -> tuple[Prediction, int]:
    async with sem:
        start = time.perf_counter()
        try:
            pred = await asyncio.wait_for(predictor.predict(case), timeout=timeout)
            if not isinstance(pred, Prediction):
                pred = Prediction(error_kind="InvalidPrediction")
        except TimeoutError:
            pred = Prediction(error_kind="TimeoutError")
        except Exception as exc:  # noqa: BLE001 - every failure becomes a scored error
            pred = Prediction(error_kind=type(exc).__name__)
        wall_ms = round((time.perf_counter() - start) * 1000)
        return pred, wall_ms


async def run_cases(
    predictor: Predictor, cases: Sequence[EvalCase], concurrency: int, timeout: float
) -> list[tuple[Prediction, int]]:
    sem = asyncio.Semaphore(max(1, concurrency))
    total = len(cases)
    done = 0

    async def one(case: EvalCase) -> tuple[Prediction, int]:
        nonlocal done
        result = await _run_case(predictor, case, timeout, sem)
        done += 1
        print(f"[{done:>3}/{total}] {case.id}", flush=True)
        return result

    return list(await asyncio.gather(*(one(c) for c in cases)))


def _select(dataset: Dataset, ids: list[str] | None, tags: list[str] | None) -> list[EvalCase]:
    if ids:
        known = {c.id for c in dataset.cases}
        unknown = [i for i in ids if i not in known]
        if unknown:
            raise UsageError(f"unknown case id(s): {', '.join(unknown)}")
        return [c for c in dataset.cases if c.id in ids]
    if tags:
        selected = [c for c in dataset.cases if set(tags) & set(c.tags)]
        if not selected:
            raise UsageError(f"no case has tag(s): {', '.join(tags)}")
        return selected
    return list(dataset.cases)


def main(argv: Sequence[str] | None = None, paths: EvalPaths | None = None) -> int:
    try:
        return _main(argv, paths)
    except (UsageError, ConfigError, RecordingError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _main(argv: Sequence[str] | None, paths: EvalPaths | None) -> int:
    args = build_parser().parse_args(argv)
    paths = paths or EvalPaths()
    if args.thresholds is not None:
        paths = replace(paths, thresholds=args.thresholds)
    if args.out is not None:
        paths = replace(paths, reports=args.out)
    subset = bool(args.cases or args.tags)
    if subset and (args.gate or args.save_baseline):
        raise UsageError("gate and baselines need the full dataset (drop --cases / --tags)")
    spec = REGISTRY.get(args.predictor)
    if spec is None:
        raise UsageError(
            f"unknown predictor {args.predictor!r} (registered: {', '.join(sorted(REGISTRY))})"
        )
    if args.predictor == "replay" and not args.recording:
        raise UsageError("--predictor replay needs --recording NAME")
    for flag, value in (("--record", args.record), ("--save-baseline", args.save_baseline)):
        if value is not None and not _NAME_RE.match(value):
            raise UsageError(f"{flag}: use lowercase letters, digits, '.', '-', '_'")
    options = PredictorOptions(
        provider=args.provider,
        model=args.model,
        prompt_version=args.prompt_version,
        recording=args.recording,
        dataset=args.dataset,
        paths=paths,
    )
    started = datetime.now(UTC)

    # Key check first (Decision 4): no key, no failure, no factory call.
    missing = [var for var in spec.requires_env(options) if not os.environ.get(var)]
    if missing:
        provider = options.provider or spec.default_provider or "none"
        line = (
            f"SKIPPED: predictor '{spec.name}' (provider '{provider}') needs "
            f"{' and '.join(missing)}, which is not set. No real-provider eval was run."
        )
        print(line)
        # Decision 4 wins over "refuse skipped runs": a skip exits 0 and saves nothing.
        for flag, value in (("--save-baseline", args.save_baseline), ("--record", args.record)):
            if value:
                print(f"{flag} {value!r} not written: the run was skipped")
        report = Report(
            status="skipped",
            dataset=DatasetInfo(
                name=args.dataset,
                hash=None,
                n_cases=None,
                label_schema_version=LABEL_SCHEMA_VERSION,
            ),
            predictor={"name": spec.name, "provider": provider},
            git_sha=git_sha(),
            started_at=started.isoformat(),
            duration_s=0.0,
            recording_stale=False,
            subset=subset,
            skipped_reason=f"missing env: {', '.join(missing)}",
            metrics=None,
            gate=None,
            cases=[],
        )
        write_report(
            report, report_dir(paths.reports, started, args.dataset, options, spec.name), line
        )
        return 0

    try:
        dataset = load_dataset(args.dataset, paths)
    except UnknownDatasetError as exc:
        raise UsageError(exc.problems[0]) from None
    except DatasetError as exc:
        for problem in exc.problems:
            print(problem, file=sys.stderr)
        raise UsageError(f"dataset {args.dataset!r} is invalid (run evals.validate)") from None

    config = load_thresholds(paths.thresholds, dataset.name)
    if args.gate and config is None:
        raise UsageError(f"no thresholds entry for dataset {dataset.name!r} in thresholds.yaml")
    if args.gate and config is not None and config.status == "proposed":
        print(f"NOTE: thresholds for {dataset.name} are proposed, not yet confirmed by the user")
    compare_to = args.compare_to or (config.compare_to if config else None)
    baseline: Baseline | None = None
    baseline_stale = False
    if compare_to:
        baseline = load_baseline(paths.baselines, dataset.name, compare_to)
        baseline_stale = baseline.dataset_hash != dataset.hash
        if baseline_stale:
            print(
                f"WARNING: baseline {compare_to!r} is stale (dataset hash differs); "
                "regression rule skipped, re-run --save-baseline"
            )

    record_target: Path | None = None
    if args.record:
        if dataset.private:
            raise UsageError("--record is not allowed for datasets under datasets_private/")
        record_target = recording_dir(paths.recordings, dataset.name, args.record)
        if record_target.exists() and not args.force:
            raise UsageError(f"recording {args.record!r} exists (use --force to overwrite)")
    if args.save_baseline:
        if dataset.private:
            raise UsageError("--save-baseline is not allowed for datasets under datasets_private/")
        target = baseline_path(paths.baselines, dataset.name, args.save_baseline)
        if target.exists() and not args.force:
            raise UsageError(f"baseline {args.save_baseline!r} exists (use --force to overwrite)")

    cases = _select(dataset, args.cases, args.tags)
    try:
        predictor = spec.factory(options)
    except (RecordingError, UsageError):
        raise
    except Exception as exc:  # noqa: BLE001
        raise UsageError(
            f"predictor {spec.name!r} could not be built ({type(exc).__name__})"
        ) from None

    recording_stale = False
    if isinstance(predictor, ReplayPredictor):
        predictor.check_dataset_hash(dataset.hash)
        recording_stale = predictor.recording_stale
        if recording_stale:
            print(f"WARNING: recording {args.recording!r} is stale (dataset hash differs)")
    if recording_stale and args.save_baseline:
        raise UsageError("cannot save a baseline from a stale recording")

    t0 = time.perf_counter()
    outcomes = asyncio.run(run_cases(predictor, cases, args.concurrency, args.case_timeout))
    duration = round(time.perf_counter() - t0, 3)

    scored = [
        Scored(c.id, c.tags, dataset.labels[c.id].expected, pred, wall_ms)
        for c, (pred, wall_ms) in zip(cases, outcomes, strict=True)
    ]
    metrics = compute_metrics(scored)
    gate = evaluate(metrics, config, baseline, baseline_stale, args.gate, compare_to)
    status: ReportStatus = "gate_failed" if args.gate and not gate.passed else "ok"
    report = Report(
        status=status,
        dataset=DatasetInfo(
            name=dataset.name,
            hash=dataset.hash,
            n_cases=len(dataset.cases),
            label_schema_version=dataset.manifest.label_schema_version,
        ),
        predictor=predictor.describe(),
        git_sha=git_sha(),
        started_at=started.isoformat(),
        duration_s=duration,
        recording_stale=recording_stale,
        subset=subset,
        metrics=metrics,
        gate=gate,
        cases=[case_result(s) for s in scored],
    )
    out_dir = report_dir(paths.reports, started, dataset.name, options, spec.name)
    md = write_report(report, out_dir)

    if record_target is not None:
        if record_target.exists():
            shutil.rmtree(record_target)
        write_recording(
            record_target,
            {
                "predictor": predictor.describe(),
                "dataset": dataset.name,
                "dataset_hash": dataset.hash,
                "git_sha": report.git_sha,
                "recorded_at": started.isoformat(),
                "label_schema_version": dataset.manifest.label_schema_version,
                "n_cases": len(cases),
                "subset": subset,
            },
            {s.case_id: s.predicted for s in scored},
        )
        print(f"recorded {len(cases)} prediction(s) as {args.record!r}")
    if args.save_baseline:
        target = baseline_path(paths.baselines, dataset.name, args.save_baseline)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((out_dir / "report.json").read_text(encoding="utf-8"), encoding="utf-8")
        target.with_suffix(".md").write_text(to_markdown(report), encoding="utf-8")
        print(f"saved baseline {args.save_baseline!r}")

    errors = metrics["n_errors"]
    print(f"{len(cases)} cases, {errors} error(s); report: {out_dir}")
    print()
    print(md)
    if args.gate and not gate.passed:
        failed = [r.metric for r in gate.rows if r.result in ("fail", "regression")]
        print(f"GATE FAILED: {', '.join(failed)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
