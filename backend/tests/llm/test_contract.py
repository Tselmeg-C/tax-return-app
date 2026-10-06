"""Runs the provider contract suite (`contract.py`) against every registered harness."""

from __future__ import annotations

import pytest

from app.llm import TextPart
from app.llm.errors import LLMError
from app.llm.types import LLMRequest

from .contract import CASES, HARNESSES, OK_REQUEST_ID, OK_USAGE, Case, Harness
from .helpers import EXPECTED, Synthetic


def _request(model: str) -> LLMRequest[Synthetic]:
    return LLMRequest(
        system="Extract the receipt.",
        parts=[TextPart("synthetic")],
        schema=Synthetic,
        model=model,
        temperature=0.0,
        max_output_tokens=1000,
        timeout_s=30,
        task="extract",
        prompt_version="p1",
    )


@pytest.mark.parametrize("harness", HARNESSES, ids=lambda h: h.name)
@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
async def test_provider_contract(harness: Harness, case: Case) -> None:
    model: str = harness.model  # type: ignore[attr-defined]
    with harness.arrange(case.name) as provider:
        assert provider.name == harness.name
        if case.error is None:
            result = await provider.structured(_request(model))
        else:
            with pytest.raises(LLMError) as info:
                await provider.structured(_request(model))
    if case.error is None:
        assert result.data == EXPECTED
        assert result.provider == harness.name and result.model == model
        assert (result.input_tokens, result.output_tokens) == OK_USAGE[:2]
        assert result.calls[0].cached_input_tokens == OK_USAGE[2]
        assert result.request_id == OK_REQUEST_ID
        assert len(result.calls) == 1 and result.calls[0].outcome == "ok"
        assert result.latency_ms >= 0
        return
    exc = info.value
    assert type(exc) is case.error
    assert exc.retryable is case.retryable
    assert exc.provider == harness.name
    assert str(exc).startswith(f"{case.error.__name__}: ")
    if case.billed:
        assert len(exc.calls) == 1
        assert exc.calls[0].outcome == case.error.__name__
        assert exc.calls[0].input_tokens > 0
