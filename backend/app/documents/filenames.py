"""Original file names: sanitising the `X-Filename` header and building `Content-Disposition`.

The name is display metadata only (#6 Decision 3). It is PII: never log it, never put it in
a span, metric, job row or error body, and never use it in a path or a `Storage` call.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from urllib.parse import quote, unquote_to_bytes

from app.documents.sniff import EXT_BY_MIME, EXTENSIONS_BY_MIME

MAX_HEADER_CHARS = 2048
MAX_NAME_BYTES = 255
MAX_EXT_BYTES = 16
EXT_VALUE_PREFIX = "utf-8''"
_WHITESPACE = re.compile(r"\s+")
_ASCII_FALLBACK_UNSAFE = re.compile(r'[^\x20-\x7e]|["\\;]')


def _truncate_utf8(text: str, limit: int) -> str:
    """At most `limit` UTF-8 bytes, cut on a character boundary."""
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    return encoded[:limit].decode("utf-8", errors="ignore")


def _split_ext(name: str) -> tuple[str, str]:
    """(`stem`, `.ext`) where `.ext` is the last `.xyz` (at most 16 bytes), else (`name`, "")."""
    dot = name.rfind(".")
    if dot <= 0:
        return name, ""
    ext = name[dot:]
    if len(ext) < 2 or len(ext.encode("utf-8")) > MAX_EXT_BYTES:
        return name, ""
    return name[:dot], ext


def sanitize_filename(raw: str | None) -> str | None:
    """The display name from an RFC 8187 `ext-value` (`UTF-8''<percent-encoded>`), or `None`.

    `None` for a missing, too long (> 2048 chars), unprefixed or non-UTF-8 value, and for
    names that are empty after cleaning. Never raises.
    """
    if raw is None or len(raw) > MAX_HEADER_CHARS:
        return None
    if raw[: len(EXT_VALUE_PREFIX)].lower() != EXT_VALUE_PREFIX:
        return None
    try:
        decoded = unquote_to_bytes(raw[len(EXT_VALUE_PREFIX) :]).decode("utf-8")
    except UnicodeDecodeError:
        return None
    name = unicodedata.normalize("NFC", decoded)
    name = re.split(r"[/\\]", name)[-1]
    name = "".join(ch for ch in name if unicodedata.category(ch) not in ("Cc", "Cf"))
    name = _WHITESPACE.sub(" ", name).strip(" .")
    if not name:
        return None
    if len(name.encode("utf-8")) > MAX_NAME_BYTES:
        stem, ext = _split_ext(name)
        if ext:
            stem = _truncate_utf8(stem, MAX_NAME_BYTES - len(ext.encode("utf-8"))).rstrip(" .")
            name = f"{stem}{ext}" if stem else _truncate_utf8(name, MAX_NAME_BYTES)
        else:
            name = _truncate_utf8(name, MAX_NAME_BYTES)
        name = name.strip(" .")
    return name or None


def download_name(original_filename: str | None, mime_type: str, document_id: uuid.UUID) -> str:
    """The name offered on download; appends the right extension if the stored one is wrong."""
    ext = EXT_BY_MIME.get(mime_type, "bin")
    if not original_filename:
        return f"beleg-{str(document_id)[:8]}.{ext}"
    _, current = _split_ext(original_filename)
    if current[1:].lower() in EXTENSIONS_BY_MIME.get(mime_type, frozenset({ext})):
        return original_filename
    return f"{original_filename}.{ext}"


def content_disposition(name: str, *, extended: bool = True) -> str:
    """`inline; filename="<ASCII fallback>"; filename*=UTF-8''<percent-encoded>` (RFC 6266).

    The fallback replaces every non-ASCII character, `"`, `\\` and `;` with `_`; neither
    part can contain CR or LF.
    """
    clean = "".join(ch for ch in name if unicodedata.category(ch) not in ("Cc", "Cf"))
    fallback = _ASCII_FALLBACK_UNSAFE.sub("_", clean)
    if not extended:
        return f'inline; filename="{fallback}"'
    encoded = quote(clean, safe="")
    return f"inline; filename=\"{fallback}\"; filename*=UTF-8''{encoded}"
