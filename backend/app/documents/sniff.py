"""File type detection by content (magic bytes), table-driven (#6 Decision 4).

The client's `Content-Type` and any file extension are ignored. Only the formats in `RULES`
are accepted; everything else (SVG and other XML, ICO, video `ftyp` brands, ZIP, HTML, text,
multipart bodies, ...) is `None`, which the upload answers with `415`.
Adding a format = one rule here plus one test.
"""

from __future__ import annotations

import struct
from collections.abc import Callable
from dataclasses import dataclass

PDF_MARKER = b"%PDF-"
PDF_WINDOW = 1024  # `%PDF-` must start within the first 1024 bytes


@dataclass(frozen=True)
class FileType:
    mime_type: str
    ext: str


JPEG = FileType("image/jpeg", "jpg")
PNG = FileType("image/png", "png")
GIF = FileType("image/gif", "gif")
WEBP = FileType("image/webp", "webp")
TIFF = FileType("image/tiff", "tif")
BMP = FileType("image/bmp", "bmp")
AVIF = FileType("image/avif", "avif")
HEIC = FileType("image/heic", "heic")
HEIF = FileType("image/heif", "heif")
JP2 = FileType("image/jp2", "jp2")
JXL = FileType("image/jxl", "jxl")
PDF = FileType("application/pdf", "pdf")

ALL_TYPES: tuple[FileType, ...] = (
    JPEG, PNG, GIF, WEBP, TIFF, BMP, AVIF, HEIC, HEIF, JP2, JXL, PDF,
)  # fmt: skip
EXT_BY_MIME: dict[str, str] = {t.mime_type: t.ext for t in ALL_TYPES}

# Extensions that already fit a detected type (for the download name, Decision 3).
EXTENSIONS_BY_MIME: dict[str, frozenset[str]] = {
    "image/jpeg": frozenset({"jpg", "jpeg", "jpe", "jfif"}),
    "image/png": frozenset({"png"}),
    "image/gif": frozenset({"gif"}),
    "image/webp": frozenset({"webp"}),
    "image/tiff": frozenset({"tif", "tiff"}),
    "image/bmp": frozenset({"bmp", "dib"}),
    "image/avif": frozenset({"avif"}),
    "image/heic": frozenset({"heic"}),
    "image/heif": frozenset({"heif", "heic"}),
    "image/jp2": frozenset({"jp2", "j2k", "jpf", "jpx"}),
    "image/jxl": frozenset({"jxl"}),
    "application/pdf": frozenset({"pdf"}),
}

BMP_DIB_SIZES = frozenset({12, 40, 52, 56, 64, 108, 124})
AVIF_BRANDS = frozenset({b"avif", b"avis"})
HEIC_BRANDS = frozenset({b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx"})
HEIF_BRANDS = frozenset({b"mif1", b"msf1"})
JP2_SIGNATURE = bytes.fromhex("0000000C6A5020200D0A870A")
JXL_CONTAINER = bytes.fromhex("0000000C4A584C200D0A870A")


def _jpeg(head: bytes) -> FileType | None:
    return JPEG if head[:3] == b"\xff\xd8\xff" else None


def _png(head: bytes) -> FileType | None:
    return PNG if head[:8] == b"\x89PNG\r\n\x1a\n" else None


def _gif(head: bytes) -> FileType | None:
    return GIF if head[:6] in (b"GIF87a", b"GIF89a") else None


def _webp(head: bytes) -> FileType | None:
    return WEBP if head[:4] == b"RIFF" and head[8:12] == b"WEBP" else None


def _tiff(head: bytes) -> FileType | None:
    magic = (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")
    return TIFF if head[:4] in magic else None


def _bmp(head: bytes) -> FileType | None:
    if head[:2] != b"BM" or len(head) < 18:
        return None
    (dib_size,) = struct.unpack_from("<I", head, 14)
    return BMP if dib_size in BMP_DIB_SIZES else None


def ftyp_brands(head: bytes) -> list[bytes]:
    """Major brand plus compatible brands of a leading `ftyp` box (read within `head` only)."""
    if len(head) < 12 or head[4:8] != b"ftyp":
        return []
    (box_size,) = struct.unpack_from(">I", head, 0)
    brands = [head[8:12]]
    end = len(head) if box_size == 0 else min(box_size, len(head))
    # bytes 12-15 are the minor version; compatible brands follow in 4-byte steps.
    for offset in range(16, end - 3, 4):
        brands.append(head[offset : offset + 4])
    return brands


def _heif_family(head: bytes) -> FileType | None:
    brands = set(ftyp_brands(head))
    if not brands:
        return None
    if brands & AVIF_BRANDS:
        return AVIF
    if brands & HEIC_BRANDS:
        return HEIC
    if brands & HEIF_BRANDS:
        return HEIF
    return None  # e.g. MP4 `isom`, QuickTime `qt  `


def _jp2(head: bytes) -> FileType | None:
    return JP2 if head[:12] == JP2_SIGNATURE else None


def _jxl(head: bytes) -> FileType | None:
    return JXL if head[:2] == b"\xff\x0a" or head[:12] == JXL_CONTAINER else None


def _pdf(head: bytes) -> FileType | None:
    index = head.find(PDF_MARKER, 0, PDF_WINDOW + len(PDF_MARKER) - 1)
    return PDF if 0 <= index < PDF_WINDOW else None


RULES: tuple[Callable[[bytes], FileType | None], ...] = (
    _jpeg,
    _png,
    _gif,
    _webp,
    _tiff,
    _bmp,
    _heif_family,
    _jp2,
    _jxl,
    _pdf,
)


def detect_type(head: bytes) -> FileType | None:
    """The accepted type of a file starting with `head` (its first 1024 bytes), or `None`."""
    for rule in RULES:
        found = rule(head)
        if found is not None:
            return found
    return None
