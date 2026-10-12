"""haushaltsnahe params (#77): values and the loader errors naming the key."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.tax_params import PARAMS_DIR, TaxParamsError, load_params, parse_params_file

D = Decimal


def _broken(tmp_path: Path, edit: Any) -> Path:
    data = yaml.safe_load((PARAMS_DIR / "2025.yaml").read_text(encoding="utf-8"))
    edit(data["haushaltsnahe"])
    path = tmp_path / "2025.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


@pytest.mark.parametrize("year", [2025, 2026])
def test_values(year: int) -> None:
    h = load_params(year).haushaltsnahe
    assert (h.rate, h.max_dienstleistung, h.max_handwerker) == (D("0.20"), D(4000), D(1200))
    assert "§ 35a Abs. 2" in h.source and "Abs. 3" in h.source and "BGBl" in h.source
    assert not h.provisional  # read in the law text (buzer.de), see the PR table


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (lambda h: h.pop("max_handwerker"), "haushaltsnahe.max_handwerker: missing"),
        (lambda h: h.update(rate=0.20), "haushaltsnahe.rate"),
        (lambda h: h.update(rate="1.2"), "rate must be in (0, 1)"),
        (lambda h: h.update(max_dienstleistung="0"), "must be positive"),
        (lambda h: h.pop("source"), "haushaltsnahe.source: missing"),
        (lambda h: h.update(surprise="1"), "haushaltsnahe.surprise: unknown key"),
    ],
)
def test_invalid(tmp_path: Path, edit: Any, message: str) -> None:
    with pytest.raises(TaxParamsError) as exc:
        parse_params_file(_broken(tmp_path, edit))
    assert message in str(exc.value)


def test_missing_section(tmp_path: Path) -> None:
    data = yaml.safe_load((PARAMS_DIR / "2025.yaml").read_text(encoding="utf-8"))
    del data["haushaltsnahe"]
    path = tmp_path / "2025.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    with pytest.raises(TaxParamsError, match="haushaltsnahe: missing"):
        parse_params_file(path)
