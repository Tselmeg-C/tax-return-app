"""Error taxonomy of the LLM layer.

Class names are the contract: #6 stores `type(exc).__name__` as `error_kind`, and #9 maps
the groups to job outcomes (transient → job retry, output → `needs_attention`, permanent →
`PermanentJobError`). `str(exc)` is a fixed template (class, provider, HTTP status or a
fixed text from code). It never contains a response body, the SDK's message, a key, a
prompt or document text. The SDK exception is chained as `__cause__`; never log `str()` of it.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import ClassVar

from app.llm.types import LLMCallRecord


class LLMError(Exception):
    default_retryable: ClassVar[bool] = False
    summary: ClassVar[str] = "call failed"

    def __init__(
        self,
        *,
        provider: str = "",
        model: str = "",
        request_id: str | None = None,
        status_code: int | None = None,
        retryable: bool | None = None,
        calls: Sequence[LLMCallRecord] = (),
        detail: str | None = None,
    ) -> None:
        """`detail` must be a fixed text from code (e.g. a setting name), never a value."""
        super().__init__()
        self.provider = provider
        self.model = model
        self.request_id = request_id
        self.status_code = status_code
        self.retryable = self.default_retryable if retryable is None else retryable
        self.calls: tuple[LLMCallRecord, ...] = tuple(calls)
        self.detail = detail
        self.fallback_used = False

    def __str__(self) -> str:
        name = type(self).__name__
        if self.detail:
            return f"{name}: {self.detail}"
        who = self.provider or "llm"
        if self.status_code is not None:
            return f"{name}: {who} returned HTTP {self.status_code}"
        return f"{name}: {who} {self.summary}"

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(provider={self.provider!r}, model={self.model!r}, "
            f"status_code={self.status_code!r}, request_id={self.request_id!r}, "
            f"retryable={self.retryable}, calls={len(self.calls)})"
        )


# --- transient: retried on the same model, then fallback --------------------------------


class LLMTransientError(LLMError):
    default_retryable = True


class LLMTimeout(LLMTransientError):
    summary = "timed out"


class LLMRateLimited(LLMTransientError):
    summary = "rate limited the call"

    def __init__(self, *, retry_after_s: float | None = None, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.retry_after_s = retry_after_s


class LLMUnavailable(LLMTransientError):
    summary = "is unavailable"


# --- output: re-asked (schema / truncated) or fallback only (refusal / filter) -----------


class LLMOutputError(LLMError):
    default_retryable = False


class LLMSchemaValidationError(LLMOutputError):
    summary = "returned output that does not match the schema"

    def __init__(
        self, *, error_types: Sequence[str] = (), error_count: int = 0, **kwargs: object
    ) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.error_types: tuple[str, ...] = tuple(error_types)  # pydantic error types only
        self.error_count = error_count


class LLMTruncated(LLMOutputError):
    summary = "stopped at max_output_tokens"


class LLMRefusal(LLMOutputError):
    summary = "refused the request"


class LLMContentFiltered(LLMOutputError):
    summary = "filtered the output"


# --- permanent: no retry, no fallback --------------------------------------------------


class LLMPermanentError(LLMError):
    default_retryable = False


class LLMAuthError(LLMPermanentError):
    summary = "rejected the credentials"


class LLMQuotaExceeded(LLMPermanentError):
    summary = "reports the quota is exhausted"


class LLMBadRequest(LLMPermanentError):
    summary = "rejected the request"


class LLMInputTooLarge(LLMPermanentError):
    summary = "input is too large"


class LLMInputInvalid(LLMPermanentError):
    summary = "input is invalid"


class LLMNotConfigured(LLMPermanentError):
    summary = "is not configured"


class LLMSchemaUnsupported(LLMPermanentError):
    summary = "schema cannot be expressed as a strict JSON schema"


def is_permanent(exc: BaseException) -> bool:
    """True if retrying the job cannot help (#9 maps it to `PermanentJobError`)."""
    if isinstance(exc, LLMError):
        return not exc.retryable
    return False


__all__ = [
    "LLMAuthError",
    "LLMBadRequest",
    "LLMContentFiltered",
    "LLMError",
    "LLMInputInvalid",
    "LLMInputTooLarge",
    "LLMNotConfigured",
    "LLMOutputError",
    "LLMPermanentError",
    "LLMQuotaExceeded",
    "LLMRateLimited",
    "LLMRefusal",
    "LLMSchemaUnsupported",
    "LLMSchemaValidationError",
    "LLMTimeout",
    "LLMTransientError",
    "LLMTruncated",
    "LLMUnavailable",
    "is_permanent",
]
