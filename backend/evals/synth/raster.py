"""Turn a rendered PDF into the `pdf_scanned`, `photo_jpeg` and `png` variants.

Rasterised with pypdfium2, then seeded Pillow transforms (rotation, keystone, uneven light,
noise). Every random value comes from `random.Random(seed)`; images carry no EXIF.
"""

from __future__ import annotations

import io
import random

import pypdfium2 as pdfium
from PIL import Image, ImageChops, ImageFilter
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

from evals.synth.writer import PDF_AUTHOR, PDF_CREATOR


def render_pages(pdf: bytes, long_side: int, grayscale: bool) -> list[Image.Image]:
    doc = pdfium.PdfDocument(pdf)
    try:
        pages = []
        for i in range(len(doc)):
            page = doc[i]
            w, h = page.get_size()
            scale = long_side / max(w, h)
            bitmap = page.render(scale=scale, grayscale=grayscale)
            img = bitmap.to_pil().convert("L" if grayscale else "RGB")
            pages.append(img.copy())
            page.close()
        return pages
    finally:
        doc.close()


def _noise(rng: random.Random, size: tuple[int, int], amplitude: int) -> Image.Image:
    raw = rng.randbytes(size[0] * size[1])
    img = Image.frombytes("L", size, raw)
    # map 0..255 -> 128-amplitude .. 128+amplitude
    return img.point(lambda v: 128 - amplitude + (v * 2 * amplitude) // 255)


def _add_noise(img: Image.Image, rng: random.Random, amplitude: int) -> Image.Image:
    noise = _noise(rng, img.size, amplitude)
    if img.mode == "RGB":
        noise = Image.merge("RGB", (noise, noise, noise))
    return ImageChops.add(img, noise, scale=1.0, offset=-128)


def _light(img: Image.Image, rng: random.Random, low: int) -> Image.Image:
    # Rotate a large gradient and crop its centre so no black corners remain.
    big = Image.linear_gradient("L").resize((512, 512)).rotate(rng.uniform(0, 360))
    grad = big.crop((106, 106, 406, 406)).resize(img.size, Image.Resampling.BILINEAR)
    grad = grad.point(lambda v: low + (v * (255 - low)) // 255)
    if img.mode == "RGB":
        grad = Image.merge("RGB", (grad, grad, grad))
    return ImageChops.multiply(img, grad)


def _jpeg(img: Image.Image, quality: int) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def scanned_pdf(pdf: bytes, seed: int) -> bytes:
    """Image-only PDF (no text layer): grey 150 dpi scan, slight skew and noise per page."""
    rng = random.Random(seed)
    out = io.BytesIO()
    c = Canvas(out, pagesize=A4, invariant=1, pageCompression=1)
    c.setAuthor(PDF_AUTHOR)
    c.setCreator(PDF_CREATOR)
    c.setTitle("Synthetisches Testdokument")
    for page in render_pages(pdf, long_side=1754, grayscale=True):
        img = page.rotate(rng.uniform(-0.8, 0.8), resample=Image.Resampling.BICUBIC, fillcolor=250)
        img = _add_noise(img, rng, 10).filter(ImageFilter.GaussianBlur(0.5))
        jpeg = _jpeg(img, 70)
        pw, ph = A4
        # keep the page aspect ratio (receipts are narrow)
        iw, ih = img.size
        scale = min(pw / iw, ph / ih)
        c.drawImage(
            ImageReader(io.BytesIO(jpeg)),
            (pw - iw * scale) / 2,
            ph - ih * scale,
            iw * scale,
            ih * scale,
        )
        c.showPage()
    c.save()
    return out.getvalue()


def _keystone(img: Image.Image, rng: random.Random) -> Image.Image:
    w, h = img.size
    j = 0.03

    def jx() -> float:
        return rng.uniform(-j, j) * w

    def jy() -> float:
        return rng.uniform(-j, j) * h

    # QUAD maps this source quadrilateral (UL, LL, LR, UR) onto the output rectangle.
    quad = (jx(), jy(), jx(), h + jy(), w + jx(), h + jy(), w + jx(), jy())
    return img.transform(
        (w, h), Image.Transform.QUAD, quad, Image.Resampling.BICUBIC, fillcolor=(92, 78, 64)
    )


def photo_jpeg(pdf: bytes, seed: int, long_side: int = 1600) -> bytes:
    """Phone photo of the first page on a table: rotation, keystone, uneven light, noise."""
    rng = random.Random(seed)
    page = render_pages(pdf, long_side=1400, grayscale=False)[0]
    page = page.rotate(
        rng.uniform(-4.0, 4.0),
        resample=Image.Resampling.BICUBIC,
        expand=True,
        fillcolor=(92, 78, 64),
    )
    margin = 70
    canvas = Image.new("RGB", (page.width + 2 * margin, page.height + 2 * margin), (92, 78, 64))
    canvas.paste(page, (margin, margin))
    canvas = _keystone(canvas, rng)
    scale = long_side / max(canvas.size)
    canvas = canvas.resize(
        (round(canvas.width * scale), round(canvas.height * scale)), Image.Resampling.LANCZOS
    )
    canvas = _light(canvas, rng, low=175)
    canvas = _add_noise(canvas, rng, 6).filter(ImageFilter.GaussianBlur(0.6))
    return _jpeg(canvas, 80)


def png(pdf: bytes, seed: int, long_side: int = 1200) -> bytes:
    """Clean screenshot-like grey PNG of the first page."""
    del seed
    page = render_pages(pdf, long_side=long_side, grayscale=True)[0]
    buf = io.BytesIO()
    page.quantize(16).save(buf, format="PNG", optimize=True)
    return buf.getvalue()
