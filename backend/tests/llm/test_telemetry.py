"""Telemetry contract and privacy: spans, metrics, log lines, sentinels, fixtures, repr."""

from __future__ import annotations

import io
import json
import re
import secrets
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from app.llm import ImagePart, LLMResult, PdfPart, TextPart
from app.llm.errors import LLMAuthError, LLMError
from app.llm.fake import FakeReply
from app.llm.openai_provider import OpenAIProvider
from app.llm.telemetry import METRIC_ATTRIBUTES, SPAN_ATTRIBUTES
from app.llm.types import LLMRequest

from .helpers import (
    EXPECTED,
    FIXTURES,
    RESPONSES_URL,
    FakeClock,
    Otel,
    Synthetic,
    llm_settings,
    load_fixture,
    make_router,
    routing,
)

LOG_FIELDS = {
    "provider",
    "model",
    "task",
    "prompt_version",
    "attempt",
    "outcome",
    "error_kind",
    "latency_ms",
    "usage_in",
    "usage_out",
    "cost_eur",
    "request_id",
    "retry_in_s",
}
CORE_FIELDS = {"event", "level", "timestamp", "logger", "service", "trace_id", "span_id"}


def _log_lines(buffer: io.StringIO, event: str) -> list[dict[str, Any]]:
    lines = [json.loads(line) for line in buffer.getvalue().splitlines() if line.strip()]
    return [line for line in lines if line.get("event") == event]


async def test_successful_call_spans_metrics_and_log(otel: Otel, json_log: io.StringIO) -> None:
    router = make_router(
        script=[FakeReply(data=EXPECTED, input_tokens=1200, output_tokens=80, request_id="req_x")],
        otel=otel,
    )
    result = await router.structured(
        task="classify",
        system="s",
        parts=[TextPart("synthetic")],
        schema=Synthetic,
        prompt_version="p1",
    )
    spans = {span.name: span for span in otel.finished()}
    assert set(spans) == {"llm classify", "chat test"}
    parent, child = spans["llm classify"], spans["chat test"]
    assert child.parent is not None and child.parent.span_id == parent.context.span_id
    for span in (parent, child):
        assert set((span.attributes or {}).keys()) <= SPAN_ATTRIBUTES
    attrs = child.attributes or {}
    assert attrs["gen_ai.usage.input_tokens"] == 1200
    assert attrs["gen_ai.usage.output_tokens"] == 80
    assert attrs["belegbot.llm.cost_eur"] == float(result.cost_eur)
    assert attrs["belegbot.llm.request_id"] == "req_x"
    assert attrs["gen_ai.operation.name"] == "chat"

    assert otel.total("belegbot.llm.calls", outcome="ok") == 1
    assert otel.total("belegbot.llm.tokens", **{"gen_ai.token.type": "input"}) == 1200
    assert otel.total("belegbot.llm.tokens", **{"gen_ai.token.type": "output"}) == 80
    assert otel.total("belegbot.llm.cost") == pytest.approx(float(result.cost_eur))
    assert [count for _, count in otel.points("belegbot.llm.duration")] == [1]
    assert otel.all_metric_attribute_keys() <= METRIC_ATTRIBUTES

    (line,) = _log_lines(json_log, "llm.call")
    assert line["usage_in"] == 1200 and line["usage_out"] == 80
    assert line["level"] == "info" and line["outcome"] == "ok"
    assert set(line) <= LOG_FIELDS | CORE_FIELDS


async def test_failed_attempt_logs_numbers_and_error_status(
    otel: Otel, json_log: io.StringIO
) -> None:
    router = make_router(
        script=[
            FakeReply(raw_text="not json", input_tokens=7, output_tokens=3),
            FakeReply(data=EXPECTED),
        ],
        otel=otel,
    )
    await router.structured(
        task="classify", system="s", parts=[TextPart("x")], schema=Synthetic, prompt_version="p"
    )
    first, second = _log_lines(json_log, "llm.call")
    assert first["level"] == "warning" and first["error_kind"] == "LLMSchemaValidationError"
    assert first["usage_in"] == 7 and first["usage_out"] == 3
    assert first["retry_in_s"] == 0
    failed = [s for s in otel.finished() if (s.attributes or {}).get("error.type")]
    assert len(failed) == 1
    assert failed[0].status.description is None
    assert (failed[0].attributes or {})["belegbot.llm.validation_error_count"] >= 1
    assert otel.total("belegbot.llm.retries", reason="LLMSchemaValidationError") == 1


def _png_with_text(sentinel: str) -> bytes:
    info = PngInfo()
    info.add_text("Comment", sentinel)
    out = io.BytesIO()
    Image.new("RGB", (60, 40), "white").save(out, format="PNG", pnginfo=info)
    return out.getvalue()


def _pdf_with_title(sentinel: str) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (60, 40), "white").save(out, format="PDF", title=sentinel, author=sentinel)
    return out.getvalue()


def _response(fixture: str, **patch_text: str) -> httpx.Response:
    data = load_fixture(fixture)
    body = data["body"]
    if "text" in patch_text:
        body["output"][0]["content"][0]["text"] = patch_text["text"]
    if "message" in patch_text:
        body["error"]["message"] = patch_text["message"]
    return httpx.Response(data["status"], headers=data["headers"], json=body)


async def test_sentinels_never_reach_telemetry(
    otel: Otel, json_log: io.StringIO, capsys: pytest.CaptureFixture[str]
) -> None:
    s = {
        name: f"SENTINEL{name.upper()}{secrets.token_hex(6)}"
        for name in ("system", "text", "image", "pdf", "response", "sdkerror", "key")
    }
    ok_text = EXPECTED.model_copy(update={"vendor": s["response"]}).model_dump_json()
    invalid_text = json.dumps({"vendor": s["response"], "total": s["response"]})
    clock = FakeClock()
    settings = llm_settings(openai_api_key=f"sk-test-{s['key']}")
    router = make_router(
        providers={"openai": OpenAIProvider(settings)},
        settings=settings,
        routing_config=routing(classify={"model": "openai:gpt-test", "schema_retries": 1}),
        otel=otel,
        clock=clock,
    )
    parts = [
        TextPart(f"synthetic {s['text']}"),
        ImagePart(data=_png_with_text(s["image"]), mime="image/png"),
        PdfPart(_pdf_with_title(s["pdf"])),
    ]
    raised: list[BaseException] = []
    with respx.mock(assert_all_mocked=True, assert_all_called=True) as mock:
        mock.post(RESPONSES_URL).mock(
            side_effect=[
                _response("error_429_retry_after", message=s["sdkerror"]),
                _response("schema_violation", text=invalid_text),
                _response("completed", text=ok_text),
                _response("error_401", message=s["sdkerror"]),
            ]
        )
        result: LLMResult[Synthetic] = await router.structured(
            task="classify",
            system=f"Extract. {s['system']}",
            parts=parts,
            schema=Synthetic,
            prompt_version="p1",
        )
        assert result.data.vendor == s["response"]
        with pytest.raises(LLMAuthError) as info:
            await router.structured(
                task="classify",
                system=f"Extract. {s['system']}",
                parts=parts,
                schema=Synthetic,
                prompt_version="p1",
            )
        raised.append(info.value)
    assert clock.sleeps == [2]
    assert [c.outcome for c in result.calls] == ["LLMRateLimited", "LLMSchemaValidationError", "ok"]

    haystacks: list[str] = [json_log.getvalue(), capsys.readouterr().out, repr(result)]
    for exc in raised:
        assert isinstance(exc, LLMError)
        haystacks += [str(exc), repr(exc)]
        haystacks += [repr(c) for c in exc.calls]
    haystacks += [repr(c) for c in result.calls]
    for span in otel.finished():
        haystacks.append(span.name)
        haystacks += [f"{k}={v}" for k, v in (span.attributes or {}).items()]
        for event in span.events:
            haystacks.append(event.name)
            haystacks += [f"{k}={v}" for k, v in (event.attributes or {}).items()]
    haystacks += otel.all_metric_attribute_values()
    blob = "\n".join(haystacks)
    for name, sentinel in s.items():
        assert sentinel not in blob, f"{name} sentinel leaked"

    # The request id is the only provider-side identifier in logs.
    for line in _log_lines(json_log, "llm.call"):
        assert set(line) <= LOG_FIELDS | CORE_FIELDS
        assert isinstance(line["usage_in"], int) and isinstance(line["usage_out"], int)
    assert "resp_test_" not in json_log.getvalue()


def test_repr_of_types_has_no_content() -> None:
    sentinel = f"SENTINEL{secrets.token_hex(6)}"
    blob = sentinel.encode()
    text, image, pdf = TextPart(sentinel), ImagePart(blob, "image/png"), PdfPart(blob)
    request = LLMRequest(
        system=sentinel,
        parts=[text, image, pdf],
        schema=Synthetic,
        model="m",
        temperature=0,
        max_output_tokens=1,
        timeout_s=1,
        task="t",
        prompt_version="p",
    )
    result = LLMResult(
        data=EXPECTED.model_copy(update={"vendor": sentinel}),
        raw_text=sentinel,
        provider="fake",
        model="m",
        input_tokens=1,
        output_tokens=1,
        cost_eur=0,  # type: ignore[arg-type]
        latency_ms=1,
        request_id=None,
        pricing_version="v",
        fallback_used=False,
        calls=(),
    )
    for obj in (text, image, pdf, request, result):
        assert sentinel not in repr(obj)
        assert sentinel not in str(obj)


_SK_KEY = re.compile(r"sk-[A-Za-z0-9_-]{8,}")
_ELEVEN_DIGITS = re.compile(r"(?<!\d)\d{11}(?!\d)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
_FORBIDDEN_HEADERS = ("authorization", "openai-organization", "openai-project")


def _ids(node: Any, key: str) -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key and isinstance(v, str):
                found.append(v)
            found += _ids(v, key)
    elif isinstance(node, list):
        for item in node:
            found += _ids(item, key)
    return found


FIXTURE_FILES = sorted(p for p in FIXTURES.rglob("*") if p.is_file())


def test_fixture_directory_is_not_empty() -> None:
    assert len(FIXTURE_FILES) >= 16


@pytest.mark.parametrize("path", FIXTURE_FILES, ids=lambda p: p.name)
def test_fixture_privacy(path: Path) -> None:
    raw = path.read_text("utf-8")
    assert not _SK_KEY.search(raw)
    assert not _ELEVEN_DIGITS.search(raw)
    for match in _EMAIL.finditer(raw):
        assert match.group(1).lower() in ("example.com", "example.org")
    lowered = raw.lower()
    for header in _FORBIDDEN_HEADERS:
        assert f'"{header}"' not in lowered
    data = json.loads(raw)
    headers = {k.lower(): v for k, v in data.get("headers", {}).items()}
    for header in _FORBIDDEN_HEADERS:
        assert header not in headers
    if "x-request-id" in headers:
        assert re.match(r"^req_test_", headers["x-request-id"])
    body = data.get("body", {})
    if body.get("object") == "response":
        assert re.match(r"^resp_test_", body["id"])
    for request_id in _ids(data, "request_id"):
        assert re.match(r"^req_test_", request_id)
