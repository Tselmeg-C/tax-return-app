"""App-level field encryption (Fernet) for sensitive columns.

The key comes from `Settings.field_encryption_key` (`FIELD_ENCRYPTION_KEY`): a comma-separated
list of Fernet keys. The first key encrypts; every key can decrypt (`MultiFernet`), so a key can
be rotated by prepending a new one. The key is only loaded on the first encrypt/decrypt, so the
app and Alembic start without it.

Error messages never contain a key, a ciphertext or a plaintext.
"""

from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.config import get_settings

KEY_ENV_VAR = "FIELD_ENCRYPTION_KEY"


class EncryptionKeyError(RuntimeError):
    """`FIELD_ENCRYPTION_KEY` is missing or malformed. The message never contains the key."""


class DecryptionError(RuntimeError):
    """A stored value cannot be decrypted (wrong key or corrupted token)."""


@lru_cache(maxsize=4)
def _multifernet(raw_keys: str) -> MultiFernet:
    keys = [part.strip() for part in raw_keys.split(",") if part.strip()]
    if not keys:
        raise EncryptionKeyError(f"{KEY_ENV_VAR} is not set")
    try:
        return MultiFernet([Fernet(key) for key in keys])
    except Exception:
        # Fernet's own message could hint at the key's content; never chain it.
        raise EncryptionKeyError(f"{KEY_ENV_VAR} is not a valid Fernet key") from None


def _current() -> MultiFernet:
    secret = get_settings().field_encryption_key
    if secret is None:
        raise EncryptionKeyError(f"{KEY_ENV_VAR} is not set")
    return _multifernet(secret.get_secret_value())


def encrypt(plaintext: bytes) -> bytes:
    """Encrypt with the first configured key. Returns the Fernet token (starts with `gAAAAA`)."""
    return _current().encrypt(plaintext)


def decrypt(token: bytes, *, label: str) -> bytes:
    """Decrypt with any configured key. `label` (e.g. `person.steuer_id`) names the column."""
    fernet = _current()
    try:
        return fernet.decrypt(token)
    except (InvalidToken, TypeError, ValueError):
        raise DecryptionError(f"cannot decrypt {label}") from None
