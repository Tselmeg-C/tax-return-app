"""`LocalVolume`: `Storage` on a local directory (dev, tests, and the Railway volume).

- Keys match `^[a-z0-9][a-z0-9/_.-]{0,511}$`, have no empty or `..` segment, and must
  resolve (symlinks included) to a path under the root. Anything else: `InvalidStorageKey`.
- Directories are created with mode 0700, files with 0600.
- Temp files live in `<root>/tmp/` (same file system, so the final link is atomic).
- `ENOSPC` / `EDQUOT` become `StorageFull`.
"""

from __future__ import annotations

import asyncio
import errno
import hashlib
import os
import re
import secrets
import shutil
import time
import uuid
from collections.abc import AsyncIterable
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from app.storage.base import (
    HEAD_BYTES,
    InvalidStorageKey,
    ObjectExists,
    ObjectNotFound,
    ObjectTooLarge,
    Staged,
    StorageFull,
    StorageUnavailable,
    StoredObject,
)

KEY_RE = re.compile(r"^[a-z0-9][a-z0-9/_.-]{0,511}$")
TMP_DIR = "tmp"
HOUSEHOLDS_DIR = "households"
DIR_MODE = 0o700
FILE_MODE = 0o600
READ_CHUNK = 256 * 1024
_FULL_ERRNOS = frozenset({errno.ENOSPC, errno.EDQUOT})


def _is_full(exc: OSError) -> bool:
    return exc.errno in _FULL_ERRNOS


@dataclass(frozen=True)
class DocumentDir:
    """A `households/<h>/documents/<d>` directory found on disk (for the sweep)."""

    household_id: uuid.UUID
    document_id: uuid.UUID
    prefix: str
    newest_mtime: float


class _FileReader(StoredObject):
    def __init__(self, fd: int, size: int) -> None:
        self._fd: int | None = fd
        self.size = size

    def __aiter__(self) -> _FileReader:
        return self

    async def __anext__(self) -> bytes:
        fd = self._fd
        if fd is None:
            raise StopAsyncIteration
        chunk = await asyncio.to_thread(os.read, fd, READ_CHUNK)
        if not chunk:
            await self.aclose()
            raise StopAsyncIteration
        return chunk

    async def aclose(self) -> None:
        fd, self._fd = self._fd, None
        if fd is not None:
            os.close(fd)


class LocalVolume:
    def __init__(self, root: Path) -> None:
        self.root = root.absolute()

    # --- paths -----------------------------------------------------------------------

    def _root_resolved(self) -> Path:
        return self.root.resolve()

    def path_for(self, key: str) -> Path:
        """The validated absolute path of `key` (raises `InvalidStorageKey`)."""
        if not isinstance(key, str) or not KEY_RE.fullmatch(key):
            raise InvalidStorageKey()
        segments = key.split("/")
        if any(seg in ("", ".", "..") for seg in segments):
            raise InvalidStorageKey()
        root = self._root_resolved()
        resolved = (root / key).resolve()
        if resolved == root or not resolved.is_relative_to(root):
            raise InvalidStorageKey()
        return root / key

    def _tmp_dir(self) -> Path:
        return self.root / TMP_DIR

    def _tmp_path(self, token: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{32}", token):
            raise InvalidStorageKey()
        return self._tmp_dir() / token

    def _mkdirs(self, path: Path) -> None:
        """Create `path` and missing parents below the root with mode 0700."""
        missing: list[Path] = []
        current = path
        while not current.exists():
            missing.append(current)
            if current.parent == current:
                break
            current = current.parent
        for directory in reversed(missing):
            try:
                os.mkdir(directory, DIR_MODE)
            except FileExistsError:
                continue
            os.chmod(directory, DIR_MODE)  # mkdir's mode is masked by the umask

    # --- startup ---------------------------------------------------------------------

    def probe(self) -> None:
        """Write and delete a probe file. Raises `StorageUnavailable` naming STORAGE_PATH."""
        try:
            self._mkdirs(self.root)
            probe = self.root / f".probe-{secrets.token_hex(8)}"
            fd = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, FILE_MODE)
            try:
                os.write(fd, b"probe")
                os.fsync(fd)
            finally:
                os.close(fd)
            os.unlink(probe)
            self._mkdirs(self._tmp_dir())
        except OSError as exc:
            raise StorageUnavailable(
                f"STORAGE_PATH is not writable ({type(exc).__name__})"
            ) from None

    def free_bytes(self) -> int:
        return shutil.disk_usage(self.root).free

    # --- Storage protocol ------------------------------------------------------------

    def _write(self, fd: int, data: bytes) -> None:
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            view = view[written:]

    async def stage(self, chunks: AsyncIterable[bytes], *, max_bytes: int) -> Staged:
        try:
            await asyncio.to_thread(self._mkdirs, self._tmp_dir())
        except OSError as exc:
            if _is_full(exc):
                raise StorageFull() from None
            raise
        token = secrets.token_hex(16)
        path = self._tmp_path(token)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, FILE_MODE)
        digest = hashlib.sha256()
        head = bytearray()
        size = 0
        try:
            async for chunk in chunks:
                if not chunk:
                    continue
                size += len(chunk)
                if size > max_bytes:
                    raise ObjectTooLarge(max_bytes)
                digest.update(chunk)
                if len(head) < HEAD_BYTES:
                    head += chunk[: HEAD_BYTES - len(head)]
                try:
                    await asyncio.to_thread(self._write, fd, chunk)
                except OSError as exc:
                    if _is_full(exc):
                        raise StorageFull() from None
                    raise
        except BaseException:
            os.close(fd)
            path.unlink(missing_ok=True)
            raise
        os.close(fd)
        return Staged(size=size, sha256=digest.hexdigest(), head=bytes(head), token=token)

    def _commit_sync(self, staged: Staged, key: str) -> None:
        source = self._tmp_path(staged.token)
        target = self.path_for(key)
        fd = os.open(source, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            self._mkdirs(target.parent)
            # link() never overwrites: an existing key raises FileExistsError.
            os.link(source, target)
        except FileExistsError:
            raise ObjectExists() from None
        except OSError as exc:
            if _is_full(exc):
                raise StorageFull() from None
            raise
        source.unlink()
        dir_fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)

    async def commit(self, staged: Staged, key: str) -> None:
        await asyncio.to_thread(self._commit_sync, staged, key)

    async def discard(self, staged: Staged) -> None:
        self._tmp_path(staged.token).unlink(missing_ok=True)

    async def open(self, key: str) -> StoredObject:
        path = self.path_for(key)
        try:
            fd = os.open(path, os.O_RDONLY)
        except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
            raise ObjectNotFound() from None
        try:
            size = os.fstat(fd).st_size
        except OSError:
            os.close(fd)
            raise
        return _FileReader(fd, size)

    async def delete_prefix(self, prefix: str) -> None:
        path = self.path_for(prefix)

        def _remove() -> None:
            try:
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path)
                else:
                    path.unlink()
            except FileNotFoundError:
                pass

        await asyncio.to_thread(_remove)

    async def exists(self, key: str) -> bool:
        return self.path_for(key).is_file()

    async def sweep_tmp(self, older_than: timedelta, *, dry_run: bool = False) -> int:
        cutoff = time.time() - older_than.total_seconds()
        tmp = self._tmp_dir()
        count = 0
        if not tmp.is_dir():
            return 0
        for entry in tmp.iterdir():
            try:
                if entry.is_file() and entry.stat().st_mtime < cutoff:
                    count += 1
                    if not dry_run:
                        entry.unlink(missing_ok=True)
            except FileNotFoundError:
                continue
        return count

    # --- sweep helpers ---------------------------------------------------------------

    def document_dirs(self) -> list[DocumentDir]:
        """Every `households/<uuid>/documents/<uuid>` directory with its newest mtime.

        Directories whose names are not UUIDs are ignored (never ours to delete).
        """
        found: list[DocumentDir] = []
        households = self.root / HOUSEHOLDS_DIR
        if not households.is_dir():
            return found
        for hh_dir in households.iterdir():
            hh_id = _as_uuid(hh_dir.name)
            docs = hh_dir / "documents"
            if hh_id is None or not docs.is_dir() or hh_dir.is_symlink():
                continue
            for doc_dir in docs.iterdir():
                doc_id = _as_uuid(doc_dir.name)
                if doc_id is None or doc_dir.is_symlink() or not doc_dir.is_dir():
                    continue
                newest = doc_dir.stat().st_mtime
                for child in doc_dir.rglob("*"):
                    try:
                        newest = max(newest, child.lstat().st_mtime)
                    except FileNotFoundError:
                        continue
                prefix = f"{HOUSEHOLDS_DIR}/{hh_id}/documents/{doc_id}"
                found.append(DocumentDir(hh_id, doc_id, prefix, newest))
        return found


def _as_uuid(name: str) -> uuid.UUID | None:
    try:
        value = uuid.UUID(name)
    except ValueError:
        return None
    return value if str(value) == name else None
