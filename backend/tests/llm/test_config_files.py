"""pricing.yaml / routing.yaml: committed files, loading rules, env overrides, validation."""

from __future__ import annotations

import copy
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import pytest

from app.llm.errors import LLMNotConfigured
from app.llm.pricing import load_pricing, parse_pricing
from app.llm.provider import registered_providers
from app.llm.router import LLMRouter
from app.llm.routing import apply_env_overrides, load_routing, validate_routing
from app.llm.types import LLMUsage

from .helpers import BASE_PRICING, llm_settings, pricing, routing


def test_committed_files_pass_validation() -> None:
    committed_pricing = load_pricing()
    committed_routing = load_routing()
    validate_routing(
        committed_routing,
        pricing=committed_pricing,
        providers=registered_providers(),
        app_env="production",
    )
    for route in committed_routing.tasks.values():
        for ref in (route.model, *route.fallback):
            assert ref.provider != "fake"
            assert ref.key in committed_routing.models
            assert committed_pricing.has(ref.key)
    assert all(price.source for price in committed_pricing.models.values())
    assert "20" in committed_pricing.fx_source  # dated
    assert {"classify", "extract"} <= set(committed_routing.tasks)
    # the committed defaults build a router without a key
    LLMRouter(llm_settings(), pricing=committed_pricing, routing=committed_routing)


def test_hand_computed_cost() -> None:
    raw = copy.deepcopy(BASE_PRICING)
    table = parse_pricing(raw)
    price = table.models["openai:gpt-test"]
    usage = LLMUsage(input_tokens=12_345, output_tokens=678, cached_input_tokens=2_000)
    expected = (
        (
            Decimal(10_345) * Decimal("0.0004")
            + Decimal(2_000) * Decimal("0.0001")
            + Decimal(678) * Decimal("0.0016")
        )
        / Decimal(1000)
        * Decimal("0.9")
    ).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    cost = table.cost_eur(price, usage)
    assert cost == expected and str(cost) == str(expected)
    assert str(cost) == "0.004881"
    eur = table.models["fake:test"]  # EUR: not converted
    assert table.cost_eur(eur, LLMUsage(input_tokens=1000, output_tokens=1000)) == Decimal(
        "0.003000"
    )


def _broken(model_patch: dict[str, Any]) -> dict[str, Any]:
    raw = copy.deepcopy(BASE_PRICING)
    raw["models"]["fake:test"].update(model_patch)
    return raw


@pytest.mark.parametrize(
    "raw",
    [
        _broken({"input_per_1k": 0.001}),  # YAML float
        _broken({"output_per_1k": "-0.1"}),
        _broken({"discount": "0.1"}),  # unknown key
        {**copy.deepcopy(BASE_PRICING), "extra": 1},
        {**copy.deepcopy(BASE_PRICING), "fx": {"usd_eur": "0.9", "source": "ECB, no date"}},
    ],
)
def test_invalid_pricing_fails_to_load(raw: dict[str, Any]) -> None:
    with pytest.raises(LLMNotConfigured):
        parse_pricing(raw)


def test_float_price_in_yaml_text_fails(tmp_path: Any) -> None:
    path = tmp_path / "pricing.yaml"
    path.write_text(
        'version: "x"\nfx: {usd_eur: "0.9", source: "ECB, 2026-01-01"}\n'
        'models: {"fake:test": {currency: EUR, input_per_1k: 0.001, '
        'output_per_1k: "0.002", source: "t"}}\n'
    )
    with pytest.raises(LLMNotConfigured) as info:
        load_pricing(path)
    assert "input_per_1k" in str(info.value)


def _build(settings_values: dict[str, Any], **tasks: dict[str, Any]) -> LLMRouter:
    return LLMRouter(
        llm_settings(**settings_values), routing=routing(**tasks), pricing=pricing(), providers={}
    )


@pytest.mark.parametrize(
    ("settings_values", "tasks", "key", "secret"),
    [
        ({"llm_fallback_model": "anthropic:x"}, {}, "LLM_FALLBACK_MODEL", "anthropic:x"),
        (
            {"llm_classify_model": "openai:unpriced-sentinel-model"},
            {},
            "LLM_CLASSIFY_MODEL",
            "unpriced-sentinel-model",
        ),
        ({"app_env": "production"}, {}, "APP_ENV=production", "fake:test"),
        ({}, {"classify": {"model": "nope:sentinel-model"}}, "tasks.classify.model", "sentinel"),
    ],
)
def test_routing_validation_names_the_key(
    settings_values: dict[str, Any], tasks: dict[str, Any], key: str, secret: str
) -> None:
    with pytest.raises(LLMNotConfigured) as info:
        _build(settings_values, **tasks)
    assert key in str(info.value)
    assert secret not in str(info.value) and secret not in repr(info.value)


def test_routed_model_missing_from_pricing() -> None:
    raw_tasks = {"classify": {"model": "fake:raster"}}
    table = pricing()
    del table.models["fake:raster"]
    with pytest.raises(LLMNotConfigured) as info:
        LLMRouter(llm_settings(), routing=routing(**raw_tasks), pricing=table, providers={})
    assert "pricing.yaml" in str(info.value)
    assert "tasks.classify.model" in str(info.value)
    assert "raster" not in str(info.value)


def test_anthropic_message_names_the_provider() -> None:
    with pytest.raises(LLMNotConfigured) as info:
        _build({"llm_fallback_model": "anthropic:x"})
    assert "anthropic" in str(info.value) and "#23" in str(info.value)


async def test_unknown_task_is_not_configured() -> None:
    router = _build({})
    with pytest.raises(LLMNotConfigured) as info:
        router.routing.route("summarise-sentinel")
    assert "summarise-sentinel" not in str(info.value)


def test_env_overrides() -> None:
    base = routing(extract={"fallback": ["fake:fallback"]})
    overridden = apply_env_overrides(base, llm_settings(llm_extract_model="openai:other"))
    assert overridden.tasks["extract"].model.key == "openai:other"
    assert overridden.tasks["classify"].model.key == "fake:test"
    router = LLMRouter(
        llm_settings(llm_extract_model="openai:other"), routing=base, pricing=pricing()
    )
    assert router.routing.tasks["extract"].model.key == "openai:other"

    none = apply_env_overrides(base, llm_settings(llm_fallback_model="none"))
    assert all(route.fallback == () for route in none.tasks.values())
    many = apply_env_overrides(base, llm_settings(llm_fallback_model="fake:fallback, openai:other"))
    assert [r.key for r in many.tasks["classify"].fallback] == ["fake:fallback", "openai:other"]


def test_malformed_model_string_names_the_key() -> None:
    with pytest.raises(LLMNotConfigured) as info:
        _build({"llm_classify_model": "no-colon-sentinel"})
    assert "LLM_CLASSIFY_MODEL" in str(info.value)
    assert "no-colon-sentinel" not in str(info.value)
