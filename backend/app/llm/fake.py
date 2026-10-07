"""Scripted `FakeProvider` for tests and local dev without a key (pricing key `fake:test`).

It returns / raises the scripted items in order and records `FakeCall`s without content.
`raw_text` that does not match the request schema raises `LLMSchemaValidationError`, like a
real provider. It is refused when `APP_ENV=production`.
"""

from __future__ import annotations

import asyncio
import copy
import os
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field

from pydantic import BaseModel

from app.llm.errors import LLMError, LLMNotConfigured, LLMTimeout
from app.llm.provider import provider_record, provider_result
from app.llm.routing import PRODUCTION
from app.llm.schema import validate_output
from app.llm.types import LLMRequest, LLMResult, LLMUsage, T

FAKE_PROVIDER = "fake"


@dataclass(frozen=True)
class FakeReply:
    """One scripted answer. `raw_text` wins over `data` (serialised to JSON)."""

    data: BaseModel | None = field(default=None, repr=False)
    raw_text: str | None = field(default=None, repr=False)
    input_tokens: int = 100
    output_tokens: int = 20
    cached_input_tokens: int = 0
    latency_ms: int = 5
    request_id: str | None = None
    response_model: str | None = None


@dataclass(frozen=True)
class FakeCall:
    """What a call asked for, without content."""

    model: str
    schema: type[BaseModel]
    n_parts: int
    temperature: float | None
    max_output_tokens: int


class FakeScriptExhausted(RuntimeError):
    """The script has no item left for this call (a test set-up error)."""

    def __init__(self) -> None:
        super().__init__("FakeProvider script exhausted")


class FakeProvider:
    name = FAKE_PROVIDER

    def __init__(
        self,
        script: Sequence[FakeReply | LLMError],
        *,
        app_env: str | None = None,
        delay_s: float = 0.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """`delay_s` simulates the time a call takes (capped at the request timeout, after
        which the call raises `LLMTimeout`, like a real HTTP timeout); `sleep` is injectable.
        """
        env = os.environ.get("APP_ENV", "") if app_env is None else app_env
        if env.strip().lower() == PRODUCTION:
            raise LLMNotConfigured(
                provider=FAKE_PROVIDER,
                detail="the fake provider is refused when APP_ENV=production",
            )
        self._script: list[FakeReply | LLMError] = list(script)
        self._delay_s = delay_s
        self._sleep = sleep
        self.calls: list[FakeCall] = []
        self.requests_seen = 0

    @property
    def remaining(self) -> int:
        return len(self._script)

    async def structured(self, request: LLMRequest[T]) -> LLMResult[T]:
        self.requests_seen += 1
        self.calls.append(
            FakeCall(
                model=request.model,
                schema=request.schema,
                n_parts=len(request.parts),
                temperature=request.temperature,
                max_output_tokens=request.max_output_tokens,
            )
        )
        if self._delay_s > 0:
            await self._sleep(min(self._delay_s, request.timeout_s))
            if self._delay_s > request.timeout_s:
                raise LLMTimeout(provider=FAKE_PROVIDER, model=request.model)
        if not self._script:
            raise FakeScriptExhausted()
        item = self._script.pop(0)
        if isinstance(item, LLMError):
            raise copy.copy(item)
        return self._reply(request, item)

    def _reply(self, request: LLMRequest[T], reply: FakeReply) -> LLMResult[T]:
        if reply.raw_text is not None:
            raw_text = reply.raw_text
        elif reply.data is not None:
            raw_text = reply.data.model_dump_json()
        else:
            raw_text = ""
        usage = LLMUsage(
            input_tokens=reply.input_tokens,
            output_tokens=reply.output_tokens,
            cached_input_tokens=reply.cached_input_tokens,
        )

        try:
            data = validate_output(
                request.schema,
                raw_text,
                provider=FAKE_PROVIDER,
                model=request.model,
                request_id=reply.request_id,
            )
        except LLMError as exc:
            exc.calls = (
                provider_record(
                    provider=FAKE_PROVIDER,
                    request=request,
                    outcome=type(exc).__name__,
                    usage=usage,
                    latency_ms=reply.latency_ms,
                    request_id=reply.request_id,
                    response_id=None,
                    response_model=reply.response_model,
                ),
            )
            raise
        ok = provider_record(
            provider=FAKE_PROVIDER,
            request=request,
            outcome="ok",
            usage=usage,
            latency_ms=reply.latency_ms,
            request_id=reply.request_id,
            response_id=None,
            response_model=reply.response_model,
        )
        return provider_result(data=data, raw_text=raw_text, record=ok)

    async def aclose(self) -> None:
        return None
