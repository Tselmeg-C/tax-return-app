"""Load and hash an eval dataset.

Layout: `<datasets dir>/<name>/manifest.yaml` and `cases/<case_id>/{document.*, label.yaml}`.
`EvalCase` is what a predictor sees: it has no label attribute. Labels stay in `Dataset.labels`
and are only handed to the scorer (and to `oracle` through its own constructor).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from evals.paths import EvalPaths
from evals.schema import (
    LABEL_SCHEMA_VERSION,
    MIME_TYPES,
    VARIANT_EXTENSIONS,
    ExpectedLabel,
    Tag,
    Variant,
)

MAX_FILE_BYTES = 500 * 1024
MAX_DATASET_BYTES = 15 * 1024 * 1024

MAGIC: dict[str, tuple[bytes, ...]] = {
    ".pdf": (b"%PDF-",),
    ".jpg": (b"\xff\xd8\xff",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
}


class DatasetError(Exception):
    """A dataset cannot be loaded. `problems` are safe to print (case id + rule only)."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__(f"{len(problems)} dataset problem(s)")
        self.problems = problems


class UnknownDatasetError(DatasetError):
    pass


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    label_schema_version: int
    coverage_exempt: dict[str, str] = Field(default_factory=dict)
    min_cases_per_group: dict[str, int] = Field(default_factory=dict)
    min_variant_share: dict[Variant, float] = Field(default_factory=dict)
    min_variant_cases: dict[Variant, int] = Field(default_factory=dict)
    min_cases: int = 0
    min_category_cases: dict[str, int] = Field(default_factory=dict)
    min_official_docs: int = 0
    required_tags: list[Tag] = Field(default_factory=list)


@dataclass(frozen=True)
class EvalCase:
    """One document as a predictor sees it. Deliberately has no label."""

    id: str
    path: Path
    mime_type: str
    variant: str
    tags: tuple[str, ...]

    def read_bytes(self) -> bytes:
        return self.path.read_bytes()


@dataclass
class Dataset:
    name: str
    root: Path
    manifest: Manifest
    cases: list[EvalCase]
    labels: dict[str, ExpectedLabel] = field(repr=False)
    hash: str
    private: bool


def format_validation_error(case_id: str, exc: ValidationError) -> list[str]:
    """One line per error: case id, field path and rule. Never the input value."""
    lines = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "<root>"
        lines.append(f"{case_id}: {loc}: rule {err['type']}: {err['msg']}")
    return lines


def dataset_hash(root: Path) -> str:
    """sha256 over the sorted `(relative path, sha256 of file)` list of every file in cases/."""
    cases_dir = root / "cases"
    entries = []
    for path in sorted(p for p in cases_dir.rglob("*") if p.is_file()):
        rel = path.relative_to(cases_dir).as_posix()
        entries.append(f"{rel}\0{hashlib.sha256(path.read_bytes()).hexdigest()}\n")
    return hashlib.sha256("".join(sorted(entries)).encode()).hexdigest()


def resolve_dataset_dir(name: str, paths: EvalPaths) -> tuple[Path, bool]:
    """Return (directory, is_private). Raises `UnknownDatasetError`."""
    if not name or "/" in name or name.startswith("."):
        raise UnknownDatasetError([f"unknown dataset {name!r}"])
    public = paths.datasets / name
    if (public / "manifest.yaml").is_file():
        return public, False
    private = paths.datasets_private / name
    if (private / "manifest.yaml").is_file():
        return private, True
    raise UnknownDatasetError([f"unknown dataset {name!r} (no manifest.yaml)"])


def _load_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def check_file(case_dir: Path, label: ExpectedLabel) -> list[str]:
    path = case_dir / label.file
    if not path.is_file():
        return [f"{label.id}: file: rule file_missing: {label.file} does not exist"]
    ext = VARIANT_EXTENSIONS[Variant(label.variant)]
    head = path.read_bytes()[:16]
    if not any(head.startswith(m) for m in MAGIC[ext]):
        return [f"{label.id}: file: rule magic_bytes: content does not match {ext} / variant"]
    return []


def load_label(case_dir: Path) -> tuple[ExpectedLabel | None, list[str]]:
    case_id = case_dir.name
    label_path = case_dir / "label.yaml"
    if not label_path.is_file():
        return None, [f"{case_id}: label.yaml: rule label_missing: no label.yaml"]
    try:
        raw = _load_yaml(label_path)
    except yaml.YAMLError:
        return None, [f"{case_id}: label.yaml: rule yaml_syntax: not valid YAML"]
    try:
        label = ExpectedLabel.model_validate(raw)
    except ValidationError as exc:
        return None, format_validation_error(case_id, exc)
    problems = []
    if label.id != case_id:
        problems.append(f"{case_id}: id: rule id_matches_dir: id differs from directory name")
    problems += check_file(case_dir, label)
    return label, problems


def load_dataset(name: str, paths: EvalPaths | None = None) -> Dataset:
    """Load and validate every label. Raises `DatasetError` listing every problem."""
    paths = paths or EvalPaths()
    root, private = resolve_dataset_dir(name, paths)
    problems: list[str] = []
    try:
        manifest = Manifest.model_validate(_load_yaml(root / "manifest.yaml"))
    except (ValidationError, yaml.YAMLError) as exc:
        raise DatasetError(
            [f"manifest.yaml: rule manifest_invalid: {type(exc).__name__}"]
        ) from None
    if manifest.label_schema_version != LABEL_SCHEMA_VERSION:
        problems.append("manifest.yaml: rule schema_version: unsupported label_schema_version")
    cases: list[EvalCase] = []
    labels: dict[str, ExpectedLabel] = {}
    cases_dir = root / "cases"
    for case_dir in sorted(p for p in cases_dir.iterdir() if p.is_dir()):
        label, case_problems = load_label(case_dir)
        problems += case_problems
        if label is None:
            continue
        if label.id in labels:
            problems.append(f"{label.id}: id: rule id_unique: duplicate case id")
            continue
        labels[label.id] = label
        ext = VARIANT_EXTENSIONS[Variant(label.variant)]
        cases.append(
            EvalCase(
                id=label.id,
                path=case_dir / label.file,
                mime_type=MIME_TYPES[ext],
                variant=label.variant.value,
                tags=tuple(t.value for t in label.tags),
            )
        )
    if problems:
        raise DatasetError(problems)
    return Dataset(
        name=name,
        root=root,
        manifest=manifest,
        cases=cases,
        labels=labels,
        hash=dataset_hash(root),
        private=private,
    )
