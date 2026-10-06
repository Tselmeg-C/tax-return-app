"""Routing file (`config/routing.yaml`), env overrides and validation.

Error messages name the setting or file key, never a value (env values may be secrets
pasted into the wrong variable).
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import yaml

from app.config import LLMSettings
from app.llm.errors import LLMNotConfigured
from app.llm.pricing import PricingTable
from app.llm.types import PdfInputMode

ROUTING_PATH = Path(__file__).resolve().parent / "config" / "routing.yaml"
PRODUCTION = "production"
# Providers that are planned but not implemented yet, with the issue that adds them.
_PLANNED_PROVIDERS = {"anthropic": "#23", "gemini": "#23", "vertex": "#23"}
_TASK_KEYS = frozenset(
    {"model", "temperature", "max_output_tokens", "timeout_s", "schema_retries", "fallback"}
)
_MODEL_KEYS = frozenset({"supports_temperature", "pdf_input", "max_output_tokens_cap"})
# Env overrides of `tasks.<task>.model`.
TASK_MODEL_ENV = {"classify": "LLM_CLASSIFY_MODEL", "extract": "LLM_EXTRACT_MODEL"}
FALLBACK_ENV = "LLM_FALLBACK_MODEL"


@dataclass(frozen=True)
class ModelRef:
    """A `provider:model` string plus where it came from (for error messages)."""

    provider: str
    model: str
    source: str  # file key or env var name, never the value

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.model}"


@dataclass(frozen=True)
class ModelCapabilities:
    supports_temperature: bool = True
    pdf_input: PdfInputMode = "native"
    max_output_tokens_cap: int = 16384


@dataclass(frozen=True)
class TaskRoute:
    task: str
    model: ModelRef
    temperature: float | None
    max_output_tokens: int
    timeout_s: float
    schema_retries: int
    fallback: tuple[ModelRef, ...]


@dataclass(frozen=True)
class RoutingConfig:
    version: int
    tasks: dict[str, TaskRoute]
    models: dict[str, ModelCapabilities]

    def route(self, task: str) -> TaskRoute:
        route = self.tasks.get(task)
        if route is None:
            raise LLMNotConfigured(detail="task is not defined in routing.yaml tasks")
        return route


def _fail(key: str, problem: str) -> LLMNotConfigured:
    return LLMNotConfigured(detail=f"{key}: {problem}")


def parse_model_ref(value: Any, source: str) -> ModelRef:
    if not isinstance(value, str):
        raise _fail(source, "must be a string provider:model")
    provider, sep, model = value.strip().partition(":")
    if not sep or not provider or not model:
        raise _fail(source, "must have the form provider:model")
    return ModelRef(provider=provider, model=model, source=source)


def _int(value: Any, key: str, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise _fail(key, f"must be an integer >= {minimum}")
    return value


def _number(value: Any, key: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        raise _fail(key, "must be a positive number")
    return float(value)


def parse_routing(raw: Any) -> RoutingConfig:
    file = "routing.yaml"
    if not isinstance(raw, dict) or set(raw) - {"version", "tasks", "models"}:
        raise _fail(file, "must be a mapping with version, tasks and models only")
    version = _int(raw.get("version"), f"{file} version", 1)
    models_raw = raw.get("models")
    if not isinstance(models_raw, dict):
        raise _fail(f"{file} models", "must be a mapping")
    models: dict[str, ModelCapabilities] = {}
    for name, entry in models_raw.items():
        key = f"{file} models.{name}"
        if not isinstance(entry, dict) or set(entry) - _MODEL_KEYS:
            raise _fail(key, "must be a mapping of known capability keys")
        supports_temperature = entry.get("supports_temperature", True)
        if not isinstance(supports_temperature, bool):
            raise _fail(f"{key}.supports_temperature", "must be true or false")
        pdf_input = entry.get("pdf_input", "native")
        if pdf_input not in ("native", "rasterize"):
            raise _fail(f"{key}.pdf_input", "must be native or rasterize")
        models[str(name)] = ModelCapabilities(
            supports_temperature=supports_temperature,
            pdf_input=pdf_input,
            max_output_tokens_cap=_int(
                entry.get("max_output_tokens_cap", 16384), f"{key}.max_output_tokens_cap", 1
            ),
        )
    tasks_raw = raw.get("tasks")
    if not isinstance(tasks_raw, dict):
        raise _fail(f"{file} tasks", "must be a mapping")
    tasks: dict[str, TaskRoute] = {}
    for task, entry in tasks_raw.items():
        key = f"{file} tasks.{task}"
        if not isinstance(entry, dict) or set(entry) - _TASK_KEYS:
            raise _fail(key, "must be a mapping of known task keys")
        temperature = entry.get("temperature")
        if temperature is not None and (
            isinstance(temperature, bool) or not isinstance(temperature, int | float)
        ):
            raise _fail(f"{key}.temperature", "must be a number or null")
        fallback_raw = entry.get("fallback", [])
        if not isinstance(fallback_raw, list):
            raise _fail(f"{key}.fallback", "must be a list")
        tasks[str(task)] = TaskRoute(
            task=str(task),
            model=parse_model_ref(entry.get("model"), f"{key}.model"),
            temperature=None if temperature is None else float(temperature),
            max_output_tokens=_int(entry.get("max_output_tokens"), f"{key}.max_output_tokens", 1),
            timeout_s=_number(entry.get("timeout_s"), f"{key}.timeout_s"),
            schema_retries=_int(entry.get("schema_retries", 1), f"{key}.schema_retries", 0),
            fallback=tuple(
                parse_model_ref(item, f"{key}.fallback[{i}]") for i, item in enumerate(fallback_raw)
            ),
        )
    return RoutingConfig(version=version, tasks=tasks, models=models)


def load_routing(path: Path = ROUTING_PATH) -> RoutingConfig:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        raise _fail("routing.yaml", "cannot be read or parsed") from None
    return parse_routing(raw)


def apply_env_overrides(routing: RoutingConfig, settings: LLMSettings) -> RoutingConfig:
    """`LLM_<TASK>_MODEL` replaces a task's model; `LLM_FALLBACK_MODEL` every fallback list."""
    tasks = dict(routing.tasks)
    for task, env in TASK_MODEL_ENV.items():
        value = getattr(settings, env.lower())
        if value and task in tasks:
            tasks[task] = replace(tasks[task], model=parse_model_ref(value, env))
    fallback_value: str = settings.llm_fallback_model
    if fallback_value:
        if fallback_value.lower() == "none":
            fallback: tuple[ModelRef, ...] = ()
        else:
            items = [item.strip() for item in fallback_value.split(",")]
            if any(not item for item in items):
                raise _fail(FALLBACK_ENV, "must be a comma-separated list of provider:model")
            fallback = tuple(parse_model_ref(item, FALLBACK_ENV) for item in items)
        tasks = {name: replace(route, fallback=fallback) for name, route in tasks.items()}
    return replace(routing, tasks=tasks)


def validate_model_ref(
    ref: ModelRef,
    *,
    providers: Collection[str],
    app_env: str,
    routing: RoutingConfig,
    pricing: PricingTable | None,
) -> None:
    """Checks one routed model. `pricing=None` skips the capability/price checks (eval override)."""
    if ref.provider in _PLANNED_PROVIDERS and ref.provider not in providers:
        raise _fail(
            ref.source,
            f"provider {ref.provider} is not available yet "
            f"(arrives with {_PLANNED_PROVIDERS[ref.provider]})",
        )
    if ref.provider not in providers:
        raise _fail(ref.source, "names a provider that is not registered")
    if ref.provider == "fake" and app_env.strip().lower() == PRODUCTION:
        raise _fail(ref.source, "the fake provider is refused when APP_ENV=production")
    if pricing is None:
        return
    if ref.key not in routing.models:
        raise _fail(ref.source, "model has no entry in routing.yaml models")
    if not pricing.has(ref.key):
        raise _fail(ref.source, "model has no entry in pricing.yaml models")


def validate_routing(
    routing: RoutingConfig,
    *,
    pricing: PricingTable,
    providers: Collection[str],
    app_env: str,
) -> None:
    """Every routed / fallback model: registered provider, capabilities and a price."""
    for route in routing.tasks.values():
        for ref in (route.model, *route.fallback):
            validate_model_ref(
                ref, providers=providers, app_env=app_env, routing=routing, pricing=pricing
            )


def capabilities_for(routing: RoutingConfig, ref: ModelRef) -> ModelCapabilities:
    return routing.models.get(ref.key, ModelCapabilities())
