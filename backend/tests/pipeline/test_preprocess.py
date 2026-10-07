"""Preprocess (#9 Decision 10): one generated fixture per stored type."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from app.config import LLMSettings
from app.llm import ImagePart, PdfPart, TextPart
from app.llm.inputs import InputLimits, preflight
from app.pipeline.errors import (
    CorruptDocument,
    EncryptedPdf,
    ImageTooLarge,
    TooManyPages,
    UnsupportedImageFormat,
)
from app.pipeline.preprocess import Limits, preprocess
from tests.pipeline import files

LIMITS = Limits(max_pages=20, classify_max_chars=20_000, max_image_pixels=50_000_000)
ACCEPTED = {"image/jpeg", "image/png", "image/webp", "image/gif"}


def _llm_sees(parts: tuple[object, ...]) -> list[Image.Image]:
    """What #8's preflight hands to a provider (accepted types only, rotated, re-encoded)."""
    limits = InputLimits.from_settings(LLMSettings(_env_file=None))  # type: ignore[call-arg]
    prepared = preflight(list(parts), limits, "native")  # type: ignore[arg-type]
    return [Image.open(io.BytesIO(p.data)) for p in prepared.parts if isinstance(p, ImagePart)]


@pytest.mark.parametrize("name", sorted(files.ALL_IMAGES))
def test_every_image_type_reaches_the_llm(name: str) -> None:
    mime, build = files.ALL_IMAGES[name]
    prepared = preprocess(build(), mime, LIMITS)
    assert prepared.page_count == 1 and not prepared.has_text_layer
    assert prepared.parts_for_classify == prepared.parts_for_extract
    (part,) = prepared.parts_for_extract
    assert isinstance(part, ImagePart) and part.mime in ACCEPTED
    (img,) = _llm_sees(prepared.parts_for_extract)
    assert img.size == (40, 20)


def test_animated_gif_uses_frame_one() -> None:
    prepared = preprocess(files.gif_animated(3), "image/gif", LIMITS)
    (part,) = prepared.parts_for_extract
    assert isinstance(part, ImagePart) and part.mime == "image/png"
    img = Image.open(io.BytesIO(part.data))
    assert getattr(img, "n_frames", 1) == 1
    assert img.convert("RGB").getpixel((5, 5)) == (0, 10, 10)


def test_multi_page_tiff_gives_every_page() -> None:
    prepared = preprocess(files.tiff(3), "image/tiff", LIMITS)
    assert prepared.page_count == 3
    assert len(prepared.parts_for_extract) == 3
    assert all(
        isinstance(p, ImagePart) and p.mime == "image/jpeg" for p in prepared.parts_for_extract
    )


def test_exif_orientation_arrives_upright() -> None:
    prepared = preprocess(files.jpeg_rotated(), "image/jpeg", LIMITS)
    (img,) = _llm_sees(prepared.parts_for_extract)
    assert img.size == (20, 40)


def test_jpeg_xl_is_unsupported() -> None:
    with pytest.raises(UnsupportedImageFormat):
        preprocess(files.jxl(), "image/jxl", LIMITS)


def test_pdf_with_text_layer() -> None:
    data = files.pdf_text(2)
    prepared = preprocess(data, "application/pdf", LIMITS)
    assert prepared.page_count == 2 and prepared.has_text_layer
    (text,) = prepared.parts_for_classify
    assert isinstance(text, TextPart) and "Testmodus" in text.text
    assert prepared.parts_for_extract == (PdfPart(data=data),)


def test_classify_text_is_capped() -> None:
    limits = Limits(max_pages=20, classify_max_chars=60, max_image_pixels=50_000_000)
    (text,) = preprocess(files.pdf_text(2), "application/pdf", limits).parts_for_classify
    assert isinstance(text, TextPart) and len(text.text) == 60


def test_image_only_pdf_classifies_on_page_one() -> None:
    prepared = preprocess(files.pdf_image_only(), "application/pdf", LIMITS)
    assert not prepared.has_text_layer and prepared.page_count == 1
    (part,) = prepared.parts_for_classify
    assert isinstance(part, ImagePart) and part.mime == "image/jpeg"
    assert isinstance(prepared.parts_for_extract[0], PdfPart)


@pytest.mark.parametrize(
    ("data", "mime", "error"),
    [
        (files.truncated(files.jpeg((400, 300))), "image/jpeg", CorruptDocument),
        (files.truncated(files.pdf_text(2)), "application/pdf", CorruptDocument),
        (files.pdf_encrypted(), "application/pdf", EncryptedPdf),
        (files.pdf_text(21), "application/pdf", TooManyPages),
        (files.tiff(21), "image/tiff", TooManyPages),
        (files.png_bomb(), "image/png", ImageTooLarge),
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * 40, "image/png", CorruptDocument),
    ],
    ids=["jpeg-truncated", "pdf-truncated", "pdf-encrypted", "pdf-21-pages", "tiff-21",
         "png-bomb", "png-garbage"],
)  # fmt: skip
def test_undecodable_files(data: bytes, mime: str, error: type[Exception]) -> None:
    with pytest.raises(error) as info:
        preprocess(data, mime, LIMITS)
    assert str(info.value) == ""  # no content in the message
