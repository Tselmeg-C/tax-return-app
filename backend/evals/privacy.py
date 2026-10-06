"""Privacy scan for datasets, recordings and baselines (the repo is public).

Fails on: an 11-digit number (Steuer-ID shape, with or without spaces), an IBAN with a valid
mod-97 checksum, an e-mail address outside example.com / example.org, an EXIF block in an
image, PDF metadata Author / Creator other than the generator's. Findings name the location
and the rule only, never the match.
"""

from __future__ import annotations

import io
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image
from pypdf import PdfReader

from evals.synth.writer import PDF_AUTHOR, PDF_CREATOR

STEUER_ID_RE = re.compile(r"(?<![0-9])[0-9](?: ?[0-9]){10}(?! ?[0-9])")
IBAN_START_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{2}[0-9]{2}")
# IBAN length per country (SEPA area plus common others); a candidate is cut to exactly this
# length, so text after the IBAN ("BIC ...", "2025", "EUR") cannot hide it.
IBAN_LENGTHS: dict[str, int] = {
    "AD": 24,
    "AT": 20,
    "BE": 16,
    "BG": 22,
    "CH": 21,
    "CY": 28,
    "CZ": 24,
    "DE": 22,
    "DK": 18,
    "EE": 20,
    "ES": 24,
    "FI": 18,
    "FO": 18,
    "FR": 27,
    "GB": 22,
    "GI": 23,
    "GL": 18,
    "GR": 27,
    "HR": 21,
    "HU": 28,
    "IE": 22,
    "IS": 26,
    "IT": 27,
    "LI": 21,
    "LT": 20,
    "LU": 20,
    "LV": 21,
    "MC": 27,
    "MT": 31,
    "NL": 18,
    "NO": 15,
    "PL": 28,
    "PT": 25,
    "RO": 24,
    "SE": 24,
    "SI": 19,
    "SK": 24,
    "SM": 27,
    "TR": 26,
    "UA": 29,
    "VA": 22,
}
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")
ALLOWED_EMAIL_DOMAINS = ("example.com", "example.org")
HASH_KEYS = frozenset({"hash", "dataset_hash", "git_sha"})
"""JSON keys holding hex digests (they may contain digit runs by chance)."""


@dataclass(frozen=True)
class Finding:
    location: str
    rule: str

    def __str__(self) -> str:
        return f"{self.location}: privacy rule {self.rule}"


def iban_valid(candidate: str) -> bool:
    compact = candidate.replace(" ", "").upper()
    if not 15 <= len(compact) <= 34:
        return False
    rearranged = compact[4:] + compact[:4]
    digits = "".join(str(int(ch, 36)) for ch in rearranged)
    return int(digits) % 97 == 1


def contains_valid_iban(text: str) -> bool:
    """True if any substring is a checksum-valid IBAN: any case, single spaces optional."""
    for m in IBAN_START_RE.finditer(text):
        length = IBAN_LENGTHS.get(m.group(0)[:2].upper())
        if length is None:
            continue
        compact = []
        for ch in text[m.start() : m.start() + 2 * length]:
            if ch == " ":
                continue
            if not ch.isascii() or not ch.isalnum():
                break
            compact.append(ch)
            if len(compact) == length:
                break
        if len(compact) == length and iban_valid("".join(compact)):
            return True
    return False


def scan_text(text: str) -> list[str]:
    """Rule names violated by `text` (deduplicated, never the matches)."""
    rules = []
    if STEUER_ID_RE.search(text):
        rules.append("steuer_id_shape")
    if contains_valid_iban(text):
        rules.append("valid_iban")
    for m in EMAIL_RE.finditer(text):
        domain = m.group(1).lower()
        if domain not in ALLOWED_EMAIL_DOMAINS:
            rules.append("email_address")
            break
    return rules


def _json_strings(value: Any, key: str | None = None) -> Iterator[str]:
    if key in HASH_KEYS:
        return
    if isinstance(value, dict):
        for k, v in value.items():
            yield str(k)
            yield from _json_strings(v, str(k))
    elif isinstance(value, list):
        for v in value:
            yield from _json_strings(v)
    elif value is not None:
        yield str(value)


def _pdf_findings(location: str, data: bytes) -> list[Finding]:
    findings = []
    reader = PdfReader(io.BytesIO(data))
    meta: dict[str, Any] = dict(reader.metadata or {})
    author = meta.get("/Author")
    creator = meta.get("/Creator")
    if author is not None and str(author) != PDF_AUTHOR:
        findings.append(Finding(location, "pdf_metadata_author"))
    if creator is not None and str(creator) != PDF_CREATOR:
        findings.append(Finding(location, "pdf_metadata_creator"))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    findings += [Finding(location, r) for r in scan_text(text)]
    return findings


def _image_findings(location: str, data: bytes) -> list[Finding]:
    with Image.open(io.BytesIO(data)) as img:
        has_exif = len(img.getexif()) > 0 or "exif" in img.info
    if has_exif or b"Exif\x00\x00" in data[:65536]:
        return [Finding(location, "image_exif")]
    return []


def scan_file(path: Path, location: str) -> list[Finding]:
    data = path.read_bytes()
    suffix = path.suffix.lower()
    # Dispatch on content, not the extension (a mislabelled file is still scanned).
    if data.startswith(b"%PDF-") or suffix == ".pdf":
        try:
            return _pdf_findings(location, data)
        except Exception:  # noqa: BLE001 - unreadable PDF: report the rule, never the error
            return [Finding(location, "unreadable_pdf")]
    if data.startswith((b"\xff\xd8\xff", b"\x89PNG")) or suffix in (".jpg", ".jpeg", ".png"):
        try:
            return _image_findings(location, data)
        except Exception:  # noqa: BLE001
            return [Finding(location, "unreadable_image")]
    text = data.decode("utf-8", errors="replace")
    if suffix == ".json":
        try:
            text = "\n".join(_json_strings(json.loads(text)))
        except json.JSONDecodeError:
            pass
    elif suffix == ".jsonl":
        parts: list[str] = []
        for line in text.splitlines():
            try:
                parts.extend(_json_strings(json.loads(line)))
            except json.JSONDecodeError:
                parts.append(line)
        text = "\n".join(parts)
    return [Finding(location, r) for r in scan_text(text)]


def scan_dataset(root: Path) -> list[Finding]:
    """Every case file (label, document) plus the manifest. Location = case id."""
    findings: list[Finding] = []
    manifest = root / "manifest.yaml"
    if manifest.is_file():
        findings += scan_file(manifest, "manifest.yaml")
    cases = root / "cases"
    if not cases.is_dir():
        return findings
    for case_dir in sorted(p for p in cases.iterdir() if p.is_dir()):
        for path in sorted(p for p in case_dir.iterdir() if p.is_file()):
            findings += scan_file(path, case_dir.name)
    return findings


def scan_tree(root: Path, label_root: Path) -> list[Finding]:
    """Every file under `root` (recordings / baselines). Location = path relative to label_root."""
    findings: list[Finding] = []
    if not root.is_dir():
        return findings
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        findings += scan_file(path, path.relative_to(label_root).as_posix())
    return findings
