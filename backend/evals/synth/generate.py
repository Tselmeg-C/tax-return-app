"""Render a dataset from its generator spec (`specs/<dataset>.yaml`) and check committed files.

The spec is the single source of truth: per case the template, the values printed on it,
the rendering variant and the expected label. `label.yaml` is written from the spec.
"""

from __future__ import annotations

import hashlib
import io
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pypdf import PdfReader

from evals.dataset import format_validation_error
from evals.paths import EvalPaths
from evals.schema import (
    LABEL_SCHEMA_VERSION,
    VARIANT_EXTENSIONS,
    ExpectedFields,
    ExpectedLabel,
    Source,
    Tag,
    Variant,
)
from evals.synth import raster
from evals.synth.templates import TEMPLATES, TemplateInput


class SpecError(Exception):
    def __init__(self, problems: list[str]) -> None:
        super().__init__(f"{len(problems)} spec problem(s)")
        self.problems = problems


class CaseSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    template: str
    variant: Variant
    tags: list[Tag] = Field(default_factory=list)
    source: Source = Source.SYNTHETIC
    doc: dict[str, Any] = Field(default_factory=dict)
    expected: dict[str, Any]
    notes: str | None = None


class DatasetSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seed: int
    manifest: dict[str, Any]
    cases: list[CaseSpec]


def load_spec(path: Path) -> DatasetSpec:
    with path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    try:
        return DatasetSpec.model_validate(raw)
    except ValidationError as exc:
        raise SpecError(format_validation_error("spec", exc)) from None


def case_seed(global_seed: int, case_id: str) -> int:
    digest = hashlib.sha256(f"{global_seed}:{case_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def _label_yaml(label: ExpectedLabel) -> bytes:
    data = label.model_dump(mode="json", exclude_none=False)
    exp = label.expected
    # Keep dates as YAML dates (not strings) and amounts as quoted strings.
    data["expected"]["invoice_date"] = exp.invoice_date
    data["expected"]["payment_date"] = exp.payment_date
    if data.get("notes") is None:
        data.pop("notes")
    text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)
    return text.encode("utf-8")


@dataclass(frozen=True)
class RenderedCase:
    case_id: str
    files: dict[str, bytes]  # file name -> bytes


def render_case(spec: CaseSpec, global_seed: int) -> RenderedCase:
    problems: list[str] = []
    try:
        expected = ExpectedFields.model_validate({"labour_share_35a": None, **spec.expected})
    except ValidationError as exc:
        raise SpecError(format_validation_error(spec.id, exc)) from None
    template = TEMPLATES.get(spec.template)
    if template is None:
        raise SpecError([f"{spec.id}: rule unknown_template: {spec.template!r}"])
    seed = case_seed(global_seed, spec.id)
    rendered = template(TemplateInput(spec.id, spec.doc, expected, seed))
    if (
        rendered.total is not None
        and expected.gross_amount is not None
        and rendered.total != expected.gross_amount
    ):
        problems.append(f"{spec.id}: rule printed_total: printed total != expected.gross_amount")
    if problems:
        raise SpecError(problems)
    ext = VARIANT_EXTENSIONS[spec.variant]
    if spec.variant is Variant.PDF_TEXT:
        doc_bytes = rendered.pdf
    elif spec.variant is Variant.PDF_SCANNED:
        doc_bytes = raster.scanned_pdf(rendered.pdf, seed)
    elif spec.variant is Variant.PHOTO_JPEG:
        doc_bytes = raster.photo_jpeg(rendered.pdf, seed)
    else:
        doc_bytes = raster.png(rendered.pdf, seed)
    try:
        label = ExpectedLabel(
            schema_version=LABEL_SCHEMA_VERSION,
            id=spec.id,
            source=spec.source,
            file=f"document{ext}",
            variant=spec.variant,
            tags=spec.tags,
            expected=expected,
            notes=spec.notes,
        )
    except ValidationError as exc:
        raise SpecError(format_validation_error(spec.id, exc)) from None
    return RenderedCase(spec.id, {f"document{ext}": doc_bytes, "label.yaml": _label_yaml(label)})


def manifest_yaml(spec: DatasetSpec) -> bytes:
    return yaml.safe_dump(spec.manifest, sort_keys=False, allow_unicode=True, width=100).encode()


def write_case(case: RenderedCase, cases_dir: Path) -> None:
    case_dir = cases_dir / case.case_id
    if case_dir.exists():
        shutil.rmtree(case_dir)
    case_dir.mkdir(parents=True)
    for name, data in case.files.items():
        (case_dir / name).write_bytes(data)


def generate(
    dataset: str, paths: EvalPaths, only: list[str] | None = None, out_dir: Path | None = None
) -> list[str]:
    """Write the dataset (or only some cases). Returns the case ids written."""
    spec = load_spec(paths.specs / f"{dataset}.yaml")
    target = out_dir or (paths.datasets / dataset)
    cases_dir = target / "cases"
    cases_dir.mkdir(parents=True, exist_ok=True)
    selected = _select(spec, only)
    rendered = [render_case(c, spec.seed) for c in selected]
    for case in rendered:
        write_case(case, cases_dir)
    (target / "manifest.yaml").write_bytes(manifest_yaml(spec))
    if only is None:
        wanted = {c.id for c in spec.cases}
        for stale in cases_dir.iterdir():
            if stale.is_dir() and stale.name not in wanted:
                shutil.rmtree(stale)
    return [c.case_id for c in rendered]


def _select(spec: DatasetSpec, only: list[str] | None) -> list[CaseSpec]:
    ids = [c.id for c in spec.cases]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise SpecError([f"{d}: rule id_unique: duplicate case id in spec" for d in dupes])
    if only is None:
        return spec.cases
    unknown = sorted(set(only) - set(ids))
    if unknown:
        raise SpecError([f"{u}: rule unknown_case: not in spec" for u in unknown])
    return [c for c in spec.cases if c.id in only]


# --- check ------------------------------------------------------------------------------


def _pdf_text(data: bytes) -> str:
    return "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(data)).pages)


def _image_size(data: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(data)) as img:
        return img.size


def _equivalent(name: str, fresh: bytes, committed: bytes) -> bool:
    """Fallback comparison for raster bytes (see README: `--check`)."""
    if name.endswith(".pdf"):
        fresh_reader = PdfReader(io.BytesIO(fresh))
        committed_reader = PdfReader(io.BytesIO(committed))
        return len(fresh_reader.pages) == len(committed_reader.pages) and _pdf_text(
            fresh
        ) == _pdf_text(committed)
    if name.endswith((".jpg", ".png")):
        return _image_size(fresh) == _image_size(committed)
    return False


@dataclass
class CheckResult:
    differing: list[str]
    fallback_used: list[str]
    missing: list[str]
    extra: list[str]

    @property
    def ok(self) -> bool:
        return not (self.differing or self.missing or self.extra)


def check(dataset: str, paths: EvalPaths, only: list[str] | None = None) -> CheckResult:
    """Render into a temp dir and compare sha256 per file with the committed files.

    Exact bytes are expected (locked versions, fixed seeds). If only raster bytes differ
    while `label.yaml` is identical and the PDF text layer / image dimensions match, the
    case passes via the documented fallback and is listed in `fallback_used`.
    """
    spec = load_spec(paths.specs / f"{dataset}.yaml")
    committed_root = paths.datasets / dataset
    result = CheckResult([], [], [], [])
    selected = _select(spec, only)
    with tempfile.TemporaryDirectory(prefix="evals-synth-check-") as tmp:
        tmp_root = Path(tmp)
        for case_spec in selected:
            try:
                case = render_case(case_spec, spec.seed)
            except SpecError:
                result.differing.append(case_spec.id)
                continue
            write_case(case, tmp_root / "cases")
            committed_dir = committed_root / "cases" / case.case_id
            if not committed_dir.is_dir():
                result.missing.append(case.case_id)
                continue
            committed_names = {p.name for p in committed_dir.iterdir() if p.is_file()}
            if committed_names != set(case.files):
                result.differing.append(case.case_id)
                continue
            fallback = False
            same = True
            for name, fresh in case.files.items():
                old = (committed_dir / name).read_bytes()
                if hashlib.sha256(old).digest() == hashlib.sha256(fresh).digest():
                    continue
                if name != "label.yaml" and _equivalent(name, fresh, old):
                    fallback = True
                    continue
                same = False
            if not same:
                result.differing.append(case.case_id)
            elif fallback:
                result.fallback_used.append(case.case_id)
        if only is None:
            wanted = {c.id for c in spec.cases}
            cases_dir = committed_root / "cases"
            if cases_dir.is_dir():
                result.extra = sorted(
                    p.name for p in cases_dir.iterdir() if p.is_dir() and p.name not in wanted
                )
            manifest = committed_root / "manifest.yaml"
            if not manifest.is_file() or manifest.read_bytes() != manifest_yaml(spec):
                result.differing.append("manifest.yaml")
    return result
