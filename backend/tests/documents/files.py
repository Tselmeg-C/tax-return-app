"""Synthetic test files generated in code (a few bytes with the right magic bytes).

No real documents are committed. Every builder appends random bytes, so two calls give
different sha256 values unless the same bytes are reused on purpose.
"""

from __future__ import annotations

import gzip
import secrets
import struct


def _rand(n: int = 32) -> bytes:
    return secrets.token_bytes(n)


def ftyp(major: bytes, *compatible: bytes) -> bytes:
    body = major + b"\x00\x00\x00\x00" + b"".join(compatible)
    return struct.pack(">I", 8 + len(body)) + b"ftyp" + body


def jpeg() -> bytes:
    return b"\xff\xd8\xff\xe0" + _rand()


def png(extra: bytes = b"") -> bytes:
    return b"\x89PNG\r\n\x1a\n" + _rand() + extra


def pdf(junk: int = 0) -> bytes:
    return b"J" * junk + b"%PDF-1.7\n" + _rand() + b"\n%%EOF\n"


def bmp(dib: int = 40) -> bytes:
    return b"BM" + b"\x00" * 12 + struct.pack("<I", dib) + _rand()


def accepted() -> dict[str, tuple[bytes, str]]:
    """Fresh accepted fixtures (Decision 4's table)."""
    return {
        "jpeg": (jpeg(), "image/jpeg"),
        "png": (png(), "image/png"),
        "gif87a": (b"GIF87a" + _rand(), "image/gif"),
        "gif89a": (b"GIF89a" + _rand(), "image/gif"),
        "webp": (b"RIFF" + b"\x10\x00\x00\x00" + b"WEBP" + _rand(), "image/webp"),
        "tiff-ii": (b"II*\x00" + _rand(), "image/tiff"),
        "tiff-mm": (b"MM\x00*" + _rand(), "image/tiff"),
        "bmp": (bmp(), "image/bmp"),
        "heic": (ftyp(b"heic", b"mif1", b"heic") + _rand(), "image/heic"),
        "heic-mif1": (ftyp(b"mif1", b"mif1", b"heic") + _rand(), "image/heic"),
        "heif-mif1": (ftyp(b"mif1", b"mif1") + _rand(), "image/heif"),
        "avif": (ftyp(b"avif", b"mif1", b"avif") + _rand(), "image/avif"),
        "avif-mif1": (ftyp(b"mif1", b"mif1", b"avif") + _rand(), "image/avif"),
        "jp2": (bytes.fromhex("0000000C6A5020200D0A870A") + _rand(), "image/jp2"),
        "jxl-codestream": (b"\xff\x0a" + _rand(), "image/jxl"),
        "jxl-container": (bytes.fromhex("0000000C4A584C200D0A870A") + _rand(), "image/jxl"),
        "pdf": (pdf(), "application/pdf"),
    }


SVG = (
    b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
)


def rejected() -> dict[str, bytes]:
    return {
        "svg": SVG + _rand(),
        "svgz": gzip.compress(SVG + _rand()),
        "ico": b"\x00\x00\x01\x00\x01\x00\x10\x10" + _rand(),
        "mp4": ftyp(b"isom", b"isom", b"iso2", b"mp41") + _rand(),
        "bmp-bad-dib": bmp(dib=99),
        "zip": b"PK\x03\x04" + _rand(),
        "html": b"<!doctype html><html><body>hi</body></html>" + _rand().hex().encode(),
        "text": b"just some text " + _rand().hex().encode(),
    }
