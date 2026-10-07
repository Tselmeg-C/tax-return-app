"""Params loader (`app.tax_params`): values, Decimal types, validation errors naming the key."""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.tax.models import STATES, TaxParams
from app.tax_params import (
    PARAMS_DIR,
    TaxParamsError,
    UnsupportedTaxYear,
    load_params,
    parse_params_file,
    supported_years,
)

D = Decimal


def test_supported_years() -> None:
    assert supported_years() == (2025, 2026)


@pytest.mark.parametrize(
    ("year", "gfb", "z2", "z3", "z4", "z5", "fg"),
    [
        (2025, "12096", ("17443", "932.30"), ("68480", "176.64", "1015.13"), "10911.92",
         "19246.67", "19950"),
        (2026, "12348", ("17799", "914.51"), ("69878", "173.10", "1034.87"), "11135.63",
         "19470.38", "20350"),
    ],
)  # fmt: skip
def test_values(
    year: int,
    gfb: str,
    z2: tuple[str, str],
    z3: tuple[str, str, str],
    z4: str,
    z5: str,
    fg: str,
) -> None:
    p = load_params(year)
    t = p.tariff
    assert t.grundfreibetrag == D(gfb)
    assert (t.zone2.upper, t.zone2.a, t.zone2.b) == (D(z2[0]), D(z2[1]), D(1400))
    assert (t.zone3.upper, t.zone3.a, t.zone3.b, t.zone3.c) == (
        D(z3[0]),
        D(z3[1]),
        D(2397),
        D(z3[2]),
    )
    assert (t.zone4.upper, t.zone4.rate, t.zone4.minus) == (D(277825), D("0.42"), D(z4))
    assert (t.zone5.rate, t.zone5.minus) == (D("0.45"), D(z5))
    assert (p.soli.rate, p.soli.milderung_rate) == (D("0.055"), D("0.119"))
    assert (p.soli.freigrenze_single, p.soli.freigrenze_joint) == (D(fg), 2 * D(fg))
    rates = p.church_tax.rate_by_state
    assert {s: r for s, r in rates.items() if r == D("0.08")} == {"BW": D("0.08"), "BY": D("0.08")}
    assert set(rates) == STATES


@pytest.mark.parametrize("year", [2025, 2026])
def test_money_fields_are_decimal(year: int) -> None:
    def walk(value: Any) -> list[Any]:
        if hasattr(value, "model_dump"):
            return walk(value.model_dump())
        if isinstance(value, dict):
            return [leaf for v in value.values() for leaf in walk(v)]
        return [value]

    p = load_params(year)
    for section in (p.tariff, p.soli, p.church_tax):
        leaves = [v for v in walk(section) if not isinstance(v, str | bool)]
        assert leaves and all(type(v) is Decimal for v in leaves)


def test_sections_cite_sources() -> None:
    for year in supported_years():
        p = load_params(year)
        for section in (p.tariff, p.soli, p.church_tax):
            assert "§" in section.source or "Kirchensteuer" in section.source
        assert "BGBl. 2024 I Nr. 449" in p.tariff.source
        assert "BGBl. 2024 I Nr. 449" in p.soli.source
        # church_tax: per-Land source and rounding not verified in #14 (see its `source`).
        assert p.provisional_sections == ("church_tax",)


def test_unsupported_year() -> None:
    with pytest.raises(UnsupportedTaxYear, match=r"2024.*2025, 2026"):
        load_params(2024)


def _broken(tmp_path: Path, edit: Any, name: str = "2025.yaml") -> Path:
    data = yaml.safe_load((PARAMS_DIR / "2025.yaml").read_text(encoding="utf-8"))
    edit(data)
    path = tmp_path / name
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def test_copy_of_real_file_loads(tmp_path: Path) -> None:
    assert parse_params_file(_broken(tmp_path, lambda d: None)) == load_params(2025)


def _set(path: str, value: Any) -> Any:
    def edit(data: dict[str, Any]) -> None:
        *parents, last = path.split(".")
        for key in parents:
            data = data[key]
        if value is _DELETE:
            del data[last]
        else:
            data[last] = value

    return edit


_DELETE = object()


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (_set("tariff.zone3.c", _DELETE), "2025.yaml: tariff.zone3.c: missing"),
        (_set("tariff.zone4.rate", 0.42), "tariff.zone4.rate: Value error, must be a quoted"),
        (_set("tariff.grundfreibetrag", 12096), "tariff.grundfreibetrag: Value error, must be a"),
        (_set("tariff.zone6", {"rate": "0.5"}), "tariff.zone6: unknown key"),
        (_set("church_tax.rate_by_state.BE", _DELETE), "church_tax: Value error, rate_by_state: "
                                                       "missing BE"),
        (_set("church_tax.rate_by_state.XX", "0.09"), "church_tax: Value error, rate_by_state: "
                                                      "unknown XX"),
        (_set("year", 2024), "2025.yaml: year: 2024 does not match file name"),
        (_set("tariff.zone2.upper", "69000"), "tariff: Value error, zone bounds must ascend"),
        (_set("soli.freigrenze_joint", "39901"), "soli: Value error, freigrenze_joint must be 2"),
        (_set("soli.rate", "5.5"), "soli: Value error, rate must be in (0, 1)"),
    ],
)  # fmt: skip
def test_invalid_file_names_the_key(tmp_path: Path, edit: Any, message: str) -> None:
    with pytest.raises(TaxParamsError, match=re.escape(message)):
        parse_params_file(_broken(tmp_path, edit))


def test_provisional_sections_listed(tmp_path: Path) -> None:
    p = parse_params_file(_broken(tmp_path, _set("tariff.provisional", True)))
    assert isinstance(p, TaxParams)
    assert p.provisional_sections == ("tariff", "church_tax")
