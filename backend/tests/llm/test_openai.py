"""OpenAIProvider against respx-mocked HTTP with the synthetic fixtures."""

from __future__ import annotations

import base64
import io
import json
import secrets
from decimal import Decimal
from typing import Any

import httpx
import pytest
import respx
from pydantic import BaseModel

from app.config import get_settings
from app.llm import ImagePart, PdfPart, TextPart
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
    LLMSchemaUnsupported,
    LLMSchemaValidationError,
    LLMTimeout,
    LLMTruncated,
    LLMUnavailable,
)
from app.llm.openai_provider import OpenAIProvider
from app.llm.smoke import synthetic_pdf
from app.llm.types import LLMRequest

from .helpers import (
    EXPECTED,
    RESPONSES_URL,
    Otel,
    Synthetic,
    fixture_response,
    llm_settings,
    load_fixture,
    make_router,
    routing,
)

OPENAI_ROUTING = {
    "classify": {"model": "openai:gpt-test", "schema_retries": 0},
    "extract": {"model": "openai:gpt-notemp"},
}


def runtime_key() -> str:
    return f"sk-test-{secrets.token_hex(12)}"


def provider(key: str | None = None) -> OpenAIProvider:
    return OpenAIProvider(llm_settings(openai_api_key=key or runtime_key()))


def request(schema: type[BaseModel] = Synthetic, **overrides: Any) -> LLMRequest[Any]:
    values: dict[str, Any] = {
        "system": "Extract the receipt.",
        "parts": [TextPart("synthetic"), PdfPart(synthetic_pdf(2))],
        "schema": schema,
        "model": "gpt-test",
        "temperature": 0.0,
        "max_output_tokens": 1000,
        "timeout_s": 30,
        "task": "extract",
        "prompt_version": "p1",
    }
    values.update(overrides)
    return LLMRequest(**values)


def mocked() -> respx.MockRouter:
    return respx.mock(assert_all_mocked=True, assert_all_called=True)


async def test_completed_round_trip() -> None:
    with mocked() as mock:
        mock.post(RESPONSES_URL).mock(return_value=fixture_response("completed"))
        result = await provider().structured(request())
    fixture = load_fixture("completed")
    assert result.data == EXPECTED
    assert result.raw_text == fixture["body"]["output"][0]["content"][0]["text"]
    record = result.calls[0]
    assert (result.input_tokens, result.output_tokens) == (1234, 56)
    assert record.cached_input_tokens == 200
    assert result.request_id == "req_test_completed"
    assert record.response_id == "resp_test_completed"
    assert record.response_model == "gpt-test-2026-01-01"
    assert result.latency_ms >= 0
    assert len(result.calls) == 1


async def test_request_body_shape() -> None:
    with mocked() as mock:
        route = mock.post(RESPONSES_URL).mock(return_value=fixture_response("completed"))
        await provider().structured(request())
    body = json.loads(route.calls.last.request.content)
    assert body["store"] is False
    fmt = body["text"]["format"]
    assert fmt["type"] == "json_schema" and fmt["strict"] is True
    assert fmt["schema"]["additionalProperties"] is False
    assert sorted(fmt["schema"]["required"]) == sorted(Synthetic.model_fields)
    assert fmt["schema"]["$defs"]["Item"]["additionalProperties"] is False
    assert body["temperature"] == 0
    assert body["max_output_tokens"] == 1000
    content = body["input"][0]["content"]
    files = [c for c in content if c["type"] == "input_file"]
    assert len(files) == 1 and files[0]["filename"] == "document.pdf"
    for forbidden in ("user", "metadata", "safety_identifier"):
        assert forbidden not in body
    headers = route.calls.last.request.headers
    assert "openai-organization" not in headers and "openai-project" not in headers


async def test_no_temperature_for_models_without_support() -> None:
    with mocked() as mock:
        route = mock.post(RESPONSES_URL).mock(return_value=fixture_response("completed"))
        router = make_router(
            providers={"openai": provider()}, routing_config=routing(**OPENAI_ROUTING)
        )
        await router.structured(
            task="extract",
            system="s",
            parts=[TextPart("synthetic")],
            schema=Synthetic,
            prompt_version="p1",
        )
    body = json.loads(route.calls.last.request.content)
    assert "temperature" not in body
    assert body["model"] == "gpt-notemp"


class WithDecimal(BaseModel):
    vendor: str
    total: Decimal


class Line(BaseModel):
    price: float


class WithNestedFloat(BaseModel):
    lines: list[Line]


@pytest.mark.parametrize(
    ("schema", "field"), [(WithDecimal, "total"), (WithNestedFloat, "lines[].price")]
)
async def test_unsupported_schema_raises_before_http(schema: type[BaseModel], field: str) -> None:
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as mock:
        route = mock.post(RESPONSES_URL)
        with pytest.raises(LLMSchemaUnsupported) as info:
            await provider().structured(request(schema))
        assert route.call_count == 0
    assert field in str(info.value)


ERROR_CASES: list[tuple[str, type[LLMError], bool, int | None]] = [
    ("error_400_context_length", LLMInputTooLarge, False, 400),
    ("error_401", LLMAuthError, False, 401),
    ("error_403", LLMAuthError, False, 403),
    ("error_404_model", LLMBadRequest, False, 404),
    ("error_429_retry_after", LLMRateLimited, True, 429),
    ("error_429_insufficient_quota", LLMQuotaExceeded, False, 429),
    ("error_500", LLMUnavailable, True, 500),
    ("error_503", LLMUnavailable, True, 503),
    ("failed", LLMUnavailable, True, None),
    ("refusal", LLMRefusal, False, None),
    ("incomplete_max_output_tokens", LLMTruncated, False, None),
    ("incomplete_content_filter", LLMContentFiltered, False, None),
    ("not_json", LLMSchemaValidationError, False, None),
    ("schema_violation", LLMSchemaValidationError, False, None),
]


@pytest.mark.parametrize(("fixture", "error", "retryable", "status"), ERROR_CASES)
async def test_fixture_maps_to_error(
    fixture: str, error: type[LLMError], retryable: bool, status: int | None
) -> None:
    with mocked() as mock:
        mock.post(RESPONSES_URL).mock(return_value=fixture_response(fixture))
        with pytest.raises(LLMError) as info:
            await provider().structured(request())
    assert type(info.value) is error
    assert info.value.retryable is retryable
    assert info.value.status_code == status
    assert info.value.request_id == f"req_test_{fixture}"
    if isinstance(info.value, LLMRateLimited):
        assert info.value.retry_after_s == 2


@pytest.mark.parametrize(
    ("side_effect", "error"),
    [(httpx.ReadTimeout("timed out"), LLMTimeout), (httpx.ConnectError("refused"), LLMUnavailable)],
)
async def test_transport_errors(side_effect: Exception, error: type[LLMError]) -> None:
    with mocked() as mock:
        mock.post(RESPONSES_URL).mock(side_effect=side_effect)
        with pytest.raises(error) as info:
            await provider().structured(request())
    assert info.value.retryable is True
    assert info.value.status_code is None


@pytest.mark.parametrize("fixture", ["schema_violation", "incomplete_max_output_tokens"])
async def test_invalid_and_truncated_output_is_still_billed(fixture: str) -> None:
    router = make_router(
        providers={"openai": provider()},
        routing_config=routing(
            classify={"model": "openai:gpt-test", "schema_retries": 0, "fallback": []}
        ),
    )
    with mocked() as mock:
        mock.post(RESPONSES_URL).mock(return_value=fixture_response(fixture))
        with pytest.raises((LLMSchemaValidationError, LLMTruncated)) as info:
            await router.structured(
                task="classify",
                system="s",
                parts=[TextPart("synthetic")],
                schema=Synthetic,
                prompt_version="p1",
            )
    assert len(info.value.calls) == 1
    record = info.value.calls[0]
    assert record.input_tokens > 0 and record.output_tokens > 0
    assert record.cost_eur > 0 and record.cost_known is True


async def test_completed_without_usage() -> None:
    router = make_router(providers={"openai": provider()}, routing_config=routing(**OPENAI_ROUTING))
    with mocked() as mock:
        mock.post(RESPONSES_URL).mock(return_value=fixture_response("completed_no_usage"))
        result = await router.structured(
            task="classify",
            system="s",
            parts=[TextPart("synthetic")],
            schema=Synthetic,
            prompt_version="p1",
        )
    assert result.input_tokens == result.output_tokens == 0
    assert result.calls[0].cost_known is False
    assert result.cost_eur == 0


@pytest.mark.parametrize("key", [None, ""])
async def test_key_unset_raises_not_configured_without_http(key: str | None) -> None:
    settings = llm_settings() if key is None else llm_settings(openai_api_key=key)
    assert settings.openai_api_key is None
    router = make_router(
        providers={"openai": OpenAIProvider(settings)}, routing_config=routing(**OPENAI_ROUTING)
    )
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as mock:
        route = mock.post(RESPONSES_URL)
        with pytest.raises(LLMNotConfigured) as info:
            await router.structured(
                task="classify",
                system="s",
                parts=[TextPart("synthetic")],
                schema=Synthetic,
                prompt_version="p1",
            )
        assert route.call_count == 0
        assert len(mock.calls) == 0
    assert info.value.retryable is False
    assert str(info.value) == "LLMNotConfigured: OPENAI_API_KEY is not set"


async def test_key_set_but_invalid(
    otel: Otel, json_log: io.StringIO, capsys: pytest.CaptureFixture[str]
) -> None:
    key = runtime_key()
    router = make_router(
        providers={"openai": provider(key)},
        routing_config=routing(classify={"model": "openai:gpt-test", "fallback": ["openai:other"]}),
        otel=otel,
    )
    with mocked() as mock:
        route = mock.post(RESPONSES_URL).mock(return_value=fixture_response("error_401"))
        with pytest.raises(LLMAuthError) as info:
            await router.structured(
                task="classify",
                system="s",
                parts=[TextPart("synthetic")],
                schema=Synthetic,
                prompt_version="p1",
            )
    assert route.call_count == 1
    # the key was sent as a bearer token, so the mock really saw it
    assert route.calls.last.request.headers["authorization"] == f"Bearer {key}"
    lines = json_log.getvalue().splitlines()
    assert sum('"llm.auth_failed"' in line for line in lines) == 1
    exc = info.value
    haystacks = [json_log.getvalue(), capsys.readouterr().out, str(exc), repr(exc)]
    haystacks += [repr(c) for c in exc.calls]
    for span in otel.finished():
        haystacks.append(span.name)
        haystacks.extend(f"{k}={v}" for k, v in (span.attributes or {}).items())
    haystacks.extend(otel.all_metric_attribute_values())
    haystacks.append(repr(get_settings()))
    assert all(key not in text for text in haystacks)


async def test_image_part_is_sent_as_data_url() -> None:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (40, 30), "white").save(buf, format="PNG")
    with mocked() as mock:
        route = mock.post(RESPONSES_URL).mock(return_value=fixture_response("completed"))
        await provider().structured(
            request(parts=[ImagePart(data=buf.getvalue(), mime="image/png")])
        )
    content = json.loads(route.calls.last.request.content)["input"][0]["content"]
    assert content[0]["type"] == "input_image"
    assert content[0]["detail"] == "high"
    prefix = "data:image/png;base64,"
    assert content[0]["image_url"].startswith(prefix)
    assert base64.b64decode(content[0]["image_url"][len(prefix) :]) == buf.getvalue()
