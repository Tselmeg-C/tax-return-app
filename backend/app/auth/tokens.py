"""Token, cookie, e-mail and redirect helpers (pure functions, no I/O)."""

from __future__ import annotations

import hashlib
import secrets

from pydantic import EmailStr, TypeAdapter, ValidationError

MAX_EMAIL_LENGTH = 320
MAX_NEXT_LENGTH = 512
# A raw token from `token_urlsafe(32)` is 43 chars; anything much longer is not ours.
MAX_TOKEN_LENGTH = 256

SECURE_COOKIE_NAME = "__Host-belegbot_session"
PLAIN_COOKIE_NAME = "belegbot_session"

_EMAIL = TypeAdapter(EmailStr)


def new_token() -> str:
    """256 random bits, URL-safe base64 without padding (43 chars)."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """`sha256(token)` as 64 lowercase hex chars: the only form stored in the DB."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def normalise_email(raw: object) -> str | None:
    """Strip + lowercase, then validate syntactically (no DNS). `None` if invalid.

    No plus-tag or dot folding. Display-name forms (`Name <a@b>`) are rejected.
    """
    if not isinstance(raw, str):
        return None
    email = raw.strip().lower()
    if not email or len(email) > MAX_EMAIL_LENGTH:
        return None
    try:
        validated = _EMAIL.validate_python(email)
    except ValidationError:
        return None
    return email if validated == email else None


def safe_next(raw: object) -> str:
    """A same-site path to go to after login; anything suspicious becomes `/`."""
    if not isinstance(raw, str) or not raw or len(raw) > MAX_NEXT_LENGTH:
        return "/"
    if not raw.startswith("/") or "//" in raw or "\\" in raw or ":" in raw.split("?", 1)[0]:
        return "/"
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in raw):
        return "/"
    return raw


def mask_email(email: str) -> str:
    """`owner@example.com` → `o***@example.com` (CLI output only)."""
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}" if domain else "***"


def cookie_name(secure: bool) -> str:
    # `__Host-` requires Secure, so plain http (local dev) uses the unprefixed name.
    return SECURE_COOKIE_NAME if secure else PLAIN_COOKIE_NAME


def session_cookie(value: str, *, max_age: int, secure: bool) -> str:
    """`Set-Cookie` value: host-only (no Domain), `Path=/`, relative `Max-Age`."""
    attrs = [f"{cookie_name(secure)}={value}", "HttpOnly"]
    if secure:
        attrs.append("Secure")
    attrs += ["SameSite=Lax", "Path=/", f"Max-Age={max(max_age, 0)}"]
    return "; ".join(attrs)


def clear_session_cookie(*, secure: bool) -> str:
    return session_cookie("", max_age=0, secure=secure)
