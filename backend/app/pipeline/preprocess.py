"""Decode a stored file into parts #8 accepts (#9 Decision 10). Sync: run it in a thread.

| stored type           | classify / extract get                                       |
|-----------------------|--------------------------------------------------------------|
| JPEG, PNG, WebP       | the original bytes (after a full decode check); #8 rotates, strips metadata, downscales |
| GIF                   | PNG of frame 1                                               |
| BMP, TIFF, JPEG 2000  | JPEG (PNG with alpha); every TIFF page                       |
| HEIC, HEIF, AVIF      | JPEG (EXIF rotation applied)                                 |
| JPEG XL               | `UnsupportedImageFormat` (no permissively licensed decoder)  |
| PDF                   | extract: the PDF unchanged; classify: its text layer (capped) if every page has text, else page 1 as JPEG |

Document text is never logged. Errors are `PermanentJobError`s without content.
"""

from __future__ import annotations

import io
import warnings
from dataclasses import dataclass

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from PIL import Image, ImageOps, ImageSequence
from pillow_heif import register_heif_opener

from app.llm import ImagePart, Part, PdfPart, TextPart
from app.pipeline.errors import (
    CorruptDocument,
    EncryptedPdf,
    ImageTooLarge,
    TooManyPages,
    UnsupportedImageFormat,
)

register_heif_opener()

MIN_TEXT_CHARS_PER_PAGE = 50
RENDER_LONG_SIDE_PX = 2048
JPEG_QUALITY = 92
PASS_THROUGH = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}
CONVERTED = {
    "image/gif": ("GIF",),
    "image/bmp": ("BMP", "DIB"),
    "image/tiff": ("TIFF",),
    "image/jp2": ("JPEG2000",),
    "image/heic": ("HEIF",),
    "image/heif": ("HEIF",),
    "image/avif": ("AVIF", "HEIF"),
}


@dataclass(frozen=True)
class Prepared:
    parts_for_classify: tuple[Part, ...]
    parts_for_extract: tuple[Part, ...]
    page_count: int
    has_text_layer: bool


@dataclass(frozen=True)
class Limits:
    max_pages: int
    classify_max_chars: int
    max_image_pixels: int


def _open(data: bytes, limits: Limits, formats: tuple[str, ...]) -> Image.Image:
    Image.MAX_IMAGE_PIXELS = limits.max_image_pixels
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(io.BytesIO(data), formats=list(formats))
            width, height = image.size
            if width * height > limits.max_image_pixels:
                raise ImageTooLarge()
            image.load()
            return image
    except ImageTooLarge:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ImageTooLarge() from None
    except Exception:
        raise CorruptDocument() from None


def _encode(image: Image.Image) -> ImagePart:
    upright = ImageOps.exif_transpose(image) or image
    has_alpha = upright.mode in ("RGBA", "LA", "PA") or (
        upright.mode == "P" and "transparency" in upright.info
    )
    out = io.BytesIO()
    if has_alpha:
        upright.convert("RGBA").save(out, format="PNG")
        return ImagePart(data=out.getvalue(), mime="image/png")
    if upright.mode in ("I;16", "I;16B", "I;16L", "I"):
        upright = upright.point(lambda v: v / 256).convert("L")
    upright.convert("RGB").save(out, format="JPEG", quality=JPEG_QUALITY)
    return ImagePart(data=out.getvalue(), mime="image/jpeg")


def _frames(image: Image.Image, limits: Limits) -> list[Image.Image]:
    try:
        n = getattr(image, "n_frames", 1)
        if n > limits.max_pages:
            raise TooManyPages()
        frames = []
        for frame in ImageSequence.Iterator(image):
            frame.load()
            frames.append(frame.copy())
        return frames
    except TooManyPages:
        raise
    except Exception:
        raise CorruptDocument() from None


def _image(data: bytes, mime: str, limits: Limits) -> Prepared:
    if mime in PASS_THROUGH:
        _open(data, limits, (PASS_THROUGH[mime],))  # full decode: corrupt / bomb checks
        part: Part = ImagePart(data=data, mime=mime)  # type: ignore[arg-type]
        return Prepared((part,), (part,), 1, False)
    formats = CONVERTED.get(mime)
    if formats is None:
        raise UnsupportedImageFormat()
    image = _open(data, limits, formats)
    if mime == "image/gif":
        image.seek(0)
        parts: tuple[Part, ...] = (_encode(image.convert("RGBA")),)
        return Prepared(parts, parts, 1, False)
    frames = _frames(image, limits) if mime == "image/tiff" else [image]
    parts = tuple(_encode(frame) for frame in frames)
    return Prepared(parts, parts, len(parts), False)


def _render_first_page(doc: pdfium.PdfDocument) -> ImagePart:
    page = doc[0]
    try:
        width, height = page.get_size()
        scale = min(RENDER_LONG_SIDE_PX / max(width, height, 1.0), 300 / 72)
        pil = page.render(scale=scale).to_pil()
    except Exception:
        raise CorruptDocument() from None
    finally:
        page.close()
    out = io.BytesIO()
    pil.convert("RGB").save(out, format="JPEG", quality=JPEG_QUALITY)
    return ImagePart(data=out.getvalue(), mime="image/jpeg")


def _pdf(data: bytes, limits: Limits) -> Prepared:
    try:
        doc = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as exc:
        if getattr(exc, "err_code", None) == pdfium_c.FPDF_ERR_PASSWORD:
            raise EncryptedPdf() from None
        raise CorruptDocument() from None
    except Exception:
        raise CorruptDocument() from None
    try:
        pages = len(doc)
        if pages == 0:
            raise CorruptDocument()
        if pages > limits.max_pages:
            raise TooManyPages()
        texts = []
        try:
            for index in range(pages):
                page = doc[index]
                try:
                    textpage = page.get_textpage()
                    texts.append(textpage.get_text_bounded())
                    textpage.close()
                finally:
                    page.close()
        except Exception:
            raise CorruptDocument() from None
        has_text = all(len("".join(t.split())) >= MIN_TEXT_CHARS_PER_PAGE for t in texts)
        if has_text:
            text = "\n\n".join(texts)[: limits.classify_max_chars]
            classify: Part = TextPart(text=text)
        else:
            classify = _render_first_page(doc)
        return Prepared((classify,), (PdfPart(data=data),), pages, has_text)
    finally:
        doc.close()


def preprocess(data: bytes, mime_type: str, limits: Limits) -> Prepared:
    """Bytes + #6's sniffed MIME type → parts for classify / extract, or a permanent error."""
    if mime_type == "application/pdf":
        return _pdf(data, limits)
    return _image(data, mime_type, limits)
