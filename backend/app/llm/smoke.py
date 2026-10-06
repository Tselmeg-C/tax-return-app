"""`python -m app.llm.smoke [--task classify]`: one live LLM call with a synthetic input.

Prints only `provider, model, input/output tokens, cost_eur, latency_ms, request_id`.
Without `OPENAI_API_KEY` it prints `SKIPPED: OPENAI_API_KEY is not set` and exits 0.
Used at the final deployment to see the cost metric in Grafana.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import logging
import os
import sys
from enum import StrEnum

from PIL import Image, ImageDraw
from pydantic import BaseModel

from app.config import get_llm_settings
from app.llm.errors import LLMError
from app.llm.types import ImagePart, Part, PdfPart, TextPart

SMOKE_PROMPT_VERSION = "smoke-1"
SMOKE_SYSTEM = (
    "You classify a synthetic test document. Answer with the document kind, the vendor "
    "name if one is printed, and the number of pages or images you were given."
)
_RECEIPT_LINES = (
    "Frischmarkt Muster",
    "Musterstrasse 1, 12345 Musterstadt",
    "Brot           2.49",
    "Milch          1.19",
    "SUMME EUR      3.68",
)


class SmokeKind(StrEnum):
    receipt = "receipt"
    invoice = "invoice"
    other = "other"


class SmokeClassification(BaseModel):
    kind: SmokeKind
    vendor: str | None
    pages_or_images: int


def _receipt_image(lines: tuple[str, ...] = _RECEIPT_LINES) -> Image.Image:
    image = Image.new("RGB", (600, 400), "white")
    draw = ImageDraw.Draw(image)
    for index, line in enumerate(lines):
        draw.text((40, 40 + index * 60), line, fill="black", font_size=32)
    return image


def synthetic_receipt_jpeg() -> bytes:
    out = io.BytesIO()
    _receipt_image().save(out, format="JPEG", quality=85)
    return out.getvalue()


def synthetic_pdf(pages: int = 2) -> bytes:
    images = [_receipt_image((*_RECEIPT_LINES[:1], f"Seite {n + 1}")) for n in range(pages)]
    out = io.BytesIO()
    images[0].save(out, format="PDF", save_all=True, append_images=images[1:])
    return out.getvalue()


def synthetic_parts() -> list[Part]:
    """A text part, a Pillow-drawn receipt and a 2-page PDF (all fictional, made at runtime)."""
    return [
        TextPart("Synthetic test input for the belegbot smoke check."),
        ImagePart(data=synthetic_receipt_jpeg(), mime="image/jpeg"),
        PdfPart(data=synthetic_pdf(2)),
    ]


async def _run(task: str) -> int:
    from app.llm.router import get_router

    router = get_router()
    try:
        result = await router.structured(
            task=task,
            system=SMOKE_SYSTEM,
            parts=synthetic_parts(),
            schema=SmokeClassification,
            prompt_version=SMOKE_PROMPT_VERSION,
        )
    except LLMError as exc:
        print(f"FAILED: {type(exc).__name__} request_id={exc.request_id}")
        return 1
    finally:
        await router.aclose()
    print(
        f"provider={result.provider} model={result.model} "
        f"input_tokens={result.input_tokens} output_tokens={result.output_tokens} "
        f"cost_eur={result.cost_eur} latency_ms={result.latency_ms} "
        f"request_id={result.request_id}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.llm.smoke")
    parser.add_argument("--task", default="classify")
    args = parser.parse_args(argv)
    if get_llm_settings().openai_api_key is None:
        print("SKIPPED: OPENAI_API_KEY is not set")
        return 0
    from app.observability import WORKER_SERVICE_NAME, setup_observability

    # stdout carries only the result (or FAILED / SKIPPED) line: no JSON log lines there.
    # Logs, metrics and spans still go to OTLP when OTEL_EXPORTER_OTLP_ENDPOINT is set.
    observability = setup_observability(
        WORKER_SERVICE_NAME, env={**os.environ, "LOG_LEVEL": "WARNING"}
    )
    root = logging.getLogger()
    for handler in list(root.handlers):
        if isinstance(handler, logging.StreamHandler) and handler.stream is sys.stdout:
            root.removeHandler(handler)
    root.setLevel(logging.INFO)
    try:
        return asyncio.run(_run(args.task))
    finally:
        observability.force_flush()


if __name__ == "__main__":
    sys.exit(main())
