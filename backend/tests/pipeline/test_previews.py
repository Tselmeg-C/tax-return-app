"""Previews (#35): thumbnails from the shared preprocess decoders, the job step, the backfill."""

from __future__ import annotations

import io
import secrets
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Document
from app.documents.previews import backfill, ensure_preview
from app.pipeline.errors import CorruptDocument, EncryptedPdf, UnsupportedImageFormat
from app.pipeline.preprocess import Limits, preview_jpeg
from app.storage import preview_key
from tests.pipeline import files
from tests.pipeline.helpers import classify_out, extract_out, make_world, scripted

LIMITS = Limits(max_pages=20, classify_max_chars=20_000, max_image_pixels=50_000_000)


def _open(data: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(data))
    img.load()
    return img


def _jpeg_with_gps(size: tuple[int, int]) -> bytes:
    exif = Image.Exif()
    exif[0x0112] = 6
    exif[0x010F] = "Testmake"
    exif[0x8825] = {1: "N", 2: (52.0, 31.0, 0.0), 3: "E", 4: (13.0, 24.0, 0.0)}
    out = io.BytesIO()
    Image.new("RGB", size, (secrets.randbelow(256), 9, 9)).save(
        out, format="JPEG", exif=exif.tobytes()
    )
    return out.getvalue()


@pytest.mark.parametrize("name", sorted(files.ALL_IMAGES))
def test_every_image_type_gets_a_small_jpeg(name: str) -> None:
    mime, build = files.ALL_IMAGES[name]
    img = _open(preview_jpeg(build(), mime, LIMITS))
    assert img.format == "JPEG" and max(img.size) <= 400


def test_large_image_is_scaled_down_keeping_aspect() -> None:
    img = _open(preview_jpeg(files.jpeg((1600, 800)), "image/jpeg", LIMITS))
    assert img.size == (400, 200)


def test_pdf_preview_is_page_one_of_three() -> None:
    img = _open(preview_jpeg(files.pdf_text(3), "application/pdf", LIMITS))
    assert img.format == "JPEG" and max(img.size) == 400


def test_exif_orientation_6_is_upright() -> None:
    img = _open(preview_jpeg(files.jpeg_rotated(), "image/jpeg", LIMITS))
    assert img.size == (20, 40)


def test_no_exif_or_gps_in_the_preview() -> None:
    source = _jpeg_with_gps((60, 30))
    assert 0x8825 in Image.open(io.BytesIO(source)).getexif()  # the fixture does carry GPS
    out = preview_jpeg(source, "image/jpeg", LIMITS)
    img = _open(out)
    assert len(img.getexif()) == 0 and img.size == (30, 60)
    assert b"Exif" not in out and b"Testmake" not in out


def test_errors_reuse_the_pipeline_kinds() -> None:
    with pytest.raises(CorruptDocument):
        preview_jpeg(files.truncated(files.jpeg()), "image/jpeg", LIMITS)
    with pytest.raises(CorruptDocument):
        preview_jpeg(b"%PDF-1.7 nonsense", "application/pdf", LIMITS)
    with pytest.raises(EncryptedPdf):
        preview_jpeg(files.pdf_encrypted(), "application/pdf", LIMITS)
    with pytest.raises(UnsupportedImageFormat):
        preview_jpeg(files.jxl(), "image/jxl", LIMITS)


async def test_job_writes_preview_and_corrupt_file_leaves_none(
    committed: async_sessionmaker[AsyncSession], tmp_path: Path, migrated_database: str
) -> None:
    world = await make_world(committed, tmp_path, migrated_database)
    good = await world.upload(files.jpeg((800, 600)))
    assert await world.worker(scripted(classify_out(), extract_out()).router).run_once()
    assert await world.volume.exists(preview_key(world.household_id, good.id))
    assert (await world.doc(good.id)).error_kind is None

    bad = await world.upload(files.truncated(files.jpeg((800, 600))))
    assert await world.worker(scripted().router).run_once()  # the worker survives
    assert (await world.doc(bad.id)).error_kind == "CorruptDocument"
    assert not await world.volume.exists(preview_key(world.household_id, bad.id))


async def test_preview_failure_never_fails_the_document(
    committed: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    migrated_database: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*_: object) -> bytes:
        raise MemoryError

    monkeypatch.setattr("app.documents.previews.preview_jpeg", boom)
    world = await make_world(committed, tmp_path, migrated_database)
    doc = await world.upload(files.pdf_text(1, text="Rechnung Gesamtbetrag 180,00 EUR ok " * 3))
    assert await world.worker(scripted(classify_out(), extract_out()).router).run_once()
    assert (await world.doc(doc.id)).error_kind is None
    assert not await world.volume.exists(preview_key(world.household_id, doc.id))


async def test_backfill_twice_creates_once(
    committed: async_sessionmaker[AsyncSession], tmp_path: Path, migrated_database: str
) -> None:
    world = await make_world(committed, tmp_path, migrated_database)
    ok = await world.upload(files.jpeg())
    corrupt = await world.upload(files.truncated(files.jpeg()))
    dry = await backfill(world.volume, committed, dry_run=True)
    assert (dry.created, dry.failed) == (2, 0)  # dry run does not decode
    assert not await world.volume.exists(preview_key(world.household_id, ok.id))

    first = await backfill(world.volume, committed)
    assert (first.created, first.existing, first.failed) == (1, 0, 1)
    path = world.volume.path_for(preview_key(world.household_id, ok.id))
    mtime = path.stat().st_mtime_ns
    second = await backfill(world.volume, committed)
    assert (second.created, second.existing, second.failed) == (0, 1, 1)
    assert path.stat().st_mtime_ns == mtime
    assert not await world.volume.exists(preview_key(world.household_id, corrupt.id))


async def test_ensure_preview_is_idempotent_and_private(
    committed: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    migrated_database: str,
    json_log: io.StringIO,
) -> None:
    world = await make_world(committed, tmp_path, migrated_database)
    tag = secrets.token_hex(6)
    doc = await world.upload(files.jpeg(), original_filename=f"secret{tag}.jpg")
    data = files.truncated(files.jpeg()) + tag.encode()
    assert not await ensure_preview(world.volume, world.household_id, doc.id, data, "image/jpeg")
    assert await ensure_preview(
        world.volume, world.household_id, doc.id, files.jpeg(), "image/jpeg"
    )
    assert await ensure_preview(world.volume, world.household_id, doc.id, data, "image/jpeg")
    await backfill(world.volume, committed)
    logged = json_log.getvalue()
    assert "preview.failed" in logged and "CorruptDocument" in logged
    assert tag not in logged
    async with committed() as session:
        assert (await session.get(Document, doc.id)) is not None
