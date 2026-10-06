"""`report.json` (stable, sorted keys) and `report.md` (pasted into PR descriptions).

Both contain codes and numbers only: never vendor, person hint, notes, document text or
exception messages.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from evals.metrics import case_failed
from evals.schema import Report

LABELS: dict[str, str] = {
    "relevance_precision": "Relevance precision",
    "relevance_recall": "Relevance recall",
    "relevance_f1": "Relevance F1",
    "doc_type_accuracy": "Doc type accuracy",
    "category_accuracy": "Category accuracy",
    "category_group_accuracy": "Category group accuracy",
    "gross_exact_match": "Gross exact match",
    "deductible_exact_match": "Deductible exact match",
    "deductible_abs_error_eur_mean": "Deductible abs error € (mean)",
    "deductible_abs_error_eur_max": "Deductible abs error € (max)",
    "deductible_abs_error_eur_sum": "Deductible abs error € (sum)",
    "overclaim_eur_sum": "Overclaim € (sum)",
    "underclaim_eur_sum": "Underclaim € (sum)",
    "labour_share_35a_abs_error_eur_mean": "§35a labour share abs error € (mean)",
    "tax_year_accuracy": "Tax year accuracy",
    "payment_method_accuracy": "Payment method accuracy",
    "invoice_date_exact_match": "Invoice date exact match",
    "vendor_match": "Vendor match (report only)",
    "person_hint_match": "Person hint match (report only)",
    "n_errors": "Errors",
    "error_rate": "Error rate",
    "cost_eur_total": "Cost € (total)",
    "cost_eur_per_doc_mean": "Cost € per doc (mean)",
    "cost_eur_per_doc_p95": "Cost € per doc (p95)",
    "input_tokens_total": "Input tokens",
    "output_tokens_total": "Output tokens",
    "latency_ms_p50": "Latency ms (p50)",
    "latency_ms_p95": "Latency ms (p95)",
}


def fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def predictor_title(desc: dict[str, str]) -> str:
    name = desc.get("name", "?")
    bits = [desc[k] for k in ("provider", "model") if desc.get(k) and desc[k] not in ("-", "none")]
    pv = desc.get("prompt_version")
    if pv and pv != "-":
        bits.append(f"prompt {pv}")
    if desc.get("recording"):
        bits.append(f"recording {desc['recording']}")
    return f"{name} ({', '.join(bits)})" if bits else name


def to_markdown(report: Report, skipped_line: str | None = None) -> str:
    title = predictor_title(report.predictor)
    if report.status == "skipped":
        return f"### Eval {report.dataset.name} · {title} · SKIPPED\n{skipped_line or ''}\n"
    gate = report.gate
    if gate is not None and gate.enabled:
        verdict = "GATE PASSED" if gate.passed else "GATE FAILED"
    else:
        verdict = "NOT GATED"
    lines = [f"### Eval {report.dataset.name} · {title} · {verdict}"]
    meta = [
        f"{report.dataset.n_cases} cases",
        f"dataset {(report.dataset.hash or '')[:8]}",
        f"git {report.git_sha[:7]}",
        report.started_at[:16] + "Z",
    ]
    if gate is not None and gate.compare_to:
        stale = " (baseline stale, re-run `--save-baseline`)" if gate.baseline_stale else ""
        meta.append(f"baseline: {gate.compare_to}{stale}")
    if gate is not None and gate.thresholds_status == "proposed":
        meta.append("thresholds: proposed")
    if report.recording_stale:
        meta.append("recording stale (dataset hash differs)")
    if report.subset:
        meta.append("subset run")
    lines.append(" · ".join(meta))
    lines.append("")
    lines.append("| Metric | Value | Threshold | Baseline | Δ | Result |")
    lines.append("|---|---|---|---|---|---|")
    for row in gate.rows if gate is not None else []:
        threshold = ""
        if row.op is not None:
            threshold = f"{'≥' if row.op == 'min' else '≤'} {row.bound}"
            if not row.required:
                threshold += " (optional)"
        baseline = fmt(row.baseline) if gate is not None and gate.compare_to else ""
        result = "" if row.result == "-" else row.result
        lines.append(
            f"| {LABELS.get(row.metric, row.metric)} | {fmt(row.value)} | {threshold} "
            f"| {baseline} | {row.delta or ''} | {result} |"
        )
    metrics = report.metrics or {}
    per_group = metrics.get("per_group") or {}
    if per_group:
        lines += ["", "**Per category group**", "", "| Group | Cases | Category accuracy |"]
        lines.append("|---|---|---|")
        for group, row in per_group.items():
            lines.append(f"| {group} | {row['n']} | {fmt(row['category_accuracy'])} |")
    confusions = metrics.get("top_confusions") or []
    if confusions:
        lines += ["", "**Top confusions**", "", "| Expected | Predicted | Count |", "|---|---|---|"]
        for c in confusions:
            lines.append(f"| {c['expected']} | {c['predicted']} | {c['count']} |")
    failing = [c for c in report.cases if case_failed(c)]
    if failing:
        lines += ["", f"**Failing cases** ({len(failing)}, first 20)", ""]
        for c in failing[:20]:
            wrong = [k for k, v in c.match.items() if v is False]
            extra = f" · error {c.error_kind}" if c.error_kind else ""
            tags = f" [{', '.join(c.tags)}]" if c.tags else ""
            lines.append(f"- `{c.id}`{tags}: {', '.join(wrong) or '-'}{extra}")
    return "\n".join(lines) + "\n"


def report_json(report: Report) -> str:
    return json.dumps(report.model_dump(mode="json"), sort_keys=True, indent=2) + "\n"


def write_report(report: Report, directory: Path, skipped_line: str | None = None) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    md = to_markdown(report, skipped_line)
    (directory / "report.json").write_text(report_json(report), encoding="utf-8")
    (directory / "report.md").write_text(md, encoding="utf-8")
    return md
