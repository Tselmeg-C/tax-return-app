"""Validate a dataset: labels, coverage, sizes and the privacy scan.

    uv run python -m evals.validate --dataset bills_v0

Prints one line per problem (case id + rule, never a value), then a coverage table.
Exit codes: 0 ok, 1 problems found, 2 unknown dataset / usage error.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import ValidationError

from app.domain.enums import CATEGORY_GROUP, Category
from evals.dataset import (
    MAX_DATASET_BYTES,
    MAX_FILE_BYTES,
    Manifest,
    UnknownDatasetError,
    load_label,
    resolve_dataset_dir,
)
from evals.paths import EvalPaths
from evals.privacy import scan_dataset, scan_tree
from evals.schema import LABEL_SCHEMA_VERSION, ExpectedLabel, Tag, Variant

OFFICIAL_KEY = "official_no_category"
MAX_EXEMPTIONS = 3


@dataclass
class ValidationResult:
    problems: list[str] = field(default_factory=list)
    labels: list[ExpectedLabel] = field(default_factory=list)
    total_bytes: int = 0

    @property
    def ok(self) -> bool:
        return not self.problems


def _check_manifest(root: Path, result: ValidationResult) -> Manifest | None:
    try:
        raw = yaml.safe_load((root / "manifest.yaml").read_text(encoding="utf-8"))
        manifest = Manifest.model_validate(raw)
    except (yaml.YAMLError, ValidationError) as exc:
        result.problems.append(f"manifest.yaml: rule manifest_invalid: {type(exc).__name__}")
        return None
    if manifest.label_schema_version != LABEL_SCHEMA_VERSION:
        result.problems.append("manifest.yaml: rule schema_version: unsupported version")
    if len(manifest.coverage_exempt) > MAX_EXEMPTIONS:
        result.problems.append(
            f"manifest.yaml: rule coverage_exempt_max: at most {MAX_EXEMPTIONS} exemptions"
        )
    for cat, reason in manifest.coverage_exempt.items():
        if cat not in Category.__members__.values():
            result.problems.append(f"manifest.yaml: rule coverage_exempt_unknown: {cat!r}")
        if not str(reason or "").strip():
            result.problems.append(f"manifest.yaml: rule coverage_exempt_reason: {cat!r}")
    return manifest


def _check_coverage(manifest: Manifest, result: ValidationResult) -> None:
    labels = result.labels
    n = len(labels)
    problems = result.problems
    if n < manifest.min_cases:
        problems.append(f"dataset: rule min_cases: {n} < {manifest.min_cases}")
    groups = Counter(
        CATEGORY_GROUP[lb.expected.category].value if lb.expected.category else OFFICIAL_KEY
        for lb in labels
    )
    for group, minimum in manifest.min_cases_per_group.items():
        if groups.get(group, 0) < minimum:
            problems.append(
                f"dataset: rule min_group_cases: {group} {groups.get(group, 0)} < {minimum}"
            )
    cats = Counter(lb.expected.category.value for lb in labels if lb.expected.category)
    for cat in Category:
        if cat.value not in manifest.coverage_exempt and cats.get(cat.value, 0) < 1:
            problems.append(f"dataset: rule category_coverage: {cat.value} has no case")
    for cat_name, minimum in manifest.min_category_cases.items():
        if cats.get(cat_name, 0) < minimum:
            problems.append(
                f"dataset: rule min_category_cases: {cat_name} {cats.get(cat_name, 0)} < {minimum}"
            )
    official = groups.get(OFFICIAL_KEY, 0)
    if official < manifest.min_official_docs:
        problems.append(
            f"dataset: rule min_official_docs: {official} < {manifest.min_official_docs}"
        )
    variants = Counter(lb.variant.value for lb in labels)
    for variant, share in manifest.min_variant_share.items():
        have = variants.get(variant.value, 0)
        if n == 0 or have / n < share:
            problems.append(
                f"dataset: rule variant_share: {variant.value} {have}/{n} < {share:.0%}"
            )
    for variant, minimum in manifest.min_variant_cases.items():
        if variants.get(variant.value, 0) < minimum:
            problems.append(f"dataset: rule variant_cases: {variant.value} < {minimum}")
    tags = Counter(t.value for lb in labels for t in lb.tags)
    for tag in manifest.required_tags:
        if tags.get(tag.value, 0) < 1:
            problems.append(f"dataset: rule required_tag: {tag.value} has no case")


def validate_dataset(name: str, paths: EvalPaths) -> tuple[ValidationResult, Manifest | None]:
    root, _private = resolve_dataset_dir(name, paths)
    result = ValidationResult()
    manifest = _check_manifest(root, result)
    cases_dir = root / "cases"
    seen: set[str] = set()
    for case_dir in (
        sorted(p for p in cases_dir.iterdir() if p.is_dir()) if cases_dir.is_dir() else []
    ):
        label, problems = load_label(case_dir)
        result.problems += problems
        for path in case_dir.iterdir():
            if path.is_file():
                size = path.stat().st_size
                result.total_bytes += size
                if size > MAX_FILE_BYTES:
                    result.problems.append(f"{case_dir.name}: rule file_size: {path.name} > 500 KB")
        if label is not None:
            if label.id in seen:
                result.problems.append(f"{label.id}: rule id_unique: duplicate case id")
            seen.add(label.id)
            result.labels.append(label)
    if result.total_bytes > MAX_DATASET_BYTES:
        result.problems.append("dataset: rule dataset_size: total > 15 MB")
    if manifest is not None:
        _check_coverage(manifest, result)
    result.problems += [str(f) for f in scan_dataset(root)]
    for tree in (paths.recordings / name, paths.baselines / name):
        result.problems += [str(f) for f in scan_tree(tree, tree.parent.parent)]
    return result, manifest


def coverage_table(result: ValidationResult, manifest: Manifest | None) -> str:
    labels = result.labels
    n = len(labels)
    lines = [f"cases: {n} · size: {result.total_bytes / 1024 / 1024:.1f} MB", ""]
    groups = Counter(
        CATEGORY_GROUP[lb.expected.category].value if lb.expected.category else OFFICIAL_KEY
        for lb in labels
    )
    mins = manifest.min_cases_per_group if manifest else {}
    lines += ["| Group | Cases | Min |", "|---|---|---|"]
    for group in sorted(set(groups) | set(mins)):
        lines.append(f"| {group} | {groups.get(group, 0)} | {mins.get(group, '')} |")
    cats = Counter(lb.expected.category.value for lb in labels if lb.expected.category)
    exempt = manifest.coverage_exempt if manifest else {}
    lines += ["", "| Category | Cases | Note |", "|---|---|---|"]
    for cat in Category:
        note = f"exempt: {exempt[cat.value]}" if cat.value in exempt else ""
        lines.append(f"| {cat.value} | {cats.get(cat.value, 0)} | {note} |")
    tags = Counter(t.value for lb in labels for t in lb.tags)
    lines += ["", "| Tag | Cases |", "|---|---|"]
    for tag in Tag:
        lines.append(f"| {tag.value} | {tags.get(tag.value, 0)} |")
    variants = Counter(lb.variant.value for lb in labels)
    lines += ["", "| Variant | Cases | Share |", "|---|---|---|"]
    for variant in Variant:
        have = variants.get(variant.value, 0)
        share = f"{have / n:.0%}" if n else "n/a"
        lines.append(f"| {variant.value} | {have} | {share} |")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None, paths: EvalPaths | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.validate", description=__doc__)
    parser.add_argument("--dataset", required=True)
    args = parser.parse_args(argv)
    paths = paths or EvalPaths()
    try:
        result, manifest = validate_dataset(args.dataset, paths)
    except UnknownDatasetError as exc:
        print(f"error: {exc.problems[0]}", file=sys.stderr)
        return 2
    for problem in result.problems:
        print(problem)
    print(coverage_table(result, manifest))
    if not result.ok:
        print(f"\nvalidate FAILED: {len(result.problems)} problem(s)")
        return 1
    print(f"\nvalidate ok: {args.dataset}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
