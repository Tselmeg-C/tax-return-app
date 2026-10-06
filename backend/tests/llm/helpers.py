"""Shared helpers for the LLM tests: schemas, settings, configs, fixtures, fake clock."""

from __future__ import annotations

import copy
import datetime
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import httpx
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import BaseModel, Field, SecretStr

from app.config import LLMSettings
from app.llm.errors import LLMError
from app.llm.fake import FakeProvider, FakeReply
from app.llm.pricing import PricingTable, parse_pricing
from app.llm.provider import LLMProvider
from app.llm.router import LLMRouter
from app.llm.routing import RoutingConfig, parse_routing
from app.llm.telemetry import LLMTelemetry
from app.observability.scrub import ScrubbingSpanProcessor

FIXTURES = Path(__file__).resolve().parent / "fixtures"
OPENAI_FIXTURES = FIXTURES / "openai"
RESPONSES_URL = "https://api.openai.com/v1/responses"
MONEY = r"^-?\d+\.\d{2}$"


class Kind(StrEnum):
    receipt = "receipt"
    invoice = "invoice"


class Item(BaseModel):
    name: str
    amount: str = Field(pattern=MONEY)


class Synthetic(BaseModel):
    vendor: str
    total: str = Field(pattern=MONEY)
    date: datetime.date
    kind: Kind
    items: list[Item]
    note: str | None


EXPECTED = Synthetic(
    vendor="Frischmarkt Muster",
    total="12.34",
    date=datetime.date(2026, 3, 14),
    kind=Kind.receipt,
    items=[Item(name="Brot", amount="2.49"), Item(name="Kaffee Muster", amount="9.85")],
    note=None,
)


def llm_settings(**values: Any) -> LLMSettings:
    """Settings from the given values only (no env file; the conftest clears LLM env vars)."""
    if "openai_api_key" in values and isinstance(values["openai_api_key"], str):
        values["openai_api_key"] = SecretStr(values["openai_api_key"])
    return LLMSettings(_env_file=None, **values)  # type: ignore[call-arg]


def _task(model: str, **overrides: Any) -> dict[str, Any]:
    task: dict[str, Any] = {
        "model": model,
        "temperature": 0,
        "max_output_tokens": 1000,
        "timeout_s": 60,
        "schema_retries": 1,
        "fallback": [],
    }
    task.update(overrides)
    return task


BASE_ROUTING: dict[str, Any] = {
    "version": 1,
    "tasks": {
        "classify": _task("fake:test"),
        "extract": _task("fake:test", max_output_tokens=4000),
    },
    "models": {
        "fake:test": {"supports_temperature": True, "pdf_input": "native", "max_output_tokens_cap": 3000},
        "fake:fallback": {"supports_temperature": True, "pdf_input": "native"},
        "fake:raster": {"supports_temperature": True, "pdf_input": "rasterize"},
        "openai:gpt-test": {"supports_temperature": True, "pdf_input": "native"},
        "openai:gpt-notemp": {"supports_temperature": False, "pdf_input": "native"},
        "openai:other": {"supports_temperature": True, "pdf_input": "native"},
    },
}

BASE_PRICING: dict[str, Any] = {
    "version": "test-2026-01-01",
    "fx": {"usd_eur": "0.9", "source": "test rate, 2026-01-01"},
    "models": {
        "fake:test": {
            "currency": "EUR",
            "input_per_1k": "0.001",
            "output_per_1k": "0.002",
            "source": "test only",
        },
        "fake:fallback": {
            "currency": "EUR",
            "input_per_1k": "0.001",
            "output_per_1k": "0.002",
            "source": "test only",
        },
        "fake:raster": {
            "currency": "EUR",
            "input_per_1k": "0.001",
            "output_per_1k": "0.002",
            "source": "test only",
        },
        "openai:gpt-test": {
            "currency": "USD",
            "input_per_1k": "0.0004",
            "cached_input_per_1k": "0.0001",
            "output_per_1k": "0.0016",
            "source": "test only",
        },
        "openai:gpt-notemp": {
            "currency": "USD",
            "input_per_1k": "0.0004",
            "output_per_1k": "0.0016",
            "source": "test only",
        },
        "openai:other": {
            "currency": "USD",
            "input_per_1k": "0.002",
            "output_per_1k": "0.008",
            "source": "test only",
        },
    },
}


def routing(**tasks: dict[str, Any]) -> RoutingConfig:
    """Test routing; `classify={"fallback": [...]}` merges into that task."""
    raw = copy.deepcopy(BASE_ROUTING)
    for name, overrides in tasks.items():
        raw["tasks"].setdefault(name, _task("fake:test")).update(overrides)
    return parse_routing(raw)


def pricing() -> PricingTable:
    return parse_pricing(copy.deepcopy(BASE_PRICING))


@dataclass
class FakeClock:
    now: float = 0.0
    sleeps: list[float] = field(default_factory=list)

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@dataclass
class Otel:
    spans: InMemorySpanExporter
    reader: InMemoryMetricReader
    telemetry: LLMTelemetry

    def finished(self) -> list[ReadableSpan]:
        return list(self.spans.get_finished_spans())

    def points(self, name: str) -> list[tuple[dict[str, Any], Any]]:
        data = self.reader.get_metrics_data()
        found: list[tuple[dict[str, Any], Any]] = []
        if data is None:
            return found
        for resource_metrics in data.resource_metrics:
            for scope in resource_metrics.scope_metrics:
                for metric in scope.metrics:
                    if metric.name != name:
                        continue
                    for point in metric.data.data_points:
                        value = getattr(point, "value", None)
                        if value is None:
                            value = getattr(point, "count", None)
                        found.append((dict(point.attributes or {}), value))
        return found

    def total(self, name: str, **attrs: Any) -> Any:
        return sum(
            value
            for point_attrs, value in self.points(name)
            if all(point_attrs.get(k) == v for k, v in attrs.items())
        )

    def all_metric_attribute_keys(self) -> set[str]:
        keys: set[str] = set()
        data = self.reader.get_metrics_data()
        if data is None:
            return keys
        for resource_metrics in data.resource_metrics:
            for scope in resource_metrics.scope_metrics:
                for metric in scope.metrics:
                    for point in metric.data.data_points:
                        keys.update((point.attributes or {}).keys())
        return keys

    def all_metric_attribute_values(self) -> list[str]:
        values: list[str] = []
        data = self.reader.get_metrics_data()
        if data is None:
            return values
        for resource_metrics in data.resource_metrics:
            for scope in resource_metrics.scope_metrics:
                for metric in scope.metrics:
                    for point in metric.data.data_points:
                        values.extend(str(v) for v in (point.attributes or {}).values())
        return values


def make_otel() -> Otel:
    exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(ScrubbingSpanProcessor(SimpleSpanProcessor(exporter)))
    reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[reader])
    return Otel(exporter, reader, LLMTelemetry(tracer_provider, meter_provider))


def make_router(
    *,
    providers: dict[str, LLMProvider] | None = None,
    script: Sequence[FakeReply | LLMError] | None = None,
    settings: LLMSettings | None = None,
    routing_config: RoutingConfig | None = None,
    otel: Otel | None = None,
    clock: FakeClock | None = None,
    rng: float = 0.5,
) -> LLMRouter:
    clock = clock or FakeClock()
    if providers is None:
        providers = {"fake": FakeProvider(script or (), app_env="test")}
    return LLMRouter(
        settings or llm_settings(),
        routing=routing_config or routing(),
        pricing=pricing(),
        providers=providers,
        telemetry=(otel or make_otel()).telemetry,
        sleep=clock.sleep,
        clock=clock,
        rng=lambda: rng,
    )


def load_fixture(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((OPENAI_FIXTURES / f"{name}.json").read_text("utf-8"))
    return data


def fixture_response(name: str) -> httpx.Response:
    fixture = load_fixture(name)
    return httpx.Response(fixture["status"], headers=fixture["headers"], json=fixture["body"])
