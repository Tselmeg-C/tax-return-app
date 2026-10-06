"""Provider contract suite: the same cases run against every provider.

A harness arranges one outcome (`arrange(case)`) and returns the provider to call. #23 adds
its providers (Anthropic, Gemini) to `HARNESSES` with their own mocked HTTP fixtures.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol

import respx

from app.llm.errors import (
    LLMAuthError,
    LLMContentFiltered,
    LLMError,
    LLMRateLimited,
    LLMRefusal,
    LLMSchemaValidationError,
    LLMTruncated,
    LLMUnavailable,
)
from app.llm.fake import FakeProvider, FakeReply
from app.llm.openai_provider import OpenAIProvider
from app.llm.provider import LLMProvider

from .helpers import EXPECTED, RESPONSES_URL, fixture_response, llm_settings


@dataclass(frozen=True)
class Case:
    name: str
    error: type[LLMError] | None  # None = success
    retryable: bool = False
    billed: bool = False  # the error carries a record with the response's usage


CASES = [
    Case("ok", None),
    Case("not_json", LLMSchemaValidationError, billed=True),
    Case("schema_violation", LLMSchemaValidationError, billed=True),
    Case("refusal", LLMRefusal),
    Case("truncated", LLMTruncated),
    Case("content_filtered", LLMContentFiltered),
    Case("rate_limited", LLMRateLimited, retryable=True),
    Case("unavailable", LLMUnavailable, retryable=True),
    Case("auth", LLMAuthError),
]

# Expected usage of the "ok" case (same as the `completed` fixture).
OK_USAGE = (1234, 56, 200)
OK_REQUEST_ID = "req_test_completed"


class Harness(Protocol):
    name: str

    def arrange(self, case: str) -> contextlib.AbstractContextManager[LLMProvider]: ...


class FakeHarness:
    name = "fake"
    model = "test"

    def _item(self, case: str) -> FakeReply | LLMError:
        items: dict[str, FakeReply | LLMError] = {
            "ok": FakeReply(
                data=EXPECTED,
                input_tokens=OK_USAGE[0],
                output_tokens=OK_USAGE[1],
                cached_input_tokens=OK_USAGE[2],
                request_id=OK_REQUEST_ID,
            ),
            "not_json": FakeReply(raw_text="The total is 12.34 EUR."),
            "schema_violation": FakeReply(raw_text='{"vendor": "Frischmarkt Muster"}'),
            "refusal": LLMRefusal(provider="fake"),
            "truncated": LLMTruncated(provider="fake"),
            "content_filtered": LLMContentFiltered(provider="fake"),
            "rate_limited": LLMRateLimited(provider="fake", status_code=429, retry_after_s=2),
            "unavailable": LLMUnavailable(provider="fake", status_code=503),
            "auth": LLMAuthError(provider="fake", status_code=401),
        }
        return items[case]

    @contextlib.contextmanager
    def arrange(self, case: str) -> Iterator[LLMProvider]:
        yield FakeProvider([self._item(case)], app_env="test")


class OpenAIHarness:
    name = "openai"
    model = "gpt-test"
    fixtures = {
        "ok": "completed",
        "not_json": "not_json",
        "schema_violation": "schema_violation",
        "refusal": "refusal",
        "truncated": "incomplete_max_output_tokens",
        "content_filtered": "incomplete_content_filter",
        "rate_limited": "error_429_retry_after",
        "unavailable": "error_503",
        "auth": "error_401",
    }

    @contextlib.contextmanager
    def arrange(self, case: str) -> Iterator[LLMProvider]:
        with respx.mock(assert_all_mocked=True, assert_all_called=True) as mock:
            mock.post(RESPONSES_URL).mock(return_value=fixture_response(self.fixtures[case]))
            yield OpenAIProvider(llm_settings(openai_api_key="sk-test-contract-runtime-only"))


HARNESSES: list[Harness] = [FakeHarness(), OpenAIHarness()]
