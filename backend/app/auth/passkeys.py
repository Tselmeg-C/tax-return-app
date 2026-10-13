"""Passkey ceremony helpers (#12): stateless challenge token and `webauthn` wrappers.

The challenge lives in a short-lived HMAC-signed httpOnly cookie (purpose + challenge + user id),
no table. The signing key is random per process (`app.state.passkey_key`): v1 runs one api
process, so a restart only invalidates challenges still open (5 min). ponytail: with several
replicas derive the key from a shared secret instead.

Never log challenges, credential ids, keys, client data, signatures or `str(exc)` of the lib.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from webauthn import (
    base64url_to_bytes,
    generate_authentication_options,
    generate_registration_options,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import bytes_to_base64url, options_to_json_dict
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.config import Settings

CHALLENGE_TTL = timedelta(minutes=5)
MAX_PASSKEYS = 10
MAX_NAME_LENGTH = 60
MAX_CREDENTIAL_ID_LENGTH = 1024
RP_NAME = "belegbot"

REGISTER = "register"
LOGIN = "login"


def rp_id(settings: Settings) -> str:
    return urlsplit(settings.app_base_url).hostname or "localhost"


def challenge_cookie_name(secure: bool) -> str:
    return "__Host-belegbot_passkey" if secure else "belegbot_passkey"


def challenge_cookie(value: str, *, secure: bool, max_age: int) -> str:
    attrs = [f"{challenge_cookie_name(secure)}={value}", "HttpOnly"]
    if secure:
        attrs.append("Secure")
    attrs += ["SameSite=Strict", "Path=/", f"Max-Age={max_age}"]
    return "; ".join(attrs)


def _sign(key: bytes, payload: str) -> str:
    return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()


def seal(
    key: bytes, *, purpose: str, challenge: bytes, user_id: uuid.UUID | None, now: datetime
) -> str:
    body = {
        "p": purpose,
        "c": bytes_to_base64url(challenge),
        "u": str(user_id) if user_id else None,
        "e": int((now + CHALLENGE_TTL).timestamp()),
    }
    payload = base64.urlsafe_b64encode(json.dumps(body).encode()).decode()
    return f"{payload}.{_sign(key, payload)}"


def unseal(
    key: bytes, token: str | None, *, purpose: str, now: datetime
) -> tuple[bytes, uuid.UUID | None] | None:
    """Challenge and user id, or `None` if tampered, expired or of another purpose."""
    try:
        if not token or len(token) > 1024:
            return None
        payload, _, sig = token.partition(".")
        if not hmac.compare_digest(sig, _sign(key, payload)):
            return None
        body = json.loads(base64.urlsafe_b64decode(payload))
        if body["p"] != purpose or now.timestamp() >= body["e"]:
            return None
        user = uuid.UUID(body["u"]) if body["u"] else None
        return base64url_to_bytes(body["c"]), user
    except Exception:
        return None


def registration_options(
    settings: Settings, *, user_id: uuid.UUID, email: str, existing: list[bytes]
) -> tuple[dict[str, Any], bytes]:
    options = generate_registration_options(
        rp_id=rp_id(settings),
        rp_name=RP_NAME,
        user_id=user_id.bytes,
        user_name=email,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        exclude_credentials=[PublicKeyCredentialDescriptor(id=c) for c in existing],
    )
    return options_to_json_dict(options), options.challenge


def authentication_options(settings: Settings) -> tuple[dict[str, Any], bytes]:
    options = generate_authentication_options(
        rp_id=rp_id(settings), user_verification=UserVerificationRequirement.REQUIRED
    )
    return options_to_json_dict(options), options.challenge


def verify_registration(
    settings: Settings, credential: dict[str, Any], challenge: bytes
) -> tuple[bytes, bytes, int]:
    """(credential id, COSE public key, sign count); raises on any failure."""
    verified = verify_registration_response(
        credential=credential,
        expected_challenge=challenge,
        expected_rp_id=rp_id(settings),
        expected_origin=settings.app_origin,
        require_user_verification=True,
    )
    return verified.credential_id, verified.credential_public_key, verified.sign_count


def verify_authentication(
    settings: Settings,
    credential: dict[str, Any],
    challenge: bytes,
    *,
    public_key: bytes,
    sign_count: int,
) -> int:
    """The new sign count; raises on any failure (incl. a sign count that does not increase)."""
    verified = verify_authentication_response(
        credential=credential,
        expected_challenge=challenge,
        expected_rp_id=rp_id(settings),
        expected_origin=settings.app_origin,
        credential_public_key=public_key,
        credential_current_sign_count=sign_count,
        require_user_verification=True,
    )
    return verified.new_sign_count


def raw_credential_id(credential: object) -> bytes | None:
    """The `rawId` of a client credential dict, or `None` if it is not a plausible one."""
    if not isinstance(credential, dict):
        return None
    raw = credential.get("rawId")
    if not isinstance(raw, str) or not raw or len(raw) > MAX_CREDENTIAL_ID_LENGTH:
        return None
    try:
        return base64url_to_bytes(raw)
    except Exception:
        return None


def clean_transports(credential: dict[str, Any]) -> list[str] | None:
    response = credential.get("response")
    raw = response.get("transports") if isinstance(response, dict) else None
    if not isinstance(raw, list):
        return None
    return [t for t in raw if isinstance(t, str) and t.isalpha() and len(t) <= 20][:10] or None
