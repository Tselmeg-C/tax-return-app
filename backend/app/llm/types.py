"""Request / result types of the LLM layer.

`repr()` of every type here shows sizes, ids and numbers only: never prompt text, part
bytes, response text or parsed data (document content).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

ImageMime = Literal["image/jpeg", "image/png", "image/webp", "image/gif"]
PdfInputMode = Literal["native", "rasterize"]


@dataclass(frozen=True)
class TextPart:
    text: str = field(repr=False)

    def __repr__(self) -> str:
        return f"TextPart(chars={len(self.text)})"


@dataclass(frozen=True)
class ImagePart:
    data: bytes = field(repr=False)
    mime: ImageMime

    def __repr__(self) -> str:
        return f"ImagePart(mime={self.mime!r}, bytes={len(self.data)})"


@dataclass(frozen=True)
class PdfPart:
    """A PDF document. Never carries a file name (providers send a constant one)."""

    data: bytes = field(repr=False)

    def __repr__(self) -> str:
        return f"PdfPart(bytes={len(self.data)})"


Part = TextPart | ImagePart | PdfPart


@dataclass(frozen=True)
class LLMRequest[T: BaseModel]:
    """One provider call. `parts` have already passed the input preflight (`inputs.py`)."""

    system: str = field(repr=False)
    parts: Sequence[Part]
    schema: type[T]
    model: str  # provider-side model id, e.g. the part after "openai:"
    temperature: float | None  # None = not sent (provider default / unsupported)
    max_output_tokens: int
    timeout_s: float
    task: str  # "classify" | "extract" | … (telemetry label only)
    prompt_version: str  # telemetry only

    def __repr__(self) -> str:
        return (
            f"LLMRequest(task={self.task!r}, model={self.model!r}, "
            f"schema={self.schema.__name__}, parts={len(self.parts)}, "
            f"system_chars={len(self.system)}, temperature={self.temperature!r}, "
            f"max_output_tokens={self.max_output_tokens}, timeout_s={self.timeout_s})"
        )


@dataclass(frozen=True)
class LLMUsage:
    input_tokens: int = 0
    output_tokens: int = 0  # includes reasoning tokens (billed as output)
    cached_input_tokens: int = 0


@dataclass(frozen=True)
class LLMCallRecord:
    """One HTTP attempt, successful or not. #9 persists one `extraction` row per record.

    Providers fill in what the response says; the router sets `attempt`, `cost_eur` and
    `cost_known` (pricing is provider-agnostic).
    """

    provider: str  # "openai"
    model: str  # requested model id (stable, low cardinality; pricing key)
    response_model: str | None  # model the API reports (dated snapshot)
    task: str
    attempt: int  # 1-based, across retries and fallback
    outcome: str  # "ok" or the error class name, e.g. "LLMRateLimited"
    input_tokens: int  # 0 if the provider returned no usage
    output_tokens: int  # includes reasoning tokens
    cached_input_tokens: int
    cost_eur: Decimal  # 6 places; 0 when no usage
    cost_known: bool  # False if the model is missing from pricing.yaml (or no usage)
    latency_ms: int  # monotonic wall time of this HTTP call
    request_id: str | None  # x-request-id header, else the response id
    response_id: str | None  # e.g. "resp_…"


@dataclass(frozen=True)
class LLMResult[T: BaseModel]:
    data: T = field(repr=False)  # parsed + validated instance of request.schema
    raw_text: str = field(repr=False)  # exact text the model returned: never log it
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_eur: Decimal  # of the successful call; total = sum(c.cost_eur for c in calls)
    latency_ms: int
    request_id: str | None
    pricing_version: str
    fallback_used: bool
    calls: tuple[LLMCallRecord, ...]  # every attempt incl. failed ones

    def __repr__(self) -> str:
        return (
            f"LLMResult(provider={self.provider!r}, model={self.model!r}, "
            f"schema={type(self.data).__name__}, input_tokens={self.input_tokens}, "
            f"output_tokens={self.output_tokens}, cost_eur={self.cost_eur}, "
            f"latency_ms={self.latency_ms}, request_id={self.request_id!r}, "
            f"pricing_version={self.pricing_version!r}, fallback_used={self.fallback_used}, "
            f"calls={len(self.calls)})"
        )
