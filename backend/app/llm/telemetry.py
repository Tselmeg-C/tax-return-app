"""LLM telemetry: spans, metrics and the `llm.call` log event.

Only allow-listed span attributes and low-cardinality metric attributes are ever set. No
prompt, part, response text, validation input, file name, key or org / project id: the
request id is the only provider-side identifier in logs and spans.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

import structlog
from opentelemetry import metrics, trace
from opentelemetry.metrics import MeterProvider
from opentelemetry.trace import Span, SpanKind, Status, StatusCode, TracerProvider

from app.llm.types import LLMCallRecord

SPAN_ATTRIBUTES = frozenset(
    {
        "gen_ai.operation.name",
        "gen_ai.provider.name",
        "gen_ai.request.model",
        "gen_ai.response.model",
        "gen_ai.response.id",
        "gen_ai.request.temperature",
        "gen_ai.request.max_tokens",
        "gen_ai.response.finish_reasons",
        "gen_ai.usage.input_tokens",
        "gen_ai.usage.output_tokens",
        "belegbot.llm.task",
        "belegbot.llm.prompt_version",
        "belegbot.llm.schema",
        "belegbot.llm.attempt",
        "belegbot.llm.fallback",
        "belegbot.llm.cost_eur",
        "belegbot.llm.pricing_version",
        "belegbot.llm.request_id",
        "belegbot.llm.input.parts",
        "belegbot.llm.input.images",
        "belegbot.llm.input.bytes",
        "belegbot.llm.validation_error_count",
        "error.type",
    }
)
# Metric attributes: the base set plus the documented extras per instrument.
METRIC_ATTRIBUTES = frozenset(
    {
        "gen_ai.provider.name",
        "gen_ai.request.model",
        "belegbot.llm.task",
        "outcome",
        "gen_ai.token.type",
        "reason",
        "from_model",
        "to_model",
    }
)

log = structlog.stdlib.get_logger("app.llm")


def set_attributes(span: Span, attributes: Mapping[str, Any]) -> None:
    for key, value in attributes.items():
        if key not in SPAN_ATTRIBUTES:
            raise ValueError(f"span attribute {key} is not allow-listed")
        if value is not None:
            span.set_attribute(key, value)


def mark_error(span: Span, error_type: str) -> None:
    set_attributes(span, {"error.type": error_type})
    span.set_status(Status(StatusCode.ERROR))  # no description: it could carry content


class LLMTelemetry:
    def __init__(
        self,
        tracer_provider: TracerProvider | None = None,
        meter_provider: MeterProvider | None = None,
    ) -> None:
        self.tracer = trace.get_tracer("app.llm", tracer_provider=tracer_provider)
        meter = metrics.get_meter("app.llm", meter_provider=meter_provider)
        self.calls = meter.create_counter(
            "belegbot.llm.calls", unit="{call}", description="LLM HTTP attempts by outcome"
        )
        self.tokens = meter.create_counter(
            "belegbot.llm.tokens", unit="{token}", description="LLM tokens (input / output)"
        )
        self.cost = meter.create_counter(
            "belegbot.llm.cost", unit="EUR", description="Estimated LLM cost in EUR"
        )
        self.duration = meter.create_histogram(
            "belegbot.llm.duration", unit="s", description="Duration of one LLM HTTP attempt"
        )
        self.retries = meter.create_counter(
            "belegbot.llm.retries", unit="{retry}", description="Same-model LLM retries / re-asks"
        )
        self.fallbacks = meter.create_counter(
            "belegbot.llm.fallbacks", unit="{fallback}", description="Switches to a fallback model"
        )
        self.unpriced_calls = meter.create_counter(
            "belegbot.llm.unpriced_calls", unit="{call}", description="Calls without a price"
        )
        self._unpriced_logged: set[tuple[str, str]] = set()

    @staticmethod
    def _base(provider: str, model: str, task: str) -> dict[str, str]:
        return {
            "gen_ai.provider.name": provider,
            "gen_ai.request.model": model,
            "belegbot.llm.task": task,
        }

    @contextmanager
    def router_span(self, task: str) -> Iterator[Span]:
        with self.tracer.start_as_current_span(
            f"llm {task}", record_exception=False, set_status_on_exception=False
        ) as span:
            yield span

    @contextmanager
    def attempt_span(self, model: str) -> Iterator[Span]:
        with self.tracer.start_as_current_span(
            f"chat {model}",
            kind=SpanKind.CLIENT,
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            yield span

    def record_call(
        self,
        record: LLMCallRecord,
        *,
        prompt_version: str,
        error_kind: str | None,
        retry_in_s: float | None,
    ) -> None:
        base = self._base(record.provider, record.model, record.task)
        self.calls.add(1, {**base, "outcome": record.outcome})
        self.duration.record(record.latency_ms / 1000, {**base, "outcome": record.outcome})
        if record.input_tokens:
            self.tokens.add(record.input_tokens, {**base, "gen_ai.token.type": "input"})
        if record.output_tokens:
            self.tokens.add(record.output_tokens, {**base, "gen_ai.token.type": "output"})
        if record.cost_eur:
            self.cost.add(float(record.cost_eur), base)
        fields: dict[str, Any] = {
            "provider": record.provider,
            "model": record.model,
            "task": record.task,
            "prompt_version": prompt_version,
            "attempt": record.attempt,
            "outcome": record.outcome,
            "error_kind": error_kind,
            "latency_ms": record.latency_ms,
            "usage_in": record.input_tokens,
            "usage_out": record.output_tokens,
            "cost_eur": float(record.cost_eur),
            "request_id": record.request_id,
        }
        if retry_in_s is not None:
            fields["retry_in_s"] = round(retry_in_s, 3)
        if error_kind is None:
            log.info("llm.call", **fields)
        else:
            log.warning("llm.call", **fields)

    def record_retry(self, provider: str, model: str, task: str, reason: str) -> None:
        self.retries.add(1, {**self._base(provider, model, task), "reason": reason})

    def record_fallback(
        self, provider: str, task: str, from_model: str, to_model: str, reason: str
    ) -> None:
        self.fallbacks.add(
            1,
            {
                **self._base(provider, to_model, task),
                "from_model": from_model,
                "to_model": to_model,
                "reason": reason,
            },
        )

    def record_unpriced(self, provider: str, model: str, task: str) -> None:
        self.unpriced_calls.add(1, self._base(provider, model, task))
        if (provider, model) not in self._unpriced_logged:
            self._unpriced_logged.add((provider, model))
            log.warning("llm.unpriced_model", provider=provider, model=model)

    def auth_failed(self, provider: str) -> None:
        log.error("llm.auth_failed", provider=provider)
