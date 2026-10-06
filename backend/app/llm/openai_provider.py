"""`OpenAIProvider`: Responses API with strict JSON-schema structured output.

Exactly one HTTP call per `structured()` call (`max_retries=0`; retries live in the router).
Every request sets `store: false` and carries no `user` / `metadata` / `safety_identifier`.
PDFs are sent as `input_file` with the constant name `document.pdf`.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from email.utils import parsedate_to_datetime
from typing import Any, cast

import httpx
import openai
from openai import AsyncOpenAI

from app.config import LLMSettings
from app.llm.errors import (
    LLMAuthError,
    LLMBadRequest,
    LLMContentFiltered,
    LLMError,
    LLMInputTooLarge,
    LLMNotConfigured,
    LLMQuotaExceeded,
    LLMRateLimited,
    LLMRefusal,
    LLMTimeout,
    LLMTruncated,
    LLMUnavailable,
)
from app.llm.provider import provider_record, provider_result
from app.llm.schema import schema_name, strict_json_schema, validate_output
from app.llm.types import ImagePart, LLMRequest, LLMResult, LLMUsage, PdfPart, T, TextPart

OPENAI_PROVIDER = "openai"
PDF_FILENAME = "document.pdf"  # constant: never the original file name
IMAGE_DETAIL = "high"
# 429s that are billing problems (permanent), not rate limits. Seen live on 2026-10-06:
# type "insufficient_quota" with code "credit_balance_exhausted".
_QUOTA_CODES = frozenset(
    {"insufficient_quota", "credit_balance_exhausted", "billing_hard_limit_reached"}
)

# The SDK logs request options (incl. the prompt) at DEBUG and httpx logs URLs at INFO.
for _name in ("openai", "httpx", "httpcore"):
    logging.getLogger(_name).setLevel(logging.WARNING)


def _input_content(request: LLMRequest[Any]) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = []
    for part in request.parts:
        if isinstance(part, TextPart):
            content.append({"type": "input_text", "text": part.text})
        elif isinstance(part, ImagePart):
            encoded = base64.b64encode(part.data).decode("ascii")
            content.append(
                {
                    "type": "input_image",
                    "image_url": f"data:{part.mime};base64,{encoded}",
                    "detail": IMAGE_DETAIL,
                }
            )
        elif isinstance(part, PdfPart):
            encoded = base64.b64encode(part.data).decode("ascii")
            content.append(
                {
                    "type": "input_file",
                    "filename": PDF_FILENAME,
                    "file_data": f"data:application/pdf;base64,{encoded}",
                }
            )
    return content


def build_request_body(request: LLMRequest[Any]) -> dict[str, Any]:
    """Keyword arguments for `responses.create` (also asserted on in tests)."""
    body: dict[str, Any] = {
        "model": request.model,
        "instructions": request.system,
        "input": [{"role": "user", "content": _input_content(request)}],
        "text": {
            "format": {
                "type": "json_schema",
                "name": schema_name(request.schema),
                "strict": True,
                "schema": strict_json_schema(request.schema),
            }
        },
        "max_output_tokens": request.max_output_tokens,
        "store": False,
    }
    if request.temperature is not None:
        body["temperature"] = request.temperature
    return body


def _usage(body: dict[str, Any]) -> LLMUsage | None:
    usage = body.get("usage")
    if not isinstance(usage, dict):
        return None
    details = usage.get("input_tokens_details") or {}
    return LLMUsage(
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
        cached_input_tokens=int(details.get("cached_tokens") or 0)
        if isinstance(details, dict)
        else 0,
    )


def _retry_after_s(headers: httpx.Headers | Any) -> float | None:
    try:
        ms = headers.get("retry-after-ms")
        if ms is not None:
            return max(0.0, float(ms) / 1000)
        value = headers.get("retry-after")
        if value is None:
            return None
        try:
            return max(0.0, float(value))
        except ValueError:
            delta = parsedate_to_datetime(value).timestamp() - time.time()
            return max(0.0, delta)
    except Exception:
        return None


def _output_text_and_refusal(body: dict[str, Any]) -> tuple[str, bool]:
    texts: list[str] = []
    refused = False
    for item in body.get("output") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "refusal":
            refused = True
        for content in item.get("content") or []:
            if not isinstance(content, dict):
                continue
            if content.get("type") == "refusal":
                refused = True
            elif content.get("type") == "output_text" and isinstance(content.get("text"), str):
                texts.append(content["text"])
    return "".join(texts), refused


class OpenAIProvider:
    name = OPENAI_PROVIDER

    def __init__(
        self, settings: LLMSettings, *, http_client: httpx.AsyncClient | None = None
    ) -> None:
        self._settings = settings
        self._http_client = http_client
        self._client: AsyncOpenAI | None = None

    def _get_client(self) -> AsyncOpenAI:
        key = self._settings.openai_api_key
        if key is None or not key.get_secret_value().strip():
            raise LLMNotConfigured(provider=OPENAI_PROVIDER, detail="OPENAI_API_KEY is not set")
        if self._client is None:
            http_client = self._http_client or httpx.AsyncClient(
                timeout=httpx.Timeout(60.0, connect=10.0)
            )
            self._client = AsyncOpenAI(
                api_key=key.get_secret_value(),
                base_url=self._settings.openai_base_url,
                max_retries=0,
                # A plain httpx client (the SDK accepts it), so respx can mock it in tests.
                http_client=cast(Any, http_client),
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    def _error(
        self,
        cls: type[LLMError],
        request: LLMRequest[Any],
        *,
        status_code: int | None = None,
        request_id: str | None = None,
        response_id: str | None = None,
        response_model: str | None = None,
        usage: LLMUsage | None = None,
        latency_ms: int = 0,
        **extra: Any,
    ) -> LLMError:
        exc = cls(
            provider=OPENAI_PROVIDER,
            model=request.model,
            request_id=request_id,
            status_code=status_code,
            **extra,
        )
        exc.calls = (
            provider_record(
                provider=OPENAI_PROVIDER,
                request=request,
                outcome=cls.__name__,
                usage=usage,
                latency_ms=latency_ms,
                request_id=request_id,
                response_id=response_id,
                response_model=response_model,
            ),
        )
        return exc

    def _map_status_error(
        self, exc: openai.APIStatusError, request: LLMRequest[Any], latency_ms: int
    ) -> LLMError:
        status = exc.status_code
        headers = exc.response.headers
        request_id = headers.get("x-request-id")
        code = exc.code or ""
        kwargs: dict[str, Any] = {
            "status_code": status,
            "request_id": request_id,
            "latency_ms": latency_ms,
        }
        if status in (401, 403):
            return self._error(LLMAuthError, request, **kwargs)
        if status == 429:
            if code in _QUOTA_CODES or exc.type == "insufficient_quota":
                return self._error(LLMQuotaExceeded, request, **kwargs)
            return self._error(
                LLMRateLimited, request, retry_after_s=_retry_after_s(headers), **kwargs
            )
        if status == 413 or code == "context_length_exceeded":
            return self._error(LLMInputTooLarge, request, **kwargs)
        if status in (408, 409) or status >= 500:
            return self._error(LLMUnavailable, request, **kwargs)
        return self._error(LLMBadRequest, request, **kwargs)

    async def structured(self, request: LLMRequest[T]) -> LLMResult[T]:
        body_kwargs = build_request_body(request)  # LLMSchemaUnsupported before any I/O
        client = self._get_client()
        started = time.monotonic()
        try:
            raw = await client.responses.with_raw_response.create(
                **body_kwargs, timeout=request.timeout_s
            )
        except openai.APITimeoutError as exc:
            latency = int((time.monotonic() - started) * 1000)
            raise self._error(LLMTimeout, request, latency_ms=latency) from exc
        except openai.APIConnectionError as exc:
            latency = int((time.monotonic() - started) * 1000)
            raise self._error(LLMUnavailable, request, latency_ms=latency) from exc
        except openai.APIStatusError as exc:
            latency = int((time.monotonic() - started) * 1000)
            raise self._map_status_error(exc, request, latency) from exc
        latency_ms = int((time.monotonic() - started) * 1000)
        header_request_id = raw.headers.get("x-request-id")
        try:
            body = json.loads(raw.text)
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            raise self._error(
                LLMUnavailable,
                request,
                status_code=raw.status_code,
                request_id=header_request_id,
                latency_ms=latency_ms,
                detail="openai returned a body that is not a JSON object",
            ) from None
        return self._parse(request, body, header_request_id, latency_ms)

    def _parse(
        self,
        request: LLMRequest[T],
        body: dict[str, Any],
        header_request_id: str | None,
        latency_ms: int,
    ) -> LLMResult[T]:
        response_id = body.get("id") if isinstance(body.get("id"), str) else None
        request_id = header_request_id or response_id
        response_model = body.get("model") if isinstance(body.get("model"), str) else None
        usage = _usage(body)
        context: dict[str, Any] = {
            "request_id": request_id,
            "response_id": response_id,
            "response_model": response_model,
            "usage": usage,
            "latency_ms": latency_ms,
        }
        status = body.get("status")
        if status == "failed":
            error = body.get("error") if isinstance(body.get("error"), dict) else {}
            code = (error or {}).get("code") or ""
            if code == "rate_limit_exceeded":
                raise self._error(LLMRateLimited, request, **context)
            if code in ("", "server_error"):
                raise self._error(LLMUnavailable, request, **context)
            raise self._error(LLMBadRequest, request, **context)
        if status == "incomplete":
            details = body.get("incomplete_details")
            reason = details.get("reason") if isinstance(details, dict) else None
            if reason == "content_filter":
                raise self._error(LLMContentFiltered, request, **context)
            raise self._error(LLMTruncated, request, **context)
        raw_text, refused = _output_text_and_refusal(body)
        if refused:
            raise self._error(LLMRefusal, request, **context)
        if status not in (None, "completed"):
            raise self._error(LLMUnavailable, request, **context)
        try:
            data = validate_output(
                request.schema,
                raw_text,
                provider=OPENAI_PROVIDER,
                model=request.model,
                request_id=request_id,
            )
        except LLMError as exc:
            exc.calls = (
                provider_record(
                    provider=OPENAI_PROVIDER,
                    request=request,
                    outcome=type(exc).__name__,
                    usage=usage,
                    latency_ms=latency_ms,
                    request_id=request_id,
                    response_id=response_id,
                    response_model=response_model,
                ),
            )
            raise
        record = provider_record(
            provider=OPENAI_PROVIDER,
            request=request,
            outcome="ok",
            usage=usage,
            latency_ms=latency_ms,
            request_id=request_id,
            response_id=response_id,
            response_model=response_model,
        )
        return provider_result(data=data, raw_text=raw_text, record=record)
