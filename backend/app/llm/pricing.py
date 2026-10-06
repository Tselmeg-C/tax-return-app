"""Pricing table (`config/pricing.yaml`) and `cost_eur()`.

Costs are estimates from list prices at a fixed, dated exchange rate. All arithmetic is
`Decimal`; prices are quoted decimal strings in the YAML (floats are rejected).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Any, Literal

import yaml

from app.llm.errors import LLMNotConfigured
from app.llm.types import LLMUsage

PRICING_PATH = Path(__file__).resolve().parent / "config" / "pricing.yaml"
COST_QUANTUM = Decimal("0.000001")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_TOP_KEYS = frozenset({"version", "fx", "models"})
_FX_KEYS = frozenset({"usd_eur", "source"})
_MODEL_KEYS = frozenset(
    {"currency", "input_per_1k", "cached_input_per_1k", "output_per_1k", "source"}
)
_REQUIRED_MODEL_KEYS = frozenset({"currency", "input_per_1k", "output_per_1k", "source"})


class PricingError(LLMNotConfigured):
    """pricing.yaml is invalid. The message names the key, never a value."""


@dataclass(frozen=True)
class ModelPrice:
    currency: Literal["USD", "EUR"]
    input_per_1k: Decimal
    cached_input_per_1k: Decimal
    output_per_1k: Decimal
    source: str


@dataclass(frozen=True)
class PricingTable:
    version: str
    usd_eur: Decimal
    fx_source: str
    models: dict[str, ModelPrice]

    def has(self, model_key: str) -> bool:
        return model_key in self.models

    def lookup(self, provider: str, model: str, response_model: str | None) -> ModelPrice | None:
        """First the requested `provider:model`, then `provider:<response_model>`."""
        price = self.models.get(f"{provider}:{model}")
        if price is None and response_model:
            price = self.models.get(f"{provider}:{response_model}")
        return price

    def cost_eur(self, price: ModelPrice, usage: LLMUsage) -> Decimal:
        with localcontext() as ctx:
            ctx.prec = 50
            cached = min(usage.cached_input_tokens, usage.input_tokens)
            uncached = usage.input_tokens - cached
            cost = (
                Decimal(uncached) * price.input_per_1k
                + Decimal(cached) * price.cached_input_per_1k
                + Decimal(usage.output_tokens) * price.output_per_1k
            ) / Decimal(1000)
            if price.currency == "USD":
                cost = cost * self.usd_eur
            return cost.quantize(COST_QUANTUM, rounding=ROUND_HALF_UP)


def _fail(key: str, problem: str) -> PricingError:
    return PricingError(detail=f"pricing.yaml {key}: {problem}")


def _decimal(value: Any, key: str) -> Decimal:
    if not isinstance(value, str):
        raise _fail(key, "must be a quoted decimal string")
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise _fail(key, "is not a decimal") from None
    if not number.is_finite() or number < 0:
        raise _fail(key, "must be a non-negative decimal")
    return number


def _text(value: Any, key: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _fail(key, "must be a non-empty string")
    return value


def _unknown(mapping: dict[str, Any], allowed: frozenset[str], key: str) -> None:
    if set(mapping) - allowed:
        raise _fail(key, "has unknown keys")


def parse_pricing(raw: Any) -> PricingTable:
    if not isinstance(raw, dict):
        raise _fail("<root>", "must be a mapping")
    _unknown(raw, _TOP_KEYS, "<root>")
    version = _text(raw.get("version"), "version")
    fx = raw.get("fx")
    if not isinstance(fx, dict):
        raise _fail("fx", "must be a mapping")
    _unknown(fx, _FX_KEYS, "fx")
    usd_eur = _decimal(fx.get("usd_eur"), "fx.usd_eur")
    if usd_eur == 0:
        raise _fail("fx.usd_eur", "must be positive")
    fx_source = _text(fx.get("source"), "fx.source")
    if not _DATE.search(fx_source):
        raise _fail("fx.source", "must contain the date of the rate (yyyy-mm-dd)")
    models_raw = raw.get("models")
    if not isinstance(models_raw, dict):
        raise _fail("models", "must be a mapping")
    models: dict[str, ModelPrice] = {}
    for name, entry in models_raw.items():
        key = f"models.{name}"
        if not isinstance(entry, dict):
            raise _fail(key, "must be a mapping")
        _unknown(entry, _MODEL_KEYS, key)
        if _REQUIRED_MODEL_KEYS - set(entry):
            raise _fail(key, "misses currency, input_per_1k, output_per_1k or source")
        currency = entry["currency"]
        if currency not in ("USD", "EUR"):
            raise _fail(f"{key}.currency", "must be USD or EUR")
        input_price = _decimal(entry["input_per_1k"], f"{key}.input_per_1k")
        cached = entry.get("cached_input_per_1k")
        models[str(name)] = ModelPrice(
            currency=currency,
            input_per_1k=input_price,
            cached_input_per_1k=(
                input_price if cached is None else _decimal(cached, f"{key}.cached_input_per_1k")
            ),
            output_per_1k=_decimal(entry["output_per_1k"], f"{key}.output_per_1k"),
            source=_text(entry["source"], f"{key}.source"),
        )
    return PricingTable(version=version, usd_eur=usd_eur, fx_source=fx_source, models=models)


def load_pricing(path: Path = PRICING_PATH) -> PricingTable:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        raise _fail("<file>", "cannot be read or parsed") from None
    return parse_pricing(raw)
