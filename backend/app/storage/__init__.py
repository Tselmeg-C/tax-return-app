"""File storage for uploaded documents (#6). See `app.storage.base` for the rules."""

from app.config import Settings
from app.storage.base import (
    HEAD_BYTES,
    InvalidStorageKey,
    ObjectExists,
    ObjectNotFound,
    ObjectTooLarge,
    Staged,
    Storage,
    StorageError,
    StorageFull,
    StorageUnavailable,
    StoredObject,
    document_key,
    document_prefix,
)
from app.storage.local import LocalVolume


def storage_from_settings(settings: Settings) -> LocalVolume:
    return LocalVolume(settings.resolved_storage_path)


__all__ = [
    "HEAD_BYTES",
    "InvalidStorageKey",
    "LocalVolume",
    "ObjectExists",
    "ObjectNotFound",
    "ObjectTooLarge",
    "Staged",
    "Storage",
    "StorageError",
    "StorageFull",
    "StorageUnavailable",
    "StoredObject",
    "document_key",
    "document_prefix",
    "storage_from_settings",
]
