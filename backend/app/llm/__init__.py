"""Provider-agnostic LLM layer: structured output from text, images and PDFs.

Call LLMs only through `get_router().structured(...)`. No DB access, and no imports of
`app.queue` or `evals` here. See `_docs/llm.md`.
"""

from app.llm.errors import (
    LLMAuthError,
    LLMBadRequest,
    LLMContentFiltered,
    LLMError,
    LLMInputInvalid,
    LLMInputTooLarge,
    LLMNotConfigured,
    LLMOutputError,
    LLMPermanentError,
    LLMQuotaExceeded,
    LLMRateLimited,
    LLMRefusal,
    LLMSchemaUnsupported,
    LLMSchemaValidationError,
    LLMTimeout,
    LLMTransientError,
    LLMTruncated,
    LLMUnavailable,
    is_permanent,
)
from app.llm.provider import LLMProvider
from app.llm.router import LLMRouter, get_router
from app.llm.types import (
    ImagePart,
    LLMCallRecord,
    LLMRequest,
    LLMResult,
    LLMUsage,
    Part,
    PdfPart,
    TextPart,
)

__all__ = [
    "ImagePart",
    "LLMAuthError",
    "LLMBadRequest",
    "LLMCallRecord",
    "LLMContentFiltered",
    "LLMError",
    "LLMInputInvalid",
    "LLMInputTooLarge",
    "LLMNotConfigured",
    "LLMOutputError",
    "LLMPermanentError",
    "LLMProvider",
    "LLMQuotaExceeded",
    "LLMRateLimited",
    "LLMRefusal",
    "LLMRequest",
    "LLMResult",
    "LLMRouter",
    "LLMSchemaUnsupported",
    "LLMSchemaValidationError",
    "LLMTimeout",
    "LLMTransientError",
    "LLMTruncated",
    "LLMUnavailable",
    "LLMUsage",
    "Part",
    "PdfPart",
    "TextPart",
    "get_router",
    "is_permanent",
]
