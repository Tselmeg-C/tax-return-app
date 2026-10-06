"""Live OpenAI test (paid). Deselected by default; run with `uv run pytest -m live`.

Skipped when `OPENAI_API_KEY` is unset or empty. Inputs are synthetic, generated at runtime.
Only pass/fail, model, tokens and cost may be reported, never content.
"""

from __future__ import annotations

import pytest

from app.config import LLMSettings
from app.llm.router import LLMRouter
from app.llm.smoke import SMOKE_PROMPT_VERSION, SMOKE_SYSTEM, SmokeClassification, synthetic_parts

pytestmark = pytest.mark.live


async def test_openai_classify_live() -> None:
    settings = LLMSettings()
    if settings.openai_api_key is None:
        pytest.skip("OPENAI_API_KEY is not set; live OpenAI test skipped")
    router = LLMRouter(settings)
    try:
        result = await router.structured(
            task="classify",
            system=SMOKE_SYSTEM,
            parts=synthetic_parts(),
            schema=SmokeClassification,
            prompt_version=SMOKE_PROMPT_VERSION,
        )
    finally:
        await router.aclose()
    assert isinstance(result.data, SmokeClassification)
    assert result.provider == "openai"
    assert result.input_tokens > 0 and result.output_tokens > 0
    assert result.cost_eur > 0 and result.calls[-1].cost_known
    assert result.request_id
    print(
        f"\nlive: model={result.model} calls={len(result.calls)} input_tokens="
        f"{result.input_tokens} output_tokens={result.output_tokens} cost_eur={result.cost_eur}"
    )
