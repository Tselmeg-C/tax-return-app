"""`LLMProvider` protocol and the provider registry.

A provider makes exactly one HTTP call per `structured()` call and maps every outcome to an
`LLMResult` or one exception from `app.llm.errors`. Routing, retries, re-ask, fallback,
pricing and telemetry live in the router, so every provider behaves the same.

Providers return records with `attempt=1`, `cost_eur=0` and `cost_known=False`; the router
fills those in. An `LLMError` raised after an HTTP response carries one record in `calls`
(with the usage the response reported).
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Protocol, runtime_checkable

from app.config import LLMSettings
from app.llm.types import LLMCallRecord, LLMRequest, LLMResult, LLMUsage, T


def provider_record(
    *,
    provider: str,
    request: LLMRequest[T],
    outcome: str,
    usage: LLMUsage | None,
    latency_ms: int,
    request_id: str | None,
    response_id: str | None,
    response_model: str | None,
) -> LLMCallRecord:
    """A record as a provider reports it; the router sets attempt and cost."""
    usage = usage or LLMUsage()
    return LLMCallRecord(
        provider=provider,
        model=request.model,
        response_model=response_model,
        task=request.task,
        attempt=1,
        outcome=outcome,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cached_input_tokens=usage.cached_input_tokens,
        cost_eur=Decimal("0"),
        cost_known=False,
        latency_ms=max(0, latency_ms),
        request_id=request_id,
        response_id=response_id,
    )


def provider_result(*, data: T, raw_text: str, record: LLMCallRecord) -> LLMResult[T]:
    return LLMResult(
        data=data,
        raw_text=raw_text,
        provider=record.provider,
        model=record.model,
        input_tokens=record.input_tokens,
        output_tokens=record.output_tokens,
        cost_eur=record.cost_eur,
        latency_ms=record.latency_ms,
        request_id=record.request_id,
        pricing_version="",
        fallback_used=False,
        calls=(record,),
    )


@runtime_checkable
class LLMProvider(Protocol):
    name: str  # "openai", "fake"; later "anthropic", "gemini" (#23)

    async def structured(self, request: LLMRequest[T]) -> LLMResult[T]: ...

    async def aclose(self) -> None: ...


ProviderFactory = Callable[[LLMSettings], LLMProvider]


def _openai(settings: LLMSettings) -> LLMProvider:
    from app.llm.openai_provider import OpenAIProvider

    return OpenAIProvider(settings)


def _fake(settings: LLMSettings) -> LLMProvider:
    from app.llm.fake import FakeProvider

    # An empty script: #9 / tests pass a scripted instance via `LLMRouter(providers=…)`.
    return FakeProvider((), app_env=settings.app_env)


PROVIDER_FACTORIES: dict[str, ProviderFactory] = {"openai": _openai, "fake": _fake}


def registered_providers() -> frozenset[str]:
    return frozenset(PROVIDER_FACTORIES)
