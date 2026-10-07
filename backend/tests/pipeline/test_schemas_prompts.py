"""LLM schemas, prompt rendering and the prompt hash lock (#9)."""

from __future__ import annotations

import json
import shutil
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.domain.enums import LABELS_DE, Category, DocType, PaymentMethod
from app.llm.schema import strict_json_schema
from app.pipeline.prompts import (
    LOCK_PATH,
    PROMPTS_DIR,
    PromptError,
    available_versions,
    load_prompt_set,
    lock_problems,
    read_lock,
    resolve_version,
)
from app.pipeline.schemas import ClassifyOutput, GenericBillExtraction, LineItem, money

SCHEMAS = (ClassifyOutput, GenericBillExtraction)


def _walk(node: Any) -> list[dict[str, Any]]:
    if isinstance(node, dict):
        return [node, *(n for v in node.values() for n in _walk(v))]
    if isinstance(node, list):
        return [n for v in node for n in _walk(v)]
    return []


@pytest.mark.parametrize("schema", SCHEMAS, ids=lambda s: s.__name__)
def test_strict_schema_and_enums(schema: Any) -> None:
    js = strict_json_schema(schema)  # raises LLMSchemaUnsupported if not expressible
    nodes = _walk(js)
    assert not [n for n in nodes if n.get("type") == "number"]
    enums = {tuple(n["enum"]) for n in nodes if "enum" in n}
    if schema is GenericBillExtraction:
        assert tuple(c.value for c in Category) in enums
        assert tuple(p.value for p in PaymentMethod) in enums
    else:
        assert tuple(d.value for d in DocType) in enums


def _line(amount: str) -> dict[str, str]:
    return {
        "description": "x",
        "gross_amount": amount,
        "category": "irrelevant",
        "cost_kind_35a": "not_applicable",
    }


@pytest.mark.parametrize(("raw", "value"), [("12.30", "12.30"), ("-5.00", "-5.00")])
def test_money_parses(raw: str, value: str) -> None:
    assert money(LineItem.model_validate(_line(raw)).gross_amount) == Decimal(value)


@pytest.mark.parametrize("raw", ["12.3", "1,234.00", "12", "1234567890.00", "12.300"])
def test_money_rejected(raw: str) -> None:
    with pytest.raises(ValidationError):
        LineItem.model_validate(_line(raw))


def test_free_text_is_capped() -> None:
    item = LineItem.model_validate({**_line("1.00"), "description": "y" * 500})
    assert len(item.description) == 120


def test_lock_matches() -> None:
    assert lock_problems() == []
    assert set(read_lock()) == set(available_versions())


def test_changed_prompt_fails_the_lock(tmp_path: Path) -> None:
    root = tmp_path / "prompts"
    shutil.copytree(PROMPTS_DIR / "v1", root / "v1")
    path = root / "v1" / "classify.md"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("household", "hausehold", 1), encoding="utf-8")
    problems = lock_problems(root, LOCK_PATH)
    assert len(problems) == 1 and "v1" in problems[0] and "create prompts/v2" in problems[0]


@pytest.mark.parametrize("task", ["classify", "extract"])
def test_rendered_prompts_list_every_category(task: str) -> None:
    ps = load_prompt_set("v1")
    system = getattr(ps, task).system
    assert "{{" not in system
    for category in Category:
        assert f"`{category.value}`" in system
        assert LABELS_DE[Category][category] in system


def test_resolve_version() -> None:
    assert resolve_version("") == available_versions()[-1]
    assert resolve_version("v1") == "v1"
    with pytest.raises(PromptError, match="PIPELINE_PROMPT_VERSION"):
        resolve_version("v9")


def test_schema_json_is_stable() -> None:
    # The hash covers the strict schema: it must be deterministic.
    a = json.dumps(strict_json_schema(GenericBillExtraction), sort_keys=True)
    b = json.dumps(strict_json_schema(GenericBillExtraction), sort_keys=True)
    assert a == b
