"""Generator: determinism (`--check`), spec edits detected, document contents."""

from __future__ import annotations

import io
import shutil
from dataclasses import replace
from pathlib import Path

import pytest
from pypdf import PdfReader

from evals.dataset import load_dataset
from evals.paths import EVALS_DIR, EvalPaths
from evals.synth.__main__ import main as synth_main
from evals.synth.writer import FOOTER, eur_de, eur_en, words_de
from tests.evals.conftest import DATASET

ONE_PER_VARIANT = [
    "b001-arbeitsmittel-notebook",  # pdf_text
    "b003-fortbildung-seminar",  # pdf_scanned
    "b028-apotheke-gemischt",  # photo_jpeg
    "b018-haftpflicht",  # png
]


def _pdf_text(path: Path) -> str:
    return "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(path.read_bytes())).pages)


def test_check_passes_for_committed_files(capsys: pytest.CaptureFixture[str]) -> None:
    args = ["--dataset", DATASET, "--check"]
    for case_id in ONE_PER_VARIANT:
        args += ["--case", case_id]
    assert synth_main(args) == 0
    assert "check ok" in capsys.readouterr().out


def test_changed_amount_in_spec_is_detected(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    specs = tmp_path / "specs"
    shutil.copytree(EVALS_DIR / "synth" / "specs", specs)
    spec = specs / f"{DATASET}.yaml"
    text = spec.read_text(encoding="utf-8")
    before = '      deductible_amount: "240.00"'
    assert text.count(before) == 1
    spec.write_text(text.replace(before, '      deductible_amount: "250.00"'), encoding="utf-8")
    paths = replace(EvalPaths(), specs=specs)
    code = synth_main(
        ["--dataset", DATASET, "--check", "--case", "b012-steuerberatung-split"], paths
    )
    out = capsys.readouterr().out
    assert code == 1
    assert "b012-steuerberatung-split: differs" in out


def test_generate_one_case_is_identical_to_committed(tmp_path: Path) -> None:
    paths = replace(EvalPaths(), datasets=tmp_path)
    assert synth_main(["--dataset", DATASET, "--case", "b056-lstb-alex"], paths) == 0
    for name in ("document.pdf", "label.yaml"):
        fresh = tmp_path / DATASET / "cases" / "b056-lstb-alex" / name
        committed = EVALS_DIR / "datasets" / DATASET / "cases" / "b056-lstb-alex" / name
        assert fresh.read_bytes() == committed.read_bytes()


def test_pdf_text_documents_match_labels_and_carry_footer() -> None:
    ds = load_dataset(DATASET)
    text_cases = [c for c in ds.cases if c.variant == "pdf_text"]
    assert text_cases
    for case in text_cases:
        text = _pdf_text(case.path)
        label = ds.labels[case.id].expected
        reader = PdfReader(io.BytesIO(case.read_bytes()))
        for page in reader.pages:
            assert FOOTER in (page.extract_text() or ""), case.id
        if label.gross_amount is not None and label.category is not None:
            amount = abs(label.gross_amount)
            assert eur_de(amount, symbol=False) in text or eur_en(amount) in text, case.id
        if label.vendor:
            assert label.vendor in text, case.id
        assert "Muster" in text or "Beispiel" in text


def test_lstb_masks_steuer_id() -> None:
    text = _pdf_text(EVALS_DIR / "datasets" / DATASET / "cases" / "b056-lstb-alex" / "document.pdf")
    assert "XX XXX XXX XXX" in text
    assert "Alex Muster" in text


def test_multi_page_total_only_on_last_page() -> None:
    path = (
        EVALS_DIR
        / "datasets"
        / DATASET
        / "cases"
        / "b041-erhaltung-bad-mehrseitig"
        / "document.pdf"
    )
    pages = [p.extract_text() or "" for p in PdfReader(io.BytesIO(path.read_bytes())).pages]
    assert len(pages) == 3
    assert "5.635,00" in pages[-1]
    assert all("5.635,00" not in p for p in pages[:-1])


def test_images_have_no_exif() -> None:
    from PIL import Image

    for path in (EVALS_DIR / "datasets" / DATASET / "cases").glob("*/document.*"):
        if path.suffix in (".jpg", ".png"):
            with Image.open(path) as img:
                assert len(img.getexif()) == 0
                assert max(img.size) <= 1600


def test_words_de() -> None:
    assert words_de(350) == "dreihundertfünfzig"
    assert words_de(120) == "einhundertzwanzig"
    assert words_de(1021) == "eintausendeinundzwanzig"
