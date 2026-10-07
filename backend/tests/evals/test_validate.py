"""Dataset validation and privacy scan (on temp copies of bills_v0)."""

from __future__ import annotations

import io
import random
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml
from PIL import Image

from evals import validate
from evals.dataset import EvalCase, dataset_hash, load_dataset
from evals.paths import EVALS_DIR, EvalPaths
from evals.privacy import scan_text
from evals.synth.writer import Writer
from tests.evals.conftest import DATASET

BACKEND = EVALS_DIR.parent


def _cases(paths: EvalPaths) -> Path:
    return paths.datasets / DATASET / "cases"


def _edit_label(paths: EvalPaths, case_id: str, edit: Callable[[dict[str, object]], None]) -> None:
    path = _cases(paths) / case_id / "label.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    edit(data)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _run(paths: EvalPaths, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = validate.main(["--dataset", DATASET], paths)
    out = capsys.readouterr()
    return code, out.out + out.err


def test_committed_dataset_is_valid_with_coverage(
    eval_paths: EvalPaths, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = _run(eval_paths, capsys)
    assert code == 0, out
    assert "validate ok" in out
    n = int(re.search(r"cases: (\d+)", out).group(1))  # type: ignore[union-attr]
    assert n >= 50
    assert "| official_no_category | 4 | 4 |" in out
    assert "exempt:" in out
    for tag in ("cash_35a", "multi_page", "no_payment_date", "duplicate_rephotographed"):
        assert re.search(rf"\| {tag} \| [1-9]", out)


def test_dataset_sizes() -> None:
    root = EVALS_DIR / "datasets" / DATASET
    files = [p for p in root.rglob("*") if p.is_file()]
    assert sum(p.stat().st_size for p in files) <= 15 * 1024 * 1024
    assert max(p.stat().st_size for p in files) <= 500 * 1024


def _set_expected(key: str, value: object) -> Callable[[dict[str, object]], None]:
    def edit(data: dict[str, object]) -> None:
        expected = data["expected"]
        assert isinstance(expected, dict)
        expected[key] = value

    return edit


def _unknown_key(data: dict[str, object]) -> None:
    data["surprise"] = 1


@pytest.mark.parametrize(
    ("case_id", "edit", "rule"),
    [
        ("b047-kleidung", _set_expected("deductible_amount", "5.00"), "irrelevant_has_deductible"),
        ("b001-arbeitsmittel-notebook", _set_expected("category", "foo"), "enum"),
        ("b029-optiker-brille", _set_expected("gross_amount", 12.5), "money_format"),
        ("b030-physiotherapie", _unknown_key, "extra_forbidden"),
    ],
)
def test_invalid_labels_fail_with_case_id_and_rule(
    eval_paths: EvalPaths,
    capsys: pytest.CaptureFixture[str],
    case_id: str,
    edit: Callable[[dict[str, object]], None],
    rule: str,
) -> None:
    _edit_label(eval_paths, case_id, edit)
    code, out = _run(eval_paths, capsys)
    assert code == 1
    assert any(case_id in line and rule in line for line in out.splitlines()), out


def test_png_named_pdf_fails_magic_bytes(
    eval_paths: EvalPaths, capsys: pytest.CaptureFixture[str]
) -> None:
    case = _cases(eval_paths) / "b007-arbeitszimmer-einbauregal"
    png = (_cases(eval_paths) / "b018-haftpflicht" / "document.png").read_bytes()
    (case / "document.pdf").write_bytes(png)
    code, out = _run(eval_paths, capsys)
    assert code == 1
    assert any(
        "b007-arbeitszimmer-einbauregal" in line and "magic_bytes" in line
        for line in out.splitlines()
    )


def test_unknown_dataset_exits_2(eval_paths: EvalPaths, capsys: pytest.CaptureFixture[str]) -> None:
    assert validate.main(["--dataset", "nope_v9"], eval_paths) == 2


# --- privacy ----------------------------------------------------------------------------


def _valid_iban(rng: random.Random) -> str:
    bban = "".join(str(rng.randrange(10)) for _ in range(18))
    numeric = int("".join(str(int(ch, 36)) for ch in bban + "DE00"))
    check = 98 - numeric % 97
    return f"DE{check:02d}{bban}"


def test_privacy_scan_catches_steuer_id_in_pdf_text(
    eval_paths: EvalPaths, capsys: pytest.CaptureFixture[str]
) -> None:
    rng = random.SystemRandom()
    sentinel = str(rng.randrange(1, 10)) + "".join(str(rng.randrange(10)) for _ in range(10))
    w = Writer()
    w.line(f"Identifikationsnummer {sentinel}")
    case = _cases(eval_paths) / "b007-arbeitszimmer-einbauregal"
    (case / "document.pdf").write_bytes(w.finish())
    code, out = _run(eval_paths, capsys)
    assert code == 1
    assert "b007-arbeitszimmer-einbauregal: privacy rule steuer_id_shape" in out
    assert sentinel not in out


def test_privacy_scan_catches_valid_iban_in_label(
    eval_paths: EvalPaths, capsys: pytest.CaptureFixture[str]
) -> None:
    iban = _valid_iban(random.Random())
    _edit_label(eval_paths, "b047-kleidung", lambda d: d.__setitem__("notes", f"IBAN {iban}"))
    code, out = _run(eval_paths, capsys)
    assert code == 1
    assert "b047-kleidung: privacy rule valid_iban" in out
    assert iban not in out
    assert iban[4:] not in out


def test_privacy_scan_catches_exif(
    eval_paths: EvalPaths, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _cases(eval_paths) / "b002-arbeitsmittel-notebook-foto" / "document.jpg"
    with Image.open(path) as img:
        exif = Image.Exif()
        exif[0x010F] = "Musterkamera"  # Make
        buf = io.BytesIO()
        img.save(buf, format="JPEG", exif=exif.tobytes())
    path.write_bytes(buf.getvalue())
    code, out = _run(eval_paths, capsys)
    assert code == 1
    assert "b002-arbeitsmittel-notebook-foto: privacy rule image_exif" in out


def test_no_eleven_digit_numbers_in_tracked_eval_files() -> None:
    result = subprocess.run(
        [
            "git",
            "grep",
            "-nE",
            "(^|[^0-9])[0-9]{11}([^0-9]|$)",
            "--",
            "evals",
            "tests/evals",
        ],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1, "an 11-digit number is tracked under evals/ or tests/evals/"


def test_eval_case_has_no_label() -> None:
    ds = load_dataset(DATASET)
    case = ds.cases[0]
    assert isinstance(case, EvalCase)
    assert not hasattr(case, "expected")
    assert not hasattr(case, "label")
    assert set(vars(case)) == {"id", "path", "mime_type", "variant", "tags"}


def test_dataset_hash_changes_with_content(eval_paths: EvalPaths) -> None:
    root = eval_paths.datasets / DATASET
    before = dataset_hash(root)
    label = root / "cases" / "b047-kleidung" / "label.yaml"
    label.write_text(label.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert dataset_hash(root) != before


def _spaced(iban: str) -> str:
    return " ".join(iban[i : i + 4] for i in range(0, len(iban), 4))


@pytest.mark.parametrize(
    "template",
    [
        "IBAN {iban}",
        "IBAN {iban} BIC MUSTDEXXX",
        "IBAN {iban} 2025",
        "{iban} EUR",
        "Konto {iban} BIC ABCDEFGH, Musterbank",
        "iban {lower}",
        "IBAN {lower} bic mustdexxx",
    ],
)
@pytest.mark.parametrize("spaced", [False, True])
def test_scan_text_finds_valid_iban_in_context(template: str, spaced: bool) -> None:
    iban = _valid_iban(random.Random())
    shown = _spaced(iban) if spaced else iban
    text = template.format(iban=shown, lower=shown.lower())
    assert "valid_iban" in scan_text(text)


def test_scan_text_ignores_masked_and_invalid_iban() -> None:
    iban = _valid_iban(random.Random())
    broken = iban[:-1] + str((int(iban[-1]) + 1) % 10)
    assert "valid_iban" not in scan_text("IBAN DE00 XXXX XXXX XXXX XXXX 00 BIC MUSTDEXXX")
    assert "valid_iban" not in scan_text(f"IBAN {_spaced(broken)} BIC MUSTDEXXX")


def test_privacy_scan_catches_iban_bic_line_in_label_notes(
    eval_paths: EvalPaths, capsys: pytest.CaptureFixture[str]
) -> None:
    iban = _spaced(_valid_iban(random.Random()))
    _edit_label(
        eval_paths,
        "b001-arbeitsmittel-notebook",
        lambda d: d.__setitem__("notes", f"IBAN {iban} BIC MUSTDEXXX"),
    )
    code, out = _run(eval_paths, capsys)
    assert code == 1
    assert "b001-arbeitsmittel-notebook: privacy rule valid_iban" in out
    assert iban not in out


def test_privacy_scan_catches_iban_bic_line_in_pdf_text(
    eval_paths: EvalPaths, capsys: pytest.CaptureFixture[str]
) -> None:
    iban = _spaced(_valid_iban(random.Random()))
    w = Writer()
    w.line(f"Bankverbindung: IBAN {iban} BIC MUSTDEXXX")
    case = _cases(eval_paths) / "b007-arbeitszimmer-einbauregal"
    (case / "document.pdf").write_bytes(w.finish())
    code, out = _run(eval_paths, capsys)
    assert code == 1
    assert "b007-arbeitszimmer-einbauregal: privacy rule valid_iban" in out
    assert iban not in out


def test_privacy_scan_catches_iban_bic_line_in_recording(
    eval_paths: EvalPaths, capsys: pytest.CaptureFixture[str]
) -> None:
    iban = _spaced(_valid_iban(random.Random()))
    path = eval_paths.recordings / DATASET / "fixture-noisy" / "predictions.jsonl"
    lines = path.read_text().splitlines()
    lines[0] = lines[0].replace('"vendor": "', f'"vendor": "IBAN {iban} BIC MUSTDEXXX ', 1)
    path.write_text("\n".join(lines) + "\n")
    code, out = _run(eval_paths, capsys)
    assert code == 1
    assert "fixture-noisy/predictions.jsonl: privacy rule valid_iban" in out
    assert iban not in out
