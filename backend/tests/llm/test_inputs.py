"""Input preflight: downscale, metadata stripping, bombs, formats, PDF limits, request size."""

from __future__ import annotations

import io
import secrets
import struct
import zlib

import pypdfium2 as pdfium
import pytest
from PIL import Image
from pypdf import PdfReader, PdfWriter

from app.llm import ImagePart, LLMResult, PdfPart, TextPart
from app.llm.errors import LLMInputInvalid, LLMInputTooLarge
from app.llm.fake import FakeProvider, FakeReply
from app.llm.inputs import InputLimits, preflight
from app.llm.provider import LLMProvider
from app.llm.smoke import synthetic_pdf
from app.llm.types import LLMRequest, T

from .helpers import EXPECTED, Synthetic, llm_settings, make_router, routing

LIMITS = InputLimits()


class Recorder:
    """Records the parts a provider receives (test only), then answers like the fake."""

    name = "fake"

    def __init__(self) -> None:
        self.requests: list[LLMRequest[Synthetic]] = []
        self._fake = FakeProvider([FakeReply(data=EXPECTED)] * 10, app_env="test")

    async def structured(self, request: LLMRequest[T]) -> LLMResult[T]:
        self.requests.append(request)  # type: ignore[arg-type]
        return await self._fake.structured(request)

    async def aclose(self) -> None:
        return None


async def send(parts: list, **routing_overrides: object) -> Recorder:  # type: ignore[type-arg]
    recorder = Recorder()
    provider: LLMProvider = recorder
    router = make_router(
        providers={"fake": provider},
        routing_config=routing(classify=routing_overrides) if routing_overrides else None,
    )
    await router.structured(
        task="classify", system="s", parts=parts, schema=Synthetic, prompt_version="p"
    )
    return recorder


def jpeg(size: tuple[int, int], **save: object) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, (200, 180, 160)).save(out, format="JPEG", quality=70, **save)
    return out.getvalue()


async def test_large_jpeg_is_downscaled() -> None:
    recorder = await send([ImagePart(data=jpeg((6000, 4000)), mime="image/jpeg")])
    sent = recorder.requests[0].parts[0]
    assert isinstance(sent, ImagePart) and sent.mime == "image/jpeg"
    image = Image.open(io.BytesIO(sent.data))
    assert image.format == "JPEG"
    assert max(image.size) == 2048
    assert abs(image.size[1] - round(2048 * 4000 / 6000)) <= 1


async def test_exif_gps_is_stripped_and_orientation_applied() -> None:
    exif = Image.Exif()
    exif[0x0112] = 6  # orientation: rotate 90° CW to display
    gps = {1: "N", 2: (52.0, 31.0, 12.0), 3: "E", 4: (13.0, 24.0, 0.0)}
    exif[0x8825] = gps
    data = jpeg((400, 200), exif=exif.tobytes())
    assert Image.open(io.BytesIO(data)).getexif()  # the fixture really has EXIF
    recorder = await send([ImagePart(data=data, mime="image/jpeg")])
    sent = recorder.requests[0].parts[0]
    assert isinstance(sent, ImagePart)
    image = Image.open(io.BytesIO(sent.data))
    assert image.size == (200, 400)  # rotated upright
    assert len(image.getexif()) == 0
    assert "exif" not in image.info
    assert b"Exif" not in sent.data


def _png_header_only(width: int, height: int) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        crc = zlib.crc32(kind + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IEND", b"")


async def test_decompression_bomb_is_rejected_before_http() -> None:
    recorder = Recorder()
    router = make_router(providers={"fake": recorder})
    with pytest.raises(LLMInputInvalid):
        await router.structured(
            task="classify",
            system="s",
            parts=[ImagePart(data=_png_header_only(10_000, 10_000), mime="image/png")],
            schema=Synthetic,
            prompt_version="p",
        )
    assert recorder.requests == []


def test_heic_and_tiff_are_rejected() -> None:
    tiff = io.BytesIO()
    Image.new("RGB", (10, 10)).save(tiff, format="TIFF")
    with pytest.raises(LLMInputInvalid):
        preflight([ImagePart(data=tiff.getvalue(), mime="image/tiff")], LIMITS, "native")  # type: ignore[arg-type]
    with pytest.raises(LLMInputInvalid):  # TIFF bytes declared as JPEG
        preflight([ImagePart(data=tiff.getvalue(), mime="image/jpeg")], LIMITS, "native")
    heic = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\x00" * 64
    with pytest.raises(LLMInputInvalid):
        preflight([ImagePart(data=heic, mime="image/heic")], LIMITS, "native")  # type: ignore[arg-type]
    with pytest.raises(LLMInputInvalid):
        preflight([ImagePart(data=heic, mime="image/jpeg")], LIMITS, "native")


async def test_pdf_native_and_rasterized() -> None:
    pdf = synthetic_pdf(2)
    recorder = await send([PdfPart(pdf)])
    parts = recorder.requests[0].parts
    assert len(parts) == 1 and isinstance(parts[0], PdfPart) and parts[0].data == pdf

    recorder = await send([PdfPart(pdf)], model="fake:raster")
    parts = recorder.requests[0].parts
    assert len(parts) == 2
    for part in parts:
        assert isinstance(part, ImagePart)
        assert max(Image.open(io.BytesIO(part.data)).size) <= 2048


async def test_pdf_over_page_limit_is_rejected_before_http() -> None:
    settings = llm_settings()
    recorder = Recorder()
    router = make_router(providers={"fake": recorder}, settings=settings)
    with pytest.raises(LLMInputTooLarge):
        await router.structured(
            task="classify",
            system="s",
            parts=[PdfPart(synthetic_pdf(settings.llm_max_pdf_pages + 1))],
            schema=Synthetic,
            prompt_version="p",
        )
    assert recorder.requests == []
    preflight([PdfPart(synthetic_pdf(settings.llm_max_pdf_pages))], LIMITS, "native")


def test_request_size_and_image_count_limits() -> None:
    image = ImagePart(data=jpeg((100, 100)), mime="image/jpeg")
    with pytest.raises(LLMInputTooLarge):
        preflight([image] * 3, InputLimits(max_images=2), "native")
    with pytest.raises(LLMInputTooLarge):
        preflight([image], InputLimits(max_request_bytes=100), "native")
    settings = llm_settings(llm_max_request_mb=0.001, llm_max_images=1)
    limits = InputLimits.from_settings(settings)
    assert limits.max_request_bytes == 1000 and limits.max_images == 1
    noise = io.BytesIO()
    Image.effect_noise((200, 200), 64).convert("RGB").save(noise, format="JPEG")
    with pytest.raises(LLMInputTooLarge):  # well over 1000 bytes even after re-encoding
        preflight([TextPart("x"), ImagePart(noise.getvalue(), "image/jpeg")], limits, "native")


def test_truncated_and_encrypted_pdf_are_invalid() -> None:
    pdf = synthetic_pdf(2)
    with pytest.raises(LLMInputInvalid):
        preflight([PdfPart(pdf[: len(pdf) // 3])], LIMITS, "native")
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(pdf)))
    writer.encrypt(user_password="fictional-pw", algorithm="RC4-128")
    out = io.BytesIO()
    writer.write(out)
    with pytest.raises(LLMInputInvalid):
        preflight([PdfPart(out.getvalue())], LIMITS, "native")
    with pytest.raises(pdfium.PdfiumError):  # the fixture really is password-protected
        pdfium.PdfDocument(out.getvalue())


def test_animated_gif_uses_first_frame() -> None:
    frames = [Image.new("RGB", (50, 40), color) for color in ("red", "blue")]
    out = io.BytesIO()
    frames[0].save(out, format="GIF", save_all=True, append_images=frames[1:])
    result = preflight([ImagePart(data=out.getvalue(), mime="image/gif")], LIMITS, "native")
    sent = result.parts[0]
    assert isinstance(sent, ImagePart) and sent.mime in ("image/jpeg", "image/png")
    assert getattr(Image.open(io.BytesIO(sent.data)), "n_frames", 1) == 1


def test_small_clean_images_are_re_encoded_without_unknown_segments() -> None:
    sentinel = f"SENTINEL{secrets.token_hex(6)}".encode()
    plain = jpeg((100, 80))
    payload = b"Ducky" + sentinel
    app12 = b"\xff\xec" + struct.pack(">H", len(payload) + 2) + payload
    with_app12 = plain[:2] + app12 + plain[2:]
    assert Image.open(io.BytesIO(with_app12)).size == (100, 80)  # still a valid JPEG
    for data, mime in ((with_app12, "image/jpeg"), (_png(sentinel), "image/png")):
        result = preflight([ImagePart(data=data, mime=mime)], LIMITS, "native")  # type: ignore[arg-type]
        sent = result.parts[0]
        assert isinstance(sent, ImagePart)
        assert sentinel not in sent.data
        assert result.n_images == 1


def _png(sentinel: bytes) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (100, 80), "white").save(out, format="PNG")
    return out.getvalue() + sentinel  # trailing bytes after IEND


def test_multi_picture_jpeg_uses_primary_frame() -> None:
    primary = Image.new("RGB", (120, 90), (250, 0, 0))
    second = Image.new("RGB", (60, 45), (0, 0, 250))
    out = io.BytesIO()
    primary.save(out, format="MPO", save_all=True, append_images=[second])
    assert Image.open(io.BytesIO(out.getvalue())).format == "MPO"
    result = preflight([ImagePart(data=out.getvalue(), mime="image/jpeg")], LIMITS, "native")
    sent = result.parts[0]
    assert isinstance(sent, ImagePart) and sent.mime == "image/jpeg"
    image = Image.open(io.BytesIO(sent.data))
    assert image.format == "JPEG" and image.size == (120, 90)
    red, _, blue = image.getpixel((60, 45))  # type: ignore[misc]
    assert red > 200 and blue < 50


def test_empty_parts_raise_value_error() -> None:
    with pytest.raises(ValueError):
        preflight([], LIMITS, "native")
