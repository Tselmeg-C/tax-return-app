"""Pure helpers: magic-byte sniffer, `sanitize_filename`, download names (#6 Decisions 3, 4)."""

from __future__ import annotations

import uuid

import pytest

from app.documents.filenames import content_disposition, download_name, sanitize_filename
from app.documents.sniff import detect_type
from tests.documents import files


@pytest.mark.parametrize(("name", "case"), files.accepted().items())
def test_sniffer_accepts(name: str, case: tuple[bytes, str]) -> None:
    data, mime = case
    found = detect_type(data[:1024])
    assert found is not None and found.mime_type == mime, name


@pytest.mark.parametrize(("name", "data"), files.rejected().items())
def test_sniffer_rejects(name: str, data: bytes) -> None:
    assert detect_type(data[:1024]) is None, name


def test_pdf_window() -> None:
    assert detect_type(files.pdf(junk=100)[:1024]) is not None
    assert detect_type(files.pdf(junk=1100)[:1024]) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("UTF-8''..%2F..%2Fetc%2Fpasswd", "passwd"),
        ("UTF-8''C%3A%5CUsers%5Cx%5Cbeleg.jpg", "beleg.jpg"),
        ("UTF-8''a%0D%0AX-Evil%3A%201.jpg", "aX-Evil: 1.jpg"),
        ("UTF-8''abc%E2%80%AEgpj.exe", "abcgpj.exe"),  # U+202E removed
        ("UTF-8''Rechnung%20Zahnarzt%20M%C3%BCller.pdf", "Rechnung Zahnarzt Müller.pdf"),
        ("UTF-8''Mu%CC%88ller.pdf", "Müller.pdf"),  # NFD -> NFC
        ("UTF-8''a%20%20%09b.pdf", "a b.pdf"),
        ("UTF-8''...", None),
        ("UTF-8''%2F", None),
        ("UTF-8''", None),
        ("", None),
        ("Rechnung.pdf", None),
        ("UTF-8''%FF", None),
        ("UTF-8''" + "a" * 2993, None),  # 3000 characters
        (None, None),
    ],
)
def test_sanitize_filename(raw: str | None, expected: str | None) -> None:
    assert sanitize_filename(raw) == expected


def test_sanitize_truncates_keeping_extension() -> None:
    from urllib.parse import quote

    name = sanitize_filename("UTF-8''" + quote("ä" * 300 + ".pdf"))
    assert name is not None
    encoded = name.encode("utf-8")
    assert len(encoded) <= 255
    encoded.decode("utf-8")
    assert name.endswith(".pdf")
    assert "‮" not in (sanitize_filename("UTF-8''x%E2%80%AE.pdf") or "")


def test_download_names() -> None:
    doc_id = uuid.uuid4()
    assert download_name(None, "image/png", doc_id) == f"beleg-{str(doc_id)[:8]}.png"
    assert download_name("rechnung.jpg", "application/pdf", doc_id) == "rechnung.jpg.pdf"
    assert download_name("foto.JPEG", "image/jpeg", doc_id) == "foto.JPEG"


def test_content_disposition() -> None:
    header = content_disposition('Müller "Rechnung".pdf')
    assert header == (
        'inline; filename="M_ller _Rechnung_.pdf"; '
        "filename*=UTF-8''M%C3%BCller%20%22Rechnung%22.pdf"
    )
    assert "\r" not in header and "\n" not in header
