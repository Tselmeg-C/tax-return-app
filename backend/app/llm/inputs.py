"""Input preflight: runs before any HTTP call.

- Images: allowed formats only, decompression-bomb guard, EXIF orientation applied,
  downscaled (never cropped) to `LLM_MAX_IMAGE_PX`, always re-encoded (no metadata survives),
  first frame of GIF / MPO.
- PDFs: corrupt / encrypted → `LLMInputInvalid`; more pages than `LLM_MAX_PDF_PAGES` →
  `LLMInputTooLarge` (pages are never dropped); `rasterize` mode renders page images.
- Whole request: image count and base64 size limits.
"""

from __future__ import annotations

import io
import math
import warnings
from collections.abc import Sequence
from dataclasses import dataclass

import pypdfium2 as pdfium
from PIL import Image, ImageOps

from app.config import LLMSettings
from app.llm.errors import LLMInputInvalid, LLMInputTooLarge
from app.llm.types import ImagePart, Part, PdfInputMode, PdfPart, TextPart

# MPO = multi-picture JPEG (camera / Android Ultra HDR); its primary frame is a plain JPEG.
_FORMAT_MIME = {
    "JPEG": "image/jpeg",
    "MPO": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
    "GIF": "image/gif",
}
_ALLOWED_MIMES = frozenset(_FORMAT_MIME.values())
JPEG_QUALITY = 85


@dataclass(frozen=True)
class InputLimits:
    max_image_px: int = 2048
    max_image_pixels: int = 50_000_000
    max_image_bytes: int = 8_000_000
    max_pdf_pages: int = 20
    max_images: int = 20
    max_request_bytes: int = 20_000_000  # base64-encoded total

    @classmethod
    def from_settings(cls, settings: LLMSettings) -> InputLimits:
        return cls(
            max_image_px=settings.llm_max_image_px,
            max_image_pixels=settings.llm_max_image_pixels,
            max_image_bytes=settings.llm_max_image_bytes,
            max_pdf_pages=settings.llm_max_pdf_pages,
            max_images=settings.llm_max_images,
            max_request_bytes=int(settings.llm_max_request_mb * 1_000_000),
        )


@dataclass(frozen=True)
class PreparedInput:
    parts: tuple[Part, ...]
    n_images: int  # after rasterising
    n_bytes: int  # base64-encoded size of all binary parts + UTF-8 size of text parts


def _invalid(detail: str) -> LLMInputInvalid:
    return LLMInputInvalid(detail=detail)


def _too_large(detail: str) -> LLMInputTooLarge:
    return LLMInputTooLarge(detail=detail)


def _open_image(data: bytes, limits: InputLimits) -> Image.Image:
    Image.MAX_IMAGE_PIXELS = limits.max_image_pixels
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(io.BytesIO(data))
            width, height = image.size
            if width * height > limits.max_image_pixels:
                raise _invalid("image has more pixels than LLM_MAX_IMAGE_PIXELS")
            if image.format not in _FORMAT_MIME:
                raise _invalid("image format is not jpeg, png, webp or gif")
            image.seek(0)  # animated GIF / WebP, MPO: first (primary) frame only
            image.load()
    except LLMInputInvalid:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise _invalid("image has more pixels than LLM_MAX_IMAGE_PIXELS") from None
    except Exception:
        raise _invalid("image cannot be decoded") from None
    return image


def _encode(image: Image.Image) -> tuple[bytes, str]:
    """Re-encode without any metadata: JPEG q85, or PNG if the image has alpha."""
    has_alpha = image.mode in ("RGBA", "LA", "PA") or (
        image.mode == "P" and "transparency" in image.info
    )
    out = io.BytesIO()
    if has_alpha:
        clean = image.convert("RGBA")
        clean.info = {}
        clean.save(out, format="PNG", optimize=True)
        return out.getvalue(), "image/png"
    clean = image.convert("RGB")
    clean.info = {}
    clean.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return out.getvalue(), "image/jpeg"


def _scaled(image: Image.Image, max_px: int) -> Image.Image:
    width, height = image.size
    long_side = max(width, height)
    if long_side <= max_px:
        return image
    scale = max_px / long_side
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    return image.resize(size, Image.Resampling.LANCZOS)


def _prepare_pil(image: Image.Image, limits: InputLimits) -> ImagePart:
    transposed = ImageOps.exif_transpose(image) or image
    data, mime = _encode(_scaled(transposed, limits.max_image_px))
    if len(data) > limits.max_image_bytes:
        raise _too_large("image is larger than LLM_MAX_IMAGE_BYTES after re-encoding")
    return ImagePart(data=data, mime=mime)  # type: ignore[arg-type]


def prepare_image(part: ImagePart, limits: InputLimits) -> ImagePart:
    if part.mime not in _ALLOWED_MIMES:
        raise _invalid("image type is not jpeg, png, webp or gif")
    # Always re-encode: only pixels reach the provider, never metadata segments (EXIF, XMP,
    # ICC, comments or APPn blocks Pillow does not even expose).
    return _prepare_pil(_open_image(part.data, limits), limits)


def _open_pdf(data: bytes) -> pdfium.PdfDocument:
    try:
        return pdfium.PdfDocument(data)
    except Exception:
        raise _invalid("PDF is corrupt or password-protected") from None


def prepare_pdf(part: PdfPart, limits: InputLimits, mode: PdfInputMode) -> list[Part]:
    doc = _open_pdf(part.data)
    try:
        pages = len(doc)
        if pages == 0:
            raise _invalid("PDF has no pages")
        if pages > limits.max_pdf_pages:
            raise _too_large("PDF has more pages than LLM_MAX_PDF_PAGES")
        if mode == "native":
            return [part]
        images: list[Part] = []
        for index in range(pages):
            page = doc[index]
            try:
                width, height = page.get_size()  # points (1/72 in)
                scale = min(limits.max_image_px / max(width, height, 1.0), 300 / 72)
                bitmap = page.render(scale=scale)
                pil = bitmap.to_pil()
            except Exception:
                raise _invalid("PDF page cannot be rendered") from None
            finally:
                page.close()
            images.append(_prepare_pil(pil, limits))
        return images
    finally:
        doc.close()


def _b64_size(n: int) -> int:
    return 4 * math.ceil(n / 3)


def preflight(parts: Sequence[Part], limits: InputLimits, pdf_mode: PdfInputMode) -> PreparedInput:
    """Validate and normalise the parts; raises before any HTTP call."""
    if not parts:
        raise ValueError("parts must not be empty")
    prepared: list[Part] = []
    for part in parts:
        if isinstance(part, TextPart):
            prepared.append(part)
        elif isinstance(part, ImagePart):
            prepared.append(prepare_image(part, limits))
        elif isinstance(part, PdfPart):
            prepared.extend(prepare_pdf(part, limits, pdf_mode))
        else:
            raise TypeError("unsupported part type")
    n_images = sum(1 for p in prepared if isinstance(p, ImagePart))
    if n_images > limits.max_images:
        raise _too_large("request has more images than LLM_MAX_IMAGES")
    n_bytes = sum(
        len(p.text.encode("utf-8")) if isinstance(p, TextPart) else _b64_size(len(p.data))
        for p in prepared
    )
    if n_bytes > limits.max_request_bytes:
        raise _too_large("request is larger than LLM_MAX_REQUEST_MB")
    return PreparedInput(parts=tuple(prepared), n_images=n_images, n_bytes=n_bytes)
