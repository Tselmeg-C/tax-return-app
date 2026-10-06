"""Router behaviour with the fake provider and an injected clock / sleep (never sleeps)."""

from __future__ import annotations

import pytest

from app.llm import TextPart, is_permanent
from app.llm.errors import (
    LLMAuthError,
    LLMBadRequest,
    LLMContentFiltered,
    LLMInputTooLarge,
    LLMNotConfigured,
    LLMQuotaExceeded,
    LLMRateLimited,
    LLMRefusal,
    LLMSchemaValidationError,
    LLMTimeout,
    LLMTruncated,
    LLMUnavailable,
)
from app.llm.fake import FakeProvider, FakeReply, FakeScriptExhausted
from app.llm.router import LLMRouter

from .helpers import (
    EXPECTED,
    FakeClock,
    Otel,
    Synthetic,
    llm_settings,
    make_router,
    pricing,
    routing,
)

PARTS = [TextPart("synthetic input")]
OK = FakeReply(data=EXPECTED, request_id="req_test_ok")
INVALID = FakeReply(raw_text='{"vendor": "Frischmarkt Muster"}', input_tokens=50, output_tokens=7)


async def call(router: LLMRouter, task: str = "classify") -> object:
    return await router.structured(
        task=task, system="sys", parts=PARTS, schema=Synthetic, prompt_version="p1"
    )


def fake(*script: object, **kwargs: object) -> FakeProvider:
    return FakeProvider(list(script), app_env="test", **kwargs)  # type: ignore[arg-type]


def unavailable() -> LLMUnavailable:
    return LLMUnavailable(provider="fake", model="test", status_code=503)


async def test_transient_retries_with_backoff(otel: Otel) -> None:
    eps = 1e-9
    for rng, low_high in ((0.0, (0.8, 1.6)), (1.0, (1.2, 2.4))):
        clock = FakeClock()
        provider = fake(unavailable(), unavailable(), OK)
        router = make_router(providers={"fake": provider}, otel=otel, clock=clock, rng=rng)
        result = await call(router)
        assert len(result.calls) == 3  # type: ignore[attr-defined]
        assert clock.sleeps == pytest.approx(list(low_high))
        assert 0.8 - eps <= clock.sleeps[0] <= 1.2 + eps
        assert 1.6 - eps <= clock.sleeps[1] <= 2.4 + eps
    assert otel.total("belegbot.llm.retries", reason="LLMUnavailable") == 4  # 2 per run


async def test_three_unavailable_without_fallback_raises_retryable(otel: Otel) -> None:
    router = make_router(providers={"fake": fake(*(unavailable() for _ in range(3)))}, otel=otel)
    with pytest.raises(LLMUnavailable) as info:
        await call(router)
    assert info.value.retryable is True
    assert len(info.value.calls) == 3
    assert [c.attempt for c in info.value.calls] == [1, 2, 3]
    assert info.value.fallback_used is False


async def test_fallback_after_primary_exhausted(otel: Otel) -> None:
    provider = fake(*(unavailable() for _ in range(3)), OK)
    router = make_router(
        providers={"fake": provider},
        routing_config=routing(classify={"fallback": ["fake:fallback"]}),
        otel=otel,
    )
    result = await call(router)
    assert result.fallback_used is True  # type: ignore[attr-defined]
    assert [c.model for c in provider.calls] == ["test", "test", "test", "fallback"]
    assert otel.total("belegbot.llm.fallbacks") == 1
    assert otel.total("belegbot.llm.fallbacks", from_model="fake:test", to_model="fake:fallback")


async def test_rate_limited_honours_retry_after() -> None:
    clock = FakeClock()
    provider = fake(LLMRateLimited(provider="fake", status_code=429, retry_after_s=2), OK)
    await call(make_router(providers={"fake": provider}, clock=clock))
    assert clock.sleeps == [2]


async def test_rate_limited_with_long_retry_after_raises_at_once() -> None:
    clock = FakeClock()
    provider = fake(LLMRateLimited(provider="fake", status_code=429, retry_after_s=120), OK)
    router = make_router(
        providers={"fake": provider},
        clock=clock,
        routing_config=routing(classify={"fallback": ["fake:fallback"]}),
    )
    with pytest.raises(LLMRateLimited) as info:
        await call(router)
    assert clock.sleeps == []
    assert info.value.retryable is True
    assert info.value.retry_after_s == 120
    assert len(provider.calls) == 1  # no fallback either


async def test_provider_outage_with_fallback() -> None:
    settings = llm_settings()
    provider = fake(*(unavailable() for _ in range(2 * settings.llm_max_attempts)))
    router = make_router(
        providers={"fake": provider},
        settings=settings,
        routing_config=routing(classify={"fallback": ["fake:fallback"]}),
    )
    with pytest.raises(LLMUnavailable) as info:
        await call(router)
    assert len(provider.calls) == 2 * settings.llm_max_attempts
    assert provider.remaining == 0
    assert info.value.retryable is True
    assert is_permanent(info.value) is False
    assert info.value.fallback_used is True


async def test_schema_invalid_then_ok(otel: Otel) -> None:
    router = make_router(providers={"fake": fake(INVALID, OK)}, otel=otel)
    result = await call(router)
    assert len(result.calls) == 2  # type: ignore[attr-defined]
    assert otel.total("belegbot.llm.calls", outcome="LLMSchemaValidationError") == 1
    assert otel.total("belegbot.llm.calls", outcome="ok") == 1


async def test_schema_invalid_twice_then_fallback_ok() -> None:
    provider = fake(INVALID, INVALID, OK)
    router = make_router(
        providers={"fake": provider},
        routing_config=routing(classify={"fallback": ["fake:fallback"]}),
    )
    result = await call(router)
    assert result.fallback_used is True  # type: ignore[attr-defined]
    assert [c.model for c in provider.calls] == ["test", "test", "fallback"]


async def test_schema_invalid_everywhere_is_permanent() -> None:
    provider = fake(INVALID, INVALID, INVALID, INVALID)
    router = make_router(
        providers={"fake": provider},
        routing_config=routing(classify={"fallback": ["fake:fallback"]}),
    )
    with pytest.raises(LLMSchemaValidationError) as info:
        await call(router)
    assert info.value.retryable is False
    assert is_permanent(info.value) is True
    assert len(info.value.calls) == 4
    assert all(c.outcome == "LLMSchemaValidationError" for c in info.value.calls)
    assert all(c.input_tokens == 50 and c.cost_eur > 0 for c in info.value.calls)
    assert "missing" in info.value.error_types


async def test_truncated_reask_doubles_max_tokens_up_to_cap() -> None:
    truncated = LLMTruncated(provider="fake")
    provider = fake(truncated, OK)
    await call(make_router(providers={"fake": provider}))
    assert [c.max_output_tokens for c in provider.calls] == [1000, 2000]

    provider = fake(truncated, truncated, OK)
    router = make_router(
        providers={"fake": provider},
        routing_config=routing(classify={"max_output_tokens": 2000, "schema_retries": 2}),
    )
    await call(router)
    # model cap for fake:test is 3000
    assert [c.max_output_tokens for c in provider.calls] == [2000, 3000, 3000]


@pytest.mark.parametrize("error", [LLMRefusal, LLMContentFiltered])
async def test_refusal_and_filter_skip_reask(error: type) -> None:
    provider = fake(error(provider="fake"), error(provider="fake"))
    with pytest.raises(error):
        await call(make_router(providers={"fake": provider}))
    assert len(provider.calls) == 1

    provider = fake(error(provider="fake"), OK)
    router = make_router(
        providers={"fake": provider},
        routing_config=routing(classify={"fallback": ["fake:fallback"]}),
    )
    result = await call(router)
    assert [c.model for c in provider.calls] == ["test", "fallback"]
    assert result.fallback_used is True  # type: ignore[attr-defined]


@pytest.mark.parametrize("error", [LLMAuthError, LLMQuotaExceeded, LLMBadRequest, LLMInputTooLarge])
async def test_permanent_errors_are_not_retried(error: type, otel: Otel) -> None:
    provider = fake(error(provider="fake", status_code=400), OK, OK)
    router = make_router(
        providers={"fake": provider},
        routing_config=routing(classify={"fallback": ["fake:fallback"]}),
        otel=otel,
    )
    with pytest.raises(error) as info:
        await call(router)
    assert type(info.value) is error
    assert info.value.retryable is False
    assert len(provider.calls) == 1
    assert otel.total("belegbot.llm.fallbacks") == 0
    assert otel.total("belegbot.llm.retries") == 0


async def test_deadline_raises_timeout() -> None:
    clock = FakeClock()
    provider = fake(*(LLMTimeout(provider="fake") for _ in range(10)), delay_s=3, sleep=clock.sleep)
    router = make_router(
        providers={"fake": provider},
        settings=llm_settings(llm_deadline_seconds=5),
        clock=clock,
        routing_config=routing(classify={"fallback": ["fake:fallback"]}),
    )
    with pytest.raises(LLMTimeout) as info:
        await call(router)
    assert len(provider.calls) == 2
    assert clock.now <= 5
    assert info.value.retryable is True
    assert len(info.value.calls) == 2


async def test_fake_script_exhausted() -> None:
    with pytest.raises(FakeScriptExhausted):
        await call(make_router(providers={"fake": fake()}))


def test_fake_refused_in_production() -> None:
    with pytest.raises(LLMNotConfigured):
        FakeProvider([], app_env="production")
    with pytest.raises(LLMNotConfigured) as info:
        LLMRouter(
            llm_settings(app_env="production"),
            routing=routing(),
            pricing=pricing(),
        )
    assert "APP_ENV=production" in str(info.value)


async def test_unknown_task_raises_not_configured() -> None:
    with pytest.raises(LLMNotConfigured) as info:
        await call(make_router(script=[OK]), task="summarise")
    assert info.value.retryable is False


async def test_with_providers_overrides_per_instance() -> None:
    base = make_router(script=[])
    scripted = fake(OK)
    router = base.with_providers({"fake": scripted})
    result = await call(router)
    assert result.data == EXPECTED  # type: ignore[attr-defined]
    with pytest.raises(FakeScriptExhausted):
        await call(base)  # the original router keeps its own provider


async def test_fake_validates_any_schema_from_data() -> None:
    """`FakeReply(data=...)` is validated against the request schema (#9's perfect reader)."""
    router = make_router(script=[FakeReply(data=EXPECTED)])
    result = await call(router)
    assert isinstance(result.data, Synthetic)  # type: ignore[attr-defined]


async def test_success_result_fields() -> None:
    router = make_router(script=[FakeReply(data=EXPECTED, input_tokens=1000, output_tokens=500)])
    result = await call(router)
    assert result.provider == "fake" and result.model == "test"  # type: ignore[attr-defined]
    # EUR prices 0.001 / 0.002 per 1k: 1000 → 0.001, 500 → 0.001
    assert str(result.cost_eur) == "0.002000"  # type: ignore[attr-defined]
    assert result.pricing_version == "test-2026-01-01"  # type: ignore[attr-defined]
    assert result.calls[0].cost_known is True  # type: ignore[attr-defined]
    assert result.fallback_used is False  # type: ignore[attr-defined]


async def test_unpriced_model_override(otel: Otel, json_log: object) -> None:
    router = make_router(script=[OK, OK], otel=otel)
    for _ in range(2):
        result = await router.structured(
            task="classify",
            system="sys",
            parts=PARTS,
            schema=Synthetic,
            prompt_version="p1",
            model="fake:unpriced",
        )
        assert result.cost_eur == 0
        assert result.calls[0].cost_known is False
    assert otel.total("belegbot.llm.unpriced_calls") == 2
    lines = json_log.getvalue().splitlines()  # type: ignore[attr-defined]
    assert sum('"llm.unpriced_model"' in line for line in lines) == 1


async def test_model_override_rejects_unregistered_provider() -> None:
    router = make_router(script=[OK])
    with pytest.raises(LLMNotConfigured):
        await router.structured(
            task="classify",
            system="s",
            parts=PARTS,
            schema=Synthetic,
            prompt_version="p",
            model="anthropic:x",
        )


async def test_empty_parts_is_a_programming_error() -> None:
    router = make_router(script=[OK])
    with pytest.raises(ValueError):
        await router.structured(
            task="classify", system="s", parts=[], schema=Synthetic, prompt_version="p"
        )
