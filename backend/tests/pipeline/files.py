"""Synthetic test files for the pipeline, generated in code (no committed documents)."""

from __future__ import annotations

import io
import secrets
import struct
import zlib

from PIL import Image
from pillow_heif import register_heif_opener
from pypdf import PdfReader, PdfWriter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

register_heif_opener()


def _noise() -> tuple[int, int, int]:
    # A random colour so two calls never give the same bytes (sha256 dedupe in #6).
    return (secrets.randbelow(256), secrets.randbelow(256), secrets.randbelow(256))


def image(fmt: str, size: tuple[int, int] = (40, 20), mode: str = "RGB", **save: object) -> bytes:
    img = Image.new(mode, size, _noise() if mode == "RGB" else None)
    out = io.BytesIO()
    img.save(out, format=fmt, **save)
    return out.getvalue()


def jpeg(size: tuple[int, int] = (40, 20)) -> bytes:
    return image("JPEG", size)


def jpeg_rotated() -> bytes:
    """40×20 pixels with EXIF orientation 6 (rotate 90° clockwise to display): upright 20×40."""
    img = Image.new("RGB", (40, 20), _noise())
    exif = Image.Exif()
    exif[0x0112] = 6
    out = io.BytesIO()
    img.save(out, format="JPEG", exif=exif.tobytes())
    return out.getvalue()


def gif_animated(frames: int = 3) -> bytes:
    imgs = [Image.new("RGB", (30, 30), (80 * i, 10, 10)) for i in range(frames)]
    imgs[0].putpixel((0, 0), _noise())
    out = io.BytesIO()
    imgs[0].save(out, format="GIF", save_all=True, append_images=imgs[1:], duration=100, loop=0)
    return out.getvalue()


def tiff(pages: int = 1) -> bytes:
    imgs = [Image.new("RGB", (40, 20), _noise()) for _ in range(pages)]
    out = io.BytesIO()
    imgs[0].save(out, format="TIFF", save_all=True, append_images=imgs[1:])
    return out.getvalue()


ALL_IMAGES = {
    "jpeg": ("image/jpeg", lambda: jpeg()),
    "png": ("image/png", lambda: image("PNG")),
    "webp": ("image/webp", lambda: image("WEBP")),
    "bmp": ("image/bmp", lambda: image("BMP")),
    "tiff": ("image/tiff", lambda: tiff(1)),
    "jp2": ("image/jp2", lambda: image("JPEG2000")),
    "heic": ("image/heic", lambda: image("HEIF")),
    "heif": ("image/heif", lambda: image("HEIF")),
    "avif": ("image/avif", lambda: image("AVIF")),
}


def jxl() -> bytes:
    return b"\xff\x0a" + secrets.token_bytes(32)


def png_bomb(width: int = 30_000, height: int = 30_000) -> bytes:
    """A PNG header claiming huge dimensions, without pixel data."""

    def chunk(kind: bytes, body: bytes) -> bytes:
        crc = zlib.crc32(kind + body) & 0xFFFFFFFF
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00" * 64)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def pdf_text(pages: int = 2, text: str | None = None) -> bytes:
    out = io.BytesIO()
    c = canvas.Canvas(out)
    for n in range(pages):
        line = text or "Rechnung Testmodus Beispiel GmbH, Musterstadt, Gesamtbetrag 12,34 EUR"
        c.drawString(72, 720, f"{line} (Seite {n + 1}, {secrets.token_hex(4)})")
        c.showPage()
    c.save()
    return out.getvalue()


def pdf_image_only() -> bytes:
    out = io.BytesIO()
    c = canvas.Canvas(out)
    buf = io.BytesIO()
    Image.new("RGB", (60, 40), _noise()).save(buf, format="PNG")
    c.drawImage(ImageReader(io.BytesIO(buf.getvalue())), 72, 600, width=200, height=120)
    c.showPage()
    c.save()
    return out.getvalue()


def pdf_encrypted() -> bytes:
    writer = PdfWriter()
    writer.append(PdfReader(io.BytesIO(pdf_text(1))))
    writer.encrypt(secrets.token_hex(8))
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def truncated(data: bytes) -> bytes:
    return data[: len(data) // 2]
