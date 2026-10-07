"""Pydantic schema → strict JSON schema, and output validation (shared by all providers).

Strict structured output needs: every property required, `additionalProperties: false` on
every object, optional fields expressed as nullable. Types that cannot be carried safely
(money as `Decimal` / `float`, free-form `dict`, `Any`) raise `LLMSchemaUnsupported` naming
the field path. LLM schemas carry money as strings with a pattern, e.g. `^-?\\d+\\.\\d{2}$`.
"""

from __future__ import annotations

import re
import types
from collections.abc import Mapping
from decimal import Decimal
from typing import Annotated, Any, Union, get_args, get_origin

from pydantic import BaseModel, ValidationError

from app.llm.errors import LLMSchemaUnsupported, LLMSchemaValidationError
from app.llm.types import T

_UNSUPPORTED_SCALARS: tuple[type, ...] = (float, Decimal, bytes, complex, set, frozenset)
_NAME = re.compile(r"[^a-zA-Z0-9_-]")
# Keywords strict mode does not accept or that only add noise.
_DROP_KEYWORDS = frozenset({"default", "title", "examples"})


def _check_annotation(annotation: Any, path: str, seen: set[type]) -> None:
    if annotation is Any or annotation is object:
        raise LLMSchemaUnsupported(detail=f"field {path} has type Any")
    origin = get_origin(annotation)
    if origin is Annotated:
        _check_annotation(get_args(annotation)[0], path, seen)
        return
    if origin in (Union, types.UnionType):
        for arg in get_args(annotation):
            if arg is not type(None):
                _check_annotation(arg, path, seen)
        return
    if origin in (list, tuple):
        for arg in get_args(annotation):
            if arg is not Ellipsis:
                _check_annotation(arg, f"{path}[]", seen)
        return
    if origin is not None and isinstance(origin, type) and issubclass(origin, Mapping):
        raise LLMSchemaUnsupported(detail=f"field {path} is a free-form mapping")
    if origin is not None:
        return  # Literal[...] and similar
    if isinstance(annotation, type):
        if issubclass(annotation, bool):
            return
        if issubclass(annotation, _UNSUPPORTED_SCALARS):
            raise LLMSchemaUnsupported(
                detail=f"field {path} has type {annotation.__name__} (carry money as a string)"
            )
        if issubclass(annotation, Mapping):
            raise LLMSchemaUnsupported(detail=f"field {path} is a free-form mapping")
        if issubclass(annotation, BaseModel):
            _check_model(annotation, path, seen)


def _check_model(model: type[BaseModel], prefix: str, seen: set[type]) -> None:
    if model in seen:
        return
    seen.add(model)
    for name, info in model.model_fields.items():
        path = f"{prefix}.{name}" if prefix else name
        _check_annotation(info.annotation, path, seen)


def _strictify(node: Any) -> Any:
    if isinstance(node, list):
        return [_strictify(item) for item in node]
    if not isinstance(node, dict):
        return node
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key in ("properties", "$defs") and isinstance(value, dict):
            # Maps of names (a field may be called "title"): keep every key.
            out[key] = {name: _strictify(sub) for name, sub in value.items()}
        elif key not in _DROP_KEYWORDS:
            out[key] = _strictify(value)
    if out.get("type") == "object" or "properties" in out:
        properties = out.get("properties", {})
        out["type"] = "object"
        out["properties"] = properties
        out["required"] = list(properties)
        out["additionalProperties"] = False
    # A `$ref` cannot carry sibling keywords in strict mode.
    if "$ref" in out and len(out) > 1:
        out = {"$ref": out["$ref"]}
    return out


def schema_name(schema: type[BaseModel]) -> str:
    return _NAME.sub("_", schema.__name__)[:64] or "Output"


def strict_json_schema(schema: type[BaseModel]) -> dict[str, Any]:
    """Strict JSON schema for `schema`, or `LLMSchemaUnsupported` naming the field path."""
    _check_model(schema, "", set())
    try:
        raw = schema.model_json_schema(mode="validation")
    except Exception as exc:
        raise LLMSchemaUnsupported(detail="schema cannot be rendered as JSON schema") from exc
    result = _strictify(raw)
    if not isinstance(result, dict) or result.get("type") != "object":
        raise LLMSchemaUnsupported(detail="schema root must be an object")
    return result


def validate_output(
    schema: type[T], raw_text: str, *, provider: str, model: str, request_id: str | None
) -> T:
    """Validate the model's text; errors carry pydantic error types and a count only."""
    if not raw_text.strip():
        raise LLMSchemaValidationError(
            provider=provider,
            model=model,
            request_id=request_id,
            error_types=("empty_output",),
            error_count=1,
        )
    try:
        return schema.model_validate_json(raw_text)
    except ValidationError as exc:
        errors = exc.errors(include_url=False, include_context=False, include_input=False)
        raise LLMSchemaValidationError(
            provider=provider,
            model=model,
            request_id=request_id,
            error_types=tuple(sorted({str(e["type"]) for e in errors})),
            error_count=len(errors),
        ) from None
