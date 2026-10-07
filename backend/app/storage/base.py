"""The `Storage` protocol, its errors and the key layout.

Everything that touches uploaded files goes through a `Storage`; nothing outside
`app/storage/` reads or writes the file system for documents. Keys are opaque and never
contain a file name: `households/<household_id>/documents/<document_id>/original`.

Error messages never contain a key's user-provided parts, file contents or OS messages.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterable, AsyncIterator
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Protocol

HEAD_BYTES = 1024


class StorageError(Exception):
    """Base class. Log the class name only (`type(exc).__name__`)."""


class ObjectTooLarge(StorageError):
    def __init__(self, max_bytes: int) -> None:
        super().__init__(f"object exceeds {max_bytes} bytes")
        self.max_bytes = max_bytes


class ObjectNotFound(StorageError):
    def __init__(self) -> None:
        super().__init__("object not found")


class ObjectExists(StorageError):
    def __init__(self) -> None:
        super().__init__("object already exists")


class InvalidStorageKey(StorageError):
    def __init__(self) -> None:
        super().__init__("invalid storage key")


class StorageFull(StorageError):
    def __init__(self) -> None:
        super().__init__("storage full")


class StorageUnavailable(StorageError):
    """The storage root cannot be used (startup probe). Names `STORAGE_PATH`, never an OS text."""


@dataclass(frozen=True)
class Staged:
    """A fully written temp file, not yet visible under any key."""

    size: int
    sha256: str
    head: bytes  # the first `HEAD_BYTES` bytes, for type sniffing
    token: str = field(repr=False)  # implementation detail (the temp file name)


class StoredObject(AsyncIterator[bytes]):
    """An opened object: iterate for its bytes; `size` is known up front. Close when done."""

    size: int

    async def aclose(self) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class Storage(Protocol):
    async def stage(self, chunks: AsyncIterable[bytes], *, max_bytes: int) -> Staged:
        """Write `chunks` to a temp file; `ObjectTooLarge` (temp file removed) past `max_bytes`."""
        ...

    async def commit(self, staged: Staged, key: str) -> None:
        """Atomically move a staged file to `key` (fsync file, link, fsync dir).

        Refuses to overwrite an existing key (`ObjectExists`).
        """
        ...

    async def discard(self, staged: Staged) -> None: ...

    async def open(self, key: str) -> StoredObject:
        """The object's bytes; `ObjectNotFound` if missing (raised here, before iterating)."""
        ...

    async def delete_prefix(self, prefix: str) -> None:
        """Remove everything under `prefix` (a document's directory); missing is fine."""
        ...

    async def exists(self, key: str) -> bool: ...

    async def sweep_tmp(self, older_than: timedelta, *, dry_run: bool = False) -> int:
        """Delete temp files older than `older_than`; returns how many (would be) deleted."""
        ...


def document_prefix(household_id: uuid.UUID, document_id: uuid.UUID) -> str:
    return f"households/{household_id}/documents/{document_id}"


def document_key(household_id: uuid.UUID, document_id: uuid.UUID) -> str:
    """`households/<h>/documents/<d>/original` (no extension, no file name, no date)."""
    return f"{document_prefix(household_id, document_id)}/original"
