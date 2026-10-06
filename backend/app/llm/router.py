"""`LLMRouter`: routing, retries / backoff, schema re-ask, fallback, deadline, pricing and
telemetry, the same for every provider. LLM calls go through `get_router()` only.

Order of attempts for one `structured()` call:

1. Preflight the parts (`inputs.py`); input errors raise at once, with no HTTP call.
2. Primary model. Transient errors (`LLMTimeout`, `LLMRateLimited`, `LLMUnavailable`) are
   retried up to `LLM_MAX_ATTEMPTS` with exponential backoff and jitter; a `Retry-After`
   is honoured, unless it is longer than `LLM_RETRY_AFTER_MAX_SECONDS` or the remaining
   deadline, in which case `LLMRateLimited` is raised at once (#6's job backoff takes over).
3. Schema-invalid / truncated output is re-asked up to `schema_retries` times with the
   identical request (truncated: `max_output_tokens` doubled, capped). Refusals and content
   filtering are not re-asked.
4. Then each fallback model, same rules. Permanent errors never retry or fall back.
5. `LLM_DEADLINE_SECONDS` covers everything incl. sleeps; when exceeded → `LLMTimeout`.
6. The final exception carries all `calls` and `fallback_used`; output errors are raised
   with `retryable=False`.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import replace
from decimal import Decimal
from functools import lru_cache
from typing import Any

from opentelemetry.trace import Span

from app.config import LLMSettings, get_llm_settings
from app.llm.errors import (
    LLMAuthError,
    LLMError,
    LLMNotConfigured,
    LLMOutputError,
    LLMPermanentError,
    LLMRateLimited,
    LLMSchemaUnsupported,
    LLMSchemaValidationError,
    LLMTimeout,
    LLMTransientError,
    LLMTruncated,
)
from app.llm.inputs import InputLimits, PreparedInput, preflight
from app.llm.pricing import PricingTable, load_pricing
from app.llm.provider import PROVIDER_FACTORIES, LLMProvider, ProviderFactory
from app.llm.routing import (
    ModelRef,
    RoutingConfig,
    TaskRoute,
    apply_env_overrides,
    capabilities_for,
    load_routing,
    parse_model_ref,
    validate_model_ref,
    validate_routing,
)
from app.llm.telemetry import LLMTelemetry, mark_error, set_attributes
from app.llm.types import LLMCallRecord, LLMRequest, LLMResult, LLMUsage, Part, PdfInputMode, T

Sleep = Callable[[float], Awaitable[None]]
Clock = Callable[[], float]


# Raised before anything is sent: these attempts get no `LLMCallRecord`.
_NO_HTTP_ERRORS = (LLMNotConfigured, LLMSchemaUnsupported)


class _GiveUp(Exception):
    """Internal: stop the whole call now (no fallback), raising `error`."""

    def __init__(self, error: LLMError) -> None:
        super().__init__()
        self.error = error


class LLMRouter:
    def __init__(
        self,
        settings: LLMSettings,
        *,
        routing: RoutingConfig | None = None,
        pricing: PricingTable | None = None,
        providers: Mapping[str, LLMProvider] | None = None,
        provider_factories: Mapping[str, ProviderFactory] | None = None,
        telemetry: LLMTelemetry | None = None,
        sleep: Sleep = asyncio.sleep,
        clock: Clock = time.monotonic,
        rng: Callable[[], float] = random.random,
    ) -> None:
        """Builds and validates the routing (raises `LLMNotConfigured` naming the key).

        `providers` are ready instances (e.g. a scripted `FakeProvider`); other registered
        providers are built lazily from `provider_factories` on first use.
        """
        self.settings = settings
        self.pricing = pricing if pricing is not None else load_pricing()
        base_routing = routing if routing is not None else load_routing()
        self.routing = apply_env_overrides(base_routing, settings)
        self._factories: dict[str, ProviderFactory] = dict(
            provider_factories if provider_factories is not None else PROVIDER_FACTORIES
        )
        self._providers: dict[str, LLMProvider] = dict(providers or {})
        validate_routing(
            self.routing,
            pricing=self.pricing,
            providers=self.provider_names,
            app_env=settings.app_env,
        )
        self.telemetry = telemetry if telemetry is not None else LLMTelemetry()
        self.limits = InputLimits.from_settings(settings)
        self._sleep = sleep
        self._clock = clock
        self._rng = rng

    @property
    def provider_names(self) -> frozenset[str]:
        return frozenset(self._factories) | frozenset(self._providers)

    def with_providers(self, providers: Mapping[str, LLMProvider]) -> LLMRouter:
        """A router with the same config whose given providers replace the registered ones
        (e.g. a `FakeProvider` scripted per job or eval case). Shares telemetry."""
        merged = {**self._providers, **providers}
        return LLMRouter(
            self.settings,
            routing=self.routing,
            pricing=self.pricing,
            providers=merged,
            provider_factories=self._factories,
            telemetry=self.telemetry,
            sleep=self._sleep,
            clock=self._clock,
            rng=self._rng,
        )

    def _provider(self, name: str) -> LLMProvider:
        provider = self._providers.get(name)
        if provider is None:
            factory = self._factories.get(name)
            if factory is None:
                raise LLMNotConfigured(detail="provider is not registered")
            provider = factory(self.settings)
            self._providers[name] = provider
        return provider

    async def aclose(self) -> None:
        for provider in self._providers.values():
            await provider.aclose()

    # --- public API -------------------------------------------------------------------

    async def structured(
        self,
        *,
        task: str,
        system: str,
        parts: Sequence[Part],
        schema: type[T],
        prompt_version: str,
        model: str | None = None,
    ) -> LLMResult[T]:
        """`model` = "provider:model" override (evals): replaces the route's model and its
        fallback list; it may be unpriced (cost_known=False)."""
        with self.telemetry.router_span(task) as span:
            set_attributes(
                span,
                {
                    "belegbot.llm.task": task,
                    "belegbot.llm.prompt_version": prompt_version,
                    "belegbot.llm.schema": schema.__name__,
                    "belegbot.llm.input.parts": len(parts),
                    "belegbot.llm.pricing_version": self.pricing.version,
                },
            )
            try:
                return await self._structured(
                    span, task, system, parts, schema, prompt_version, model
                )
            except LLMError as exc:
                mark_error(span, type(exc).__name__)
                raise
            except BaseException as exc:
                mark_error(span, type(exc).__name__)
                raise

    # --- internals --------------------------------------------------------------------

    def _chain(self, route: TaskRoute, model: str | None) -> list[ModelRef]:
        if model is None:
            return [route.model, *route.fallback]
        ref = parse_model_ref(model, "model override")
        validate_model_ref(
            ref,
            providers=self.provider_names,
            app_env=self.settings.app_env,
            routing=self.routing,
            pricing=None,
        )
        return [ref]

    async def _structured(
        self,
        span: Span,
        task: str,
        system: str,
        parts: Sequence[Part],
        schema: type[T],
        prompt_version: str,
        model: str | None,
    ) -> LLMResult[T]:
        route = self.routing.route(task)
        chain = self._chain(route, model)
        prepared: dict[PdfInputMode, PreparedInput] = {}

        def inputs_for(mode: PdfInputMode) -> PreparedInput:
            if mode not in prepared:
                prepared[mode] = preflight(parts, self.limits, mode)
            return prepared[mode]

        # Preflight for the primary model before any HTTP call.
        first = inputs_for(capabilities_for(self.routing, chain[0]).pdf_input)
        set_attributes(
            span,
            {
                "belegbot.llm.input.images": first.n_images,
                "belegbot.llm.input.bytes": first.n_bytes,
            },
        )
        deadline = self._clock() + self.settings.llm_deadline_seconds
        calls: list[LLMCallRecord] = []
        last: LLMError | None = None
        for index, ref in enumerate(chain):
            fallback = index > 0
            if fallback and last is not None:
                self.telemetry.record_fallback(
                    ref.provider, task, chain[index - 1].key, ref.key, type(last).__name__
                )
            try:
                result = await self._run_model(
                    ref,
                    route,
                    system=system,
                    prepared=inputs_for(capabilities_for(self.routing, ref).pdf_input),
                    schema=schema,
                    prompt_version=prompt_version,
                    fallback=fallback,
                    deadline=deadline,
                    calls=calls,
                )
            except _GiveUp as stop:
                error = self._final(stop.error, calls, fallback)
                raise error from error.__cause__
            except LLMPermanentError as exc:
                self._final(exc, calls, fallback)
                raise
            except (LLMTransientError, LLMOutputError) as exc:
                last = exc
                continue
            return replace(result, fallback_used=fallback, calls=tuple(calls))
        assert last is not None
        raise self._final(last, calls, len(chain) > 1)

    @staticmethod
    def _final(exc: LLMError, calls: list[LLMCallRecord], fallback_used: bool) -> LLMError:
        exc.calls = tuple(calls)
        exc.fallback_used = fallback_used
        if isinstance(exc, LLMOutputError):
            exc.retryable = False
        elif isinstance(exc, LLMTransientError):
            exc.retryable = True
        return exc

    def _backoff(self, n: int) -> float:
        base = self.settings.llm_backoff_base_seconds * (2.0 ** (n - 1))
        delay = min(base, self.settings.llm_backoff_max_seconds)
        return delay * (0.8 + 0.4 * self._rng())

    async def _run_model(
        self,
        ref: ModelRef,
        route: TaskRoute,
        *,
        system: str,
        prepared: PreparedInput,
        schema: type[T],
        prompt_version: str,
        fallback: bool,
        deadline: float,
        calls: list[LLMCallRecord],
    ) -> LLMResult[T]:
        caps = capabilities_for(self.routing, ref)
        provider = self._provider(ref.provider)
        max_tokens = min(route.max_output_tokens, caps.max_output_tokens_cap)
        temperature = route.temperature if caps.supports_temperature else None
        transient_failures = 0
        output_failures = 0
        while True:
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise _GiveUp(LLMTimeout(provider=ref.provider, model=ref.model))
            request: LLMRequest[T] = LLMRequest(
                system=system,
                parts=prepared.parts,
                schema=schema,
                model=ref.model,
                temperature=temperature,
                max_output_tokens=max_tokens,
                timeout_s=min(route.timeout_s, remaining),
                task=route.task,
                prompt_version=prompt_version,
            )
            try:
                return await self._attempt(provider, ref, request, len(calls) + 1, fallback, calls)
            except LLMError as exc:
                error_kind = type(exc).__name__
                record = exc.calls[-1] if exc.calls else None
                delay: float | None = None  # None = stop with this error
                stop: BaseException = exc
                if isinstance(exc, LLMTransientError):
                    transient_failures += 1
                    remaining = deadline - self._clock()
                    retry_after = exc.retry_after_s if isinstance(exc, LLMRateLimited) else None
                    if transient_failures >= self.settings.llm_max_attempts:
                        pass
                    elif retry_after is not None:
                        if (
                            retry_after > self.settings.llm_retry_after_max_seconds
                            or retry_after > remaining
                        ):
                            stop = _GiveUp(exc)
                        else:
                            delay = retry_after
                    else:
                        backoff = self._backoff(transient_failures)
                        if backoff >= remaining:
                            stop = _GiveUp(LLMTimeout(provider=ref.provider, model=ref.model))
                        else:
                            delay = backoff
                elif isinstance(exc, LLMSchemaValidationError | LLMTruncated):
                    output_failures += 1
                    transient_failures = 0
                    if output_failures <= route.schema_retries:
                        delay = 0.0
                        if isinstance(exc, LLMTruncated):
                            max_tokens = min(max_tokens * 2, caps.max_output_tokens_cap)
                if record is not None:
                    self.telemetry.record_call(
                        record,
                        prompt_version=prompt_version,
                        error_kind=error_kind,
                        retry_in_s=delay,
                    )
                if isinstance(exc, LLMAuthError):
                    self.telemetry.auth_failed(ref.provider)
                if delay is None:
                    if stop is exc:
                        raise
                    raise stop from None
                self.telemetry.record_retry(ref.provider, ref.model, route.task, error_kind)
                if delay > 0:
                    await self._sleep(delay)

    def _price(self, record: LLMCallRecord, attempt: int) -> LLMCallRecord:
        price = self.pricing.lookup(record.provider, record.model, record.response_model)
        has_usage = record.input_tokens > 0 or record.output_tokens > 0
        if price is None:
            self.telemetry.record_unpriced(record.provider, record.model, record.task)
            return replace(record, attempt=attempt, cost_eur=Decimal("0"), cost_known=False)
        cost = self.pricing.cost_eur(
            price,
            LLMUsage(
                input_tokens=record.input_tokens,
                output_tokens=record.output_tokens,
                cached_input_tokens=record.cached_input_tokens,
            ),
        )
        return replace(record, attempt=attempt, cost_eur=cost, cost_known=has_usage)

    async def _attempt(
        self,
        provider: LLMProvider,
        ref: ModelRef,
        request: LLMRequest[T],
        attempt: int,
        fallback: bool,
        calls: list[LLMCallRecord],
    ) -> LLMResult[T]:
        """One provider call inside a `chat {model}` span. Errors leave their priced record
        in `exc.calls` (logging happens in `_run_model`, once the next step is known)."""
        with self.telemetry.attempt_span(ref.model) as span:
            set_attributes(
                span,
                {
                    "gen_ai.operation.name": "chat",
                    "gen_ai.provider.name": ref.provider,
                    "gen_ai.request.model": ref.model,
                    "gen_ai.request.temperature": request.temperature,
                    "gen_ai.request.max_tokens": request.max_output_tokens,
                    "belegbot.llm.task": request.task,
                    "belegbot.llm.prompt_version": request.prompt_version,
                    "belegbot.llm.schema": request.schema.__name__,
                    "belegbot.llm.attempt": attempt,
                    "belegbot.llm.fallback": fallback,
                    "belegbot.llm.pricing_version": self.pricing.version,
                    "belegbot.llm.input.parts": len(request.parts),
                },
            )
            started = self._clock()
            try:
                result = await self._call_provider(provider, request)
            except LLMError as exc:
                error_kind = type(exc).__name__
                mark_error(span, error_kind)
                if isinstance(exc, _NO_HTTP_ERRORS):
                    exc.calls = ()  # nothing was sent: no record
                    raise
                elapsed_ms = int((self._clock() - started) * 1000)
                raw_record = (
                    exc.calls[-1]
                    if exc.calls
                    else self._synthetic(ref, request, error_kind, elapsed_ms, exc.request_id)
                )
                record = self._price(raw_record, attempt)
                calls.append(record)
                exc.calls = (record,)
                self._span_record(span, record)
                if isinstance(exc, LLMSchemaValidationError):
                    set_attributes(span, {"belegbot.llm.validation_error_count": exc.error_count})
                raise
            except Exception as exc:
                mark_error(span, type(exc).__name__)
                raise
            record = self._price(result.calls[-1], attempt)
            calls.append(record)
            self._span_record(span, record)
            set_attributes(span, {"gen_ai.response.finish_reasons": ["stop"]})
            self.telemetry.record_call(
                record, prompt_version=request.prompt_version, error_kind=None, retry_in_s=None
            )
            return replace(
                result,
                provider=record.provider,
                model=record.model,
                cost_eur=record.cost_eur,
                latency_ms=record.latency_ms,
                request_id=record.request_id,
                pricing_version=self.pricing.version,
            )

    async def _call_provider(self, provider: LLMProvider, request: LLMRequest[T]) -> LLMResult[T]:
        # Safety net in real time on top of the HTTP timeout (the clock may be injected).
        try:
            async with asyncio.timeout(request.timeout_s + 5):
                return await provider.structured(request)
        except TimeoutError:
            raise LLMTimeout(provider=provider.name, model=request.model) from None

    @staticmethod
    def _synthetic(
        ref: ModelRef,
        request: LLMRequest[Any],
        outcome: str,
        latency_ms: int,
        request_id: str | None,
    ) -> LLMCallRecord:
        return LLMCallRecord(
            provider=ref.provider,
            model=ref.model,
            response_model=None,
            task=request.task,
            attempt=1,
            outcome=outcome,
            input_tokens=0,
            output_tokens=0,
            cached_input_tokens=0,
            cost_eur=Decimal("0"),
            cost_known=False,
            latency_ms=max(0, latency_ms),
            request_id=request_id,
            response_id=None,
        )

    def _span_record(self, span: Span, record: LLMCallRecord) -> None:
        set_attributes(
            span,
            {
                "gen_ai.response.model": record.response_model,
                "gen_ai.response.id": record.response_id,
                "gen_ai.usage.input_tokens": record.input_tokens,
                "gen_ai.usage.output_tokens": record.output_tokens,
                "belegbot.llm.cost_eur": float(record.cost_eur),
                "belegbot.llm.request_id": record.request_id,
            },
        )


@lru_cache
def get_router() -> LLMRouter:
    """Process-wide router built from settings; providers are created on first use."""
    return LLMRouter(get_llm_settings())
