"""Custom column types: encrypted columns and the enum pattern.

Encrypted columns (`EncryptedString`, `EncryptedJSON`) are stored as `bytea` holding a Fernet
token (see `app.db.crypto`). Use them for anything that holds a Steuer-ID, an IBAN or raw
document text.

Fernet is non-deterministic: the same plaintext encrypts to a different token every time.
Comparisons, filters, ORDER BY, indexes and UNIQUE constraints on these columns therefore do
not work. Never put an encrypted column in a WHERE clause; look rows up by another column.

`NULL` is stored as SQL `NULL` and never touches the key.
"""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator

from app.db.crypto import DecryptionError, EncryptionKeyError, decrypt, encrypt

__all__ = [
    "DecryptionError",
    "EncryptedJSON",
    "EncryptedString",
    "EncryptedType",
    "EncryptionKeyError",
    "enum_type",
]

ENUM_LENGTH = 64


class EncryptedType(TypeDecorator[Any]):
    """Base for encrypted columns. `label` is `"<table>.<column>"` and appears in errors.

    Comparisons and filters on encrypted columns do not work (non-deterministic encryption).
    """

    impl = sa.LargeBinary
    cache_ok = True

    def __init__(self, label: str) -> None:
        super().__init__()
        self.label = label

    def _to_bytes(self, value: Any) -> bytes:
        raise NotImplementedError

    def _from_bytes(self, data: bytes) -> Any:
        raise NotImplementedError

    def process_bind_param(self, value: Any, dialect: Dialect) -> bytes | None:
        if value is None:
            return None
        return encrypt(self._to_bytes(value))

    def process_result_value(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        return self._from_bytes(decrypt(bytes(value), label=self.label))

    def process_literal_param(self, value: Any, dialect: Dialect) -> str:
        # Literal rendering would put plaintext into SQL text (logs, offline migrations).
        raise TypeError(f"{self.label} is encrypted and cannot be rendered as a literal")


class EncryptedString(EncryptedType):
    """Encrypted `str` (e.g. `person.steuer_id`). No filtering or comparison possible."""

    def _to_bytes(self, value: Any) -> bytes:
        if not isinstance(value, str):
            raise TypeError(f"{self.label} expects str")
        return value.encode("utf-8")

    def _from_bytes(self, data: bytes) -> str:
        return data.decode("utf-8")


class EncryptedJSON(EncryptedType):
    """Encrypted JSON value (e.g. `extraction.raw_json`). No filtering or comparison possible."""

    def _to_bytes(self, value: Any) -> bytes:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    def _from_bytes(self, data: bytes) -> Any:
        return json.loads(data.decode("utf-8"))


def enum_type(enum_cls: type[StrEnum], name: str) -> sa.Enum:
    """VARCHAR + CHECK enum column type storing the enum *values*.

    `name` becomes the CHECK name via the naming convention (`ck_<table>_<name>`); use the
    column name. Not a native Postgres enum: values can be added and removed by replacing the
    CHECK in a migration. `alembic check` does not compare CHECKs; `tests/domain/test_enums_db.py`
    compares every enum CHECK with its Python enum.
    """
    return sa.Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=ENUM_LENGTH,
        values_callable=lambda e: [member.value for member in e],
        validate_strings=True,
    )
