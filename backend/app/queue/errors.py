"""Job errors. Only class names (or a validated `error_kind`) are ever stored or logged."""

from __future__ import annotations

import re

ERROR_KIND_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]{0,99}$")


class PermanentJobError(Exception):
    """A non-retryable job failure: the job and its document end `failed` right away.

    `error_kind` (optional) is stored instead of the class name, e.g. an LLM error class
    (#9). It must look like a class name (`^[A-Za-z][A-Za-z0-9]{0,99}$`). The message is
    never stored or logged.
    """

    def __init__(self, message: str = "", *, error_kind: str | None = None) -> None:
        super().__init__(message)
        if error_kind is not None and not ERROR_KIND_RE.fullmatch(error_kind):
            raise ValueError("error_kind must look like a class name")
        self.error_kind = error_kind


class FileMissing(PermanentJobError):
    """The document's file is not in storage."""


class ChecksumMismatch(PermanentJobError):
    """The stored bytes no longer match `document.sha256`."""


class DocumentMissing(PermanentJobError):
    """The job's document row does not exist (any more)."""


class JobTimeout(Exception):
    """The handler ran longer than `JOB_TIMEOUT_SECONDS` (retryable)."""


LEASE_EXPIRED = "LeaseExpired"


def error_kind_of(exc: BaseException) -> str:
    """What gets stored as `last_error_kind` / `error_kind`: never a message."""
    if isinstance(exc, PermanentJobError) and exc.error_kind:
        return exc.error_kind
    return type(exc).__name__[:100]
