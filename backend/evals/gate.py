"""Thresholds (`evals/thresholds.yaml`), baselines and the gate.

`--gate` fails (exit 1) if any threshold is violated, a `required` metric is n/a, or a
regression metric is worse than the `compare_to` baseline by more than the tolerance.
Config problems raise `ConfigError` (exit 2).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

import yaml

from evals.metrics import (
    HIGHER_IS_BETTER,
    LOWER_IS_BETTER,
    MONEY_METRICS,
    metric_number,
)
from evals.schema import GateResult, GateRow

DISPLAY_ORDER: tuple[str, ...] = (
    "relevance_precision",
    "relevance_recall",
    "relevance_f1",
    "doc_type_accuracy",
    "category_accuracy",
    "category_group_accuracy",
    "gross_exact_match",
    "deductible_exact_match",
    "deductible_abs_error_eur_mean",
    "deductible_abs_error_eur_max",
    "deductible_abs_error_eur_sum",
    "overclaim_eur_sum",
    "underclaim_eur_sum",
    "labour_share_35a_abs_error_eur_mean",
    "tax_year_accuracy",
    "payment_method_accuracy",
    "invoice_date_exact_match",
    "vendor_match",
    "person_hint_match",
    "n_errors",
    "error_rate",
    "cost_eur_total",
    "cost_eur_per_doc_mean",
    "cost_eur_per_doc_p95",
    "input_tokens_total",
    "output_tokens_total",
    "latency_ms_p50",
    "latency_ms_p95",
)
NEVER_GATED = frozenset({"vendor_match", "person_hint_match"})


class ConfigError(Exception):
    """Thresholds / baseline configuration problem. Safe to print."""


@dataclass(frozen=True)
class Threshold:
    op: Literal["min", "max"]
    bound: Decimal
    required: bool


@dataclass(frozen=True)
class Regression:
    tolerance: Decimal = Decimal(0)
    metrics: tuple[str, ...] = ()
    per_metric: dict[str, Decimal] = field(default_factory=dict)

    def tolerance_for(self, metric: str) -> Decimal:
        return self.per_metric.get(metric, self.tolerance)


@dataclass(frozen=True)
class DatasetThresholds:
    status: str
    compare_to: str | None
    thresholds: dict[str, Threshold]
    regression: Regression


def _dec(value: Any, where: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise ConfigError(f"thresholds.yaml: {where} must be a number")
    try:
        return Decimal(str(value))
    except InvalidOperation:
        raise ConfigError(f"thresholds.yaml: {where} must be a number") from None


def _check_metric(name: Any, where: str) -> str:
    if not isinstance(name, str) or name not in DISPLAY_ORDER:
        raise ConfigError(f"thresholds.yaml: unknown metric name {name!r} in {where}")
    if name in NEVER_GATED:
        raise ConfigError(f"thresholds.yaml: metric {name!r} is report-only and cannot be gated")
    return name


def load_thresholds(path: Path, dataset: str) -> DatasetThresholds | None:
    """The entry for `dataset`, or None if the file has none. Raises `ConfigError`."""
    if not path.is_file():
        return None
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        raise ConfigError("thresholds.yaml: not valid YAML") from None
    if not isinstance(raw, dict):
        raise ConfigError("thresholds.yaml: top level must be a mapping of dataset names")
    entry = raw.get(dataset)
    if entry is None:
        return None
    if not isinstance(entry, dict):
        raise ConfigError(f"thresholds.yaml: entry {dataset!r} must be a mapping")
    unknown_keys = set(entry) - {"status", "compare_to", "thresholds", "regression"}
    if unknown_keys:
        raise ConfigError(f"thresholds.yaml: unknown key(s) {sorted(unknown_keys)} in {dataset!r}")
    status = entry.get("status", "proposed")
    if status not in ("proposed", "confirmed"):
        raise ConfigError("thresholds.yaml: status must be 'proposed' or 'confirmed'")
    compare_to = entry.get("compare_to")
    if compare_to is not None and not isinstance(compare_to, str):
        raise ConfigError("thresholds.yaml: compare_to must be a baseline name or null")
    thresholds: dict[str, Threshold] = {}
    for name, spec in (entry.get("thresholds") or {}).items():
        metric = _check_metric(name, "thresholds")
        if not isinstance(spec, dict) or set(spec) - {"min", "max", "required"}:
            raise ConfigError(f"thresholds.yaml: {metric}: use {{min|max: N, required: bool}}")
        ops = [k for k in ("min", "max") if k in spec]
        if len(ops) != 1:
            raise ConfigError(f"thresholds.yaml: {metric}: exactly one of min / max")
        op: Literal["min", "max"] = "min" if ops[0] == "min" else "max"
        required = spec.get("required", True)
        if not isinstance(required, bool):
            raise ConfigError(f"thresholds.yaml: {metric}: required must be true or false")
        thresholds[metric] = Threshold(op, _dec(spec[op], metric), required)
    reg_raw = entry.get("regression") or {}
    if not isinstance(reg_raw, dict) or set(reg_raw) - {"tolerance", "metrics", "per_metric"}:
        raise ConfigError("thresholds.yaml: regression takes tolerance, metrics, per_metric")
    reg_metrics = tuple(_check_metric(m, "regression.metrics") for m in reg_raw.get("metrics", []))
    per_metric = {
        _check_metric(k, "regression.per_metric"): _dec(v, f"regression.per_metric.{k}")
        for k, v in (reg_raw.get("per_metric") or {}).items()
    }
    regression = Regression(
        tolerance=_dec(reg_raw.get("tolerance", 0), "regression.tolerance"),
        metrics=reg_metrics,
        per_metric=per_metric,
    )
    return DatasetThresholds(status, compare_to, thresholds, regression)


@dataclass(frozen=True)
class Baseline:
    name: str
    metrics: dict[str, Any]
    dataset_hash: str | None


def baseline_path(baselines_root: Path, dataset: str, name: str) -> Path:
    return baselines_root / dataset / f"{name}.json"


def load_baseline(baselines_root: Path, dataset: str, name: str) -> Baseline:
    path = baseline_path(baselines_root, dataset, name)
    if not path.is_file():
        raise ConfigError(f"baseline {name!r} not found for dataset {dataset!r}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return Baseline(name, dict(data["metrics"]), data["dataset"]["hash"])
    except (json.JSONDecodeError, KeyError, TypeError):
        raise ConfigError(f"baseline {name!r} is not a valid report JSON") from None


def _fmt_delta(metric: str, delta: Decimal) -> str:
    sign = "+" if delta >= 0 else "-"
    mag = abs(delta)
    if metric in MONEY_METRICS:
        places = 4 if metric.startswith("cost_") else 2
        return f"{sign}{mag:.{places}f}"
    if metric in (
        "n_errors",
        "input_tokens_total",
        "output_tokens_total",
        "latency_ms_p50",
        "latency_ms_p95",
    ):
        return f"{sign}{mag:.0f}"
    return f"{sign}{mag:.4f}"


def evaluate(
    metrics: dict[str, Any],
    config: DatasetThresholds | None,
    baseline: Baseline | None,
    baseline_stale: bool,
    gate_enabled: bool,
    compare_to: str | None,
) -> GateResult:
    thresholds = config.thresholds if config else {}
    regression = config.regression if config else Regression()
    check_regression = baseline is not None and not baseline_stale
    rows: list[GateRow] = []
    for metric in DISPLAY_ORDER:
        value = metrics.get(metric)
        num = metric_number(value)
        th = thresholds.get(metric)
        result: Literal["pass", "fail", "n/a", "regression", "-"] = "-"
        if th is not None:
            if num is None:
                result = "fail" if th.required else "n/a"
            elif (th.op == "min" and num >= th.bound) or (th.op == "max" and num <= th.bound):
                result = "pass"
            else:
                result = "fail"
        base_value = baseline.metrics.get(metric) if baseline is not None else None
        base_num = metric_number(base_value)
        delta = None
        if num is not None and base_num is not None:
            delta = _fmt_delta(metric, num - base_num)
        reg_checked = check_regression and metric in regression.metrics
        if reg_checked and result != "fail":
            tol = regression.tolerance_for(metric)
            worse = False
            if base_num is not None:
                if num is None:
                    worse = True
                elif metric in HIGHER_IS_BETTER:
                    worse = num < base_num - tol
                elif metric in LOWER_IS_BETTER:
                    worse = num > base_num + tol
            if worse:
                result = "regression"
            elif result == "-":
                result = "pass"
        rows.append(
            GateRow(
                metric=metric,
                value=value,
                op=th.op if th else None,
                bound=format(th.bound, "f") if th else None,
                required=th.required if th else False,
                baseline=base_value,
                delta=delta,
                regression_checked=reg_checked,
                result=result,
            )
        )
    passed = not any(r.result in ("fail", "regression") for r in rows)
    return GateResult(
        enabled=gate_enabled,
        passed=passed if gate_enabled else None,
        thresholds_status=config.status if config else None,
        compare_to=compare_to,
        baseline_stale=baseline_stale if baseline is not None else None,
        rows=rows,
    )
