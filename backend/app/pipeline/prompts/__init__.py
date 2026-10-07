"""Versioned prompt sets (`prompts/vN/`), rendering and the hash lock (`LOCK.yaml`).

A prompt set is a directory: `classify.md`, `extract_generic_bill.md` (YAML front matter +
system prompt) and `guidance.yaml` (one line per enum code). `{{categories}}`,
`{{doc_types}}` and `{{payment_methods}}` are rendered from the enums and `LABELS_DE`, so an
enum change shows up in the rendered prompt and its hash. `prompt_sha256` covers the
rendered prompts plus the strict JSON schemas of the output models; `LOCK.yaml` pins it per
version, and an existing version is never edited (create `vN+1` and run the eval).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from functools import cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from app.domain.enums import LABELS_DE, Category, DocType, PaymentMethod
from app.llm.schema import strict_json_schema
from app.pipeline.schemas import ClassifyOutput, GenericBillExtraction

PROMPTS_DIR = Path(__file__).resolve().parent
LOCK_PATH = PROMPTS_DIR / "LOCK.yaml"
VERSION_RE = re.compile(r"^v([1-9][0-9]*)$")
SCHEMAS: dict[str, type[BaseModel]] = {
    "ClassifyOutput": ClassifyOutput,
    "GenericBillExtraction": GenericBillExtraction,
}
FILES = {"classify": "classify.md", "extract": "extract_generic_bill.md"}
_BLOCK = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")


class PromptError(ValueError):
    """A prompt set is missing, malformed or does not match `LOCK.yaml` (names the version)."""


@dataclass(frozen=True)
class Prompt:
    task: str
    schema: type[BaseModel]
    system: str = field(repr=False)


@dataclass(frozen=True)
class PromptSet:
    version: str
    classify: Prompt
    extract: Prompt
    sha256: str


def available_versions(root: Path = PROMPTS_DIR) -> list[str]:
    found = [p.name for p in root.iterdir() if p.is_dir() and VERSION_RE.match(p.name)]
    return sorted(found, key=lambda v: int(v[1:]))


def resolve_version(setting: str, root: Path = PROMPTS_DIR) -> str:
    """`PIPELINE_PROMPT_VERSION` (empty = the highest version) → an existing version."""
    versions = available_versions(root)
    if not setting:
        if not versions:
            raise PromptError("no prompt set found")
        return versions[-1]
    if setting not in versions:
        raise PromptError(f"PIPELINE_PROMPT_VERSION: unknown prompt version {setting!r}")
    return setting


def _block(enum_cls: type[StrEnum], guidance: dict[str, str]) -> str:
    labels = LABELS_DE[enum_cls]
    lines = []
    for member in enum_cls:
        lines.append(f"- `{member.value}` – {labels[member]}: {guidance[member.value]}")
    return "\n".join(lines)


def _render(body: str, blocks: dict[str, str], version: str) -> str:
    def sub(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in blocks:
            raise PromptError(f"{version}: unknown block {{{{{name}}}}}")
        return blocks[name]

    return _BLOCK.sub(sub, body).strip() + "\n"


def _front_matter(text: str, version: str, name: str) -> tuple[dict[str, Any], str]:
    parts = text.split("---\n", 2)
    if len(parts) != 3 or parts[0].strip():
        raise PromptError(f"{version}/{name}: missing YAML front matter")
    meta = yaml.safe_load(parts[1])
    if not isinstance(meta, dict):
        raise PromptError(f"{version}/{name}: front matter must be a mapping")
    return meta, parts[2]


def load_prompt_set(version: str, root: Path = PROMPTS_DIR) -> PromptSet:
    directory = root / version
    if not VERSION_RE.match(version) or not directory.is_dir():
        raise PromptError(f"unknown prompt version {version!r}")
    guidance = yaml.safe_load((directory / "guidance.yaml").read_text(encoding="utf-8"))
    try:
        blocks = {
            "categories": _block(Category, guidance["categories"]),
            "doc_types": _block(DocType, guidance["doc_types"]),
            "payment_methods": _block(PaymentMethod, guidance["payment_methods"]),
        }
    except (KeyError, TypeError) as exc:
        raise PromptError(f"{version}/guidance.yaml: missing guidance ({exc!r})") from None
    prompts: dict[str, Prompt] = {}
    digest = hashlib.sha256()
    for task, name in FILES.items():
        meta, body = _front_matter((directory / name).read_text(encoding="utf-8"), version, name)
        if meta.get("version") != version or meta.get("task") != task:
            raise PromptError(f"{version}/{name}: front matter version / task do not match")
        schema = SCHEMAS.get(str(meta.get("schema")))
        if schema is None:
            raise PromptError(f"{version}/{name}: unknown schema")
        system = _render(body, blocks, version)
        prompts[task] = Prompt(task=task, schema=schema, system=system)
        digest.update(f"{task}\0{system}\0".encode())
        digest.update(json.dumps(strict_json_schema(schema), sort_keys=True).encode())
        digest.update(b"\0")
    return PromptSet(
        version=version,
        classify=prompts["classify"],
        extract=prompts["extract"],
        sha256=digest.hexdigest(),
    )


def read_lock(path: Path = LOCK_PATH) -> dict[str, str]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {str(k): str(v) for k, v in raw.items()}


def lock_problems(root: Path = PROMPTS_DIR, lock: Path = LOCK_PATH) -> list[str]:
    """One line per version whose recomputed hash differs from `LOCK.yaml` (or is missing)."""
    pinned = read_lock(lock)
    problems = []
    for version in available_versions(root):
        actual = load_prompt_set(version, root).sha256
        expected = pinned.get(version)
        if expected is None:
            problems.append(f"{version}: not in LOCK.yaml (add it once its eval run is done)")
        elif expected != actual:
            problems.append(
                f"prompt set {version} changed (hash differs from LOCK.yaml): existing "
                f"versions are never edited, create prompts/v{int(version[1:]) + 1} instead"
            )
    return problems


@cache
def prompt_set(version: str = "") -> PromptSet:
    """The committed prompt set for `PIPELINE_PROMPT_VERSION` (cached)."""
    return load_prompt_set(resolve_version(version))
