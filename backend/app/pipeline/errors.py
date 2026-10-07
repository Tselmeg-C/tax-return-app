"""Files the pipeline cannot decode: the job ends `failed` at once (`error_kind` = class name).

The message is never stored or logged; #6's UI asks the user to upload the file again.
"""

from __future__ import annotations

from app.queue.errors import PermanentJobError


class CorruptDocument(PermanentJobError):
    """The image or PDF cannot be decoded (truncated, damaged)."""


class EncryptedPdf(PermanentJobError):
    """The PDF is password-protected."""


class TooManyPages(PermanentJobError):
    """More pages / frames than `PIPELINE_MAX_PAGES` (never truncated)."""


class ImageTooLarge(PermanentJobError):
    """More pixels than `LLM_MAX_IMAGE_PIXELS` (decompression bomb guard)."""


class UnsupportedImageFormat(PermanentJobError):
    """A stored type no decoder here can convert (JPEG XL in v1)."""
