"""`app/tax/` stays pure: allowlisted imports only, no `open`, no floats (#14)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

TAX_DIR = Path(__file__).resolve().parents[2] / "app" / "tax"

ALLOWED = {
    "__future__",
    "decimal",
    "dataclasses",
    "enum",
    "typing",
    "collections.abc",  # #87: Callable (the injected tariff) and Iterable only
    "functools",
    "datetime",
    "pydantic",
    "app.domain.enums",
    # #9: bill_rules reads the LLM output models (pydantic + enums only, no I/O).
    "app.pipeline.schemas",
}


def violations(source: str) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            modules = [node.module or ""] if node.level == 0 else []
        else:
            modules = []
        for module in modules:
            if module not in ALLOWED and not module.startswith("app.tax."):
                found.append(f"line {node.lineno}: import {module}")
        if isinstance(node, ast.Constant) and isinstance(node.value, float):
            found.append(f"line {node.lineno}: float literal {node.value!r}")
        if isinstance(node, ast.Name) and node.id in ("float", "open"):
            found.append(f"line {node.lineno}: uses {node.id}")
    return found


MODULES = sorted(TAX_DIR.rglob("*.py"))


def test_tax_package_has_modules() -> None:
    assert {p.name for p in MODULES} >= {"__init__.py", "models.py", "bill_rules.py", "mapping.py"}
    names = {p.relative_to(TAX_DIR).as_posix() for p in MODULES}
    assert names >= {  # #15
        "deductions/__init__.py",
        "deductions/models.py",
        "deductions/select.py",
        "deductions/werbungskosten.py",
        "deductions/sonderausgaben.py",
        "deductions/vorsorge.py",  # #16
    }
    assert "progression.py" in names  # #86
    assert {"kinder.py", "assessment.py"} <= names  # #87


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.relative_to(TAX_DIR).as_posix())
def test_module_is_pure(path: Path) -> None:
    assert violations(path.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    "snippet",
    [
        "import os",
        "from pathlib import Path",
        "import yaml",
        "from app.tax_params import load_params",
        "x = 0.5",
        "y = float('1')",
        "z = open('f')",
    ],
)
def test_checker_catches(snippet: str) -> None:
    assert violations(snippet)
