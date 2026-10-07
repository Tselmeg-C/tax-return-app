"""`LocalVolume` (#6 Storage criteria)."""

from __future__ import annotations

import errno
import hashlib
import os
import secrets
import stat
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from app.storage import (
    InvalidStorageKey,
    LocalVolume,
    ObjectExists,
    ObjectNotFound,
    ObjectTooLarge,
    StorageFull,
    document_key,
)


async def chunks_of(data: bytes, size: int = 7) -> AsyncIterator[bytes]:
    for i in range(0, len(data), size):
        yield data[i : i + size]


@pytest.fixture
def volume(tmp_path: Path) -> LocalVolume:
    v = LocalVolume(tmp_path / "root")
    v.probe()
    return v


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


async def test_stage_commit_open(volume: LocalVolume) -> None:
    data = secrets.token_bytes(5000)
    hh, doc = uuid.uuid4(), uuid.uuid4()
    staged = await volume.stage(chunks_of(data, 999), max_bytes=10_000)
    assert staged.sha256 == hashlib.sha256(data).hexdigest()
    assert staged.size == 5000 and staged.head == data[:1024]
    key = document_key(hh, doc)
    await volume.commit(staged, key)

    path = volume.root / "households" / str(hh) / "documents" / str(doc) / "original"
    assert path.read_bytes() == data
    assert _mode(path) == 0o600
    for directory in (path.parent, path.parent.parent, volume.root / "households" / str(hh)):
        assert _mode(directory) == 0o700
    reader = await volume.open(key)
    assert reader.size == 5000
    assert b"".join([c async for c in reader]) == data
    assert list((volume.root / "tmp").iterdir()) == []


async def test_too_large_removes_temp(volume: LocalVolume) -> None:
    with pytest.raises(ObjectTooLarge):
        await volume.stage(chunks_of(b"x" * 11, 3), max_bytes=10)
    assert list((volume.root / "tmp").iterdir()) == []


async def test_commit_refuses_overwrite(volume: LocalVolume) -> None:
    key = document_key(uuid.uuid4(), uuid.uuid4())
    await volume.commit(await volume.stage(chunks_of(b"first"), max_bytes=100), key)
    second = await volume.stage(chunks_of(b"second"), max_bytes=100)
    with pytest.raises(ObjectExists):
        await volume.commit(second, key)
    assert volume.path_for(key).read_bytes() == b"first"


@pytest.mark.parametrize(
    "key",
    ["../x", "a/../../x", "/etc/passwd", "a//b", "a\\b", "a\x00b", "A/upper", "a" * 513],
)
def test_invalid_keys(volume: LocalVolume, key: str) -> None:
    with pytest.raises(InvalidStorageKey):
        volume.path_for(key)


async def test_symlink_escape(volume: LocalVolume, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (volume.root / "link").symlink_to(outside)
    with pytest.raises(InvalidStorageKey):
        volume.path_for("link/x")
    with pytest.raises(InvalidStorageKey):
        await volume.open("link/x")


async def test_missing(volume: LocalVolume) -> None:
    with pytest.raises(ObjectNotFound):
        await volume.open("households/none/original")
    await volume.delete_prefix("households/none")


async def test_enospc_is_storage_full(volume: LocalVolume, monkeypatch: pytest.MonkeyPatch) -> None:
    def full(fd: int, data: bytes) -> None:
        raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))

    monkeypatch.setattr(volume, "_write", full)
    with pytest.raises(StorageFull):
        await volume.stage(chunks_of(b"abc"), max_bytes=100)
    assert list((volume.root / "tmp").iterdir()) == []


def test_probe_fails_naming_storage_path(tmp_path: Path) -> None:
    from app.storage import StorageUnavailable

    root = tmp_path / "ro"
    root.mkdir()
    root.chmod(0o500)
    try:
        with pytest.raises(StorageUnavailable) as caught:
            LocalVolume(root).probe()
        assert "STORAGE_PATH" in str(caught.value)
        assert str(root) not in str(caught.value)
    finally:
        root.chmod(0o700)
