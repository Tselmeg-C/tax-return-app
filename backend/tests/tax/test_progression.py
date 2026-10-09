# ruff: noqa: E501
"""§ 32b Progressionsvorbehalt (#86): reference cases, validation, params loading, BMF blocks."""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.domain.enums import DeductionNote, FilingStatus
from app.tax.deductions import ReturnContext
from app.tax.models import ProgressionParams
from app.tax.progression import LohnersatzInput, progression_amount, tariff_with_progression
from app.tax.tariff import income_tax
from app.tax_params import TaxParamsError, load_params, parse_params_file, supported_years

D = Decimal
HERE = Path(__file__).parent
PARAMS_DIR = Path(__file__).resolve().parents[2] / "app" / "tax" / "params"


def _filing(name: str) -> FilingStatus:
    return FilingStatus.JOINT if name == "joint" else FilingStatus.SINGLE


def _cases(year: int) -> list[dict[str, Any]]:
    path = HERE / "reference" / f"progressionsvorbehalt_{year}.yaml"
    assert path.is_file(), f"reference file missing: {path.name}"
    cases = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(cases, list) and cases
    return cases


def _params() -> list[Any]:
    return [pytest.param(y, c, id=f"{y}-{c['id']}") for y in supported_years() for c in _cases(y)]


@pytest.mark.parametrize(("year", "case"), _params())
def test_reference_case(year: int, case: dict[str, Any]) -> None:
    for key in ("id", "rule", "source", "arithmetic", "input", "expected"):
        assert case.get(key), f"{case.get('id')}: missing {key}"
    assert "§ 32b" in case["source"]
    p, inp = load_params(year), case["input"]
    filing = _filing(inp["filing"])
    if case["rule"] == "P":
        params = p.progressionsvorbehalt
        if "rate_decimals" in inp:
            params = ProgressionParams(source="t", rate_decimals=inp["rate_decimals"])
        got = tariff_with_progression(D(inp["zve"]), filing, D(inp["lohnersatz"]), p.tariff, params)
        assert got == D(case["expected"]), case["id"]
        if "plain" in case:
            assert income_tax(D(inp["zve"]), filing, p.tariff) == D(case["plain"])
            assert got != D(case["plain"])
    else:
        ctx = ReturnContext(year, filing, "A", "B" if filing is FilingStatus.JOINT else None)
        benefits = [
            LohnersatzInput(b["person"], D(b["amount"]), D(b["deducted"])) for b in inp["benefits"]
        ]
        result = progression_amount(ctx, benefits, p.werbungskosten.arbeitnehmer_pauschbetrag)
        assert result.total == D(case["expected"]), case["id"]
        assert [n.code.value for n in result.notes] == case.get("notes", [])


def test_every_year_has_reference_cases() -> None:
    for year in supported_years():
        ids = {c["id"] for c in _cases(year)}
        assert {f"U{n}" for n in range(1, 9)} <= {i.split("_")[0] for i in ids}


# --- named mutation guards -------------------------------------------------------------


def test_p3_exact_quotient_not_lost() -> None:
    """Dividing first / rounding the rate to 2 decimals gives 24 000 or 24 100, not 24 025."""
    p = load_params(2025)
    got = tariff_with_progression(
        100000, FilingStatus.JOINT, D(20000), p.tariff, p.progressionsvorbehalt
    )
    assert got == 24025
    two = ProgressionParams(source="t", rate_decimals="2")
    assert tariff_with_progression(100000, FilingStatus.JOINT, D(20000), p.tariff, two) != 24025


def test_base_is_zve_not_sum() -> None:
    p = load_params(2025)
    got = tariff_with_progression(
        40000, FilingStatus.SINGLE, D(15000), p.tariff, p.progressionsvorbehalt
    )
    assert got < income_tax(55000, FilingStatus.SINGLE, p.tariff)


def test_sum_is_floored_to_full_euros() -> None:
    """P13: 55 001,49 -> 55 001 (not 55 002, not exact cents)."""
    p = load_params(2025)
    cents = tariff_with_progression(
        D("40000.99"), FilingStatus.SINGLE, D("15000.50"), p.tariff, p.progressionsvorbehalt
    )
    assert cents == 9097
    just_under = tariff_with_progression(
        40000, FilingStatus.SINGLE, D("15000.99"), p.tariff, p.progressionsvorbehalt
    )
    assert just_under == tariff_with_progression(
        40000, FilingStatus.SINGLE, D(15000), p.tariff, p.progressionsvorbehalt
    )


def test_pauschbetrag_is_not_410_and_not_deducted_twice() -> None:
    ctx = ReturnContext(2025, FilingStatus.SINGLE, "A")
    pb = load_params(2025).werbungskosten.arbeitnehmer_pauschbetrag
    assert pb == 1230
    assert progression_amount(ctx, [LohnersatzInput("A", D(5000), D(0))], pb).total == 3770
    # U3: a person whose wages already used the Pauschbetrag gets no second deduction
    assert progression_amount(ctx, [LohnersatzInput("A", D(5000), D(1230))], pb).total == 5000
    two = [LohnersatzInput("A", D(3000), D(0)), LohnersatzInput("A", D(2000), D(0))]
    assert progression_amount(ctx, two, pb).total == 3770


def test_notes_carry_codes_and_ids_only() -> None:
    ctx = ReturnContext(2025, FilingStatus.SINGLE, "A")
    r = progression_amount(ctx, [LohnersatzInput("X", D("4000"), D(0))], D(1230))
    (note,) = r.notes
    assert note.code is DeductionNote.PERSON_NOT_IN_RETURN
    assert "4000" not in repr(note)


# --- input validation ------------------------------------------------------------------


def _tp() -> Any:
    p = load_params(2025)
    return p.tariff, p.progressionsvorbehalt


def test_plain_string_filing_is_rejected() -> None:
    t, pr = _tp()
    with pytest.raises(TypeError):
        tariff_with_progression(100000, "joint", D(0), t, pr)  # type: ignore[arg-type]


def test_joint_differs_from_single() -> None:
    t, pr = _tp()
    j = tariff_with_progression(100000, FilingStatus.JOINT, D(20000), t, pr)
    s = tariff_with_progression(100000, FilingStatus.SINGLE, D(20000), t, pr)
    assert j < s


@pytest.mark.parametrize("bad", [0.5, True, "1"])
def test_float_and_other_types_raise_type_error(bad: Any) -> None:
    t, pr = _tp()
    with pytest.raises(TypeError):
        tariff_with_progression(bad, FilingStatus.SINGLE, D(0), t, pr)
    with pytest.raises(TypeError):
        tariff_with_progression(1000, FilingStatus.SINGLE, bad, t, pr)


@pytest.mark.parametrize("bad", [D("NaN"), D("Infinity"), D(-1)])
def test_bad_lohnersatz_raises_value_error(bad: Decimal) -> None:
    t, pr = _tp()
    with pytest.raises(ValueError):  # noqa: PT011
        tariff_with_progression(1000, FilingStatus.SINGLE, bad, t, pr)


def test_dto_validation() -> None:
    with pytest.raises(TypeError):
        LohnersatzInput("A", 1.5, D(0))  # type: ignore[arg-type]
    with pytest.raises(ValueError):  # noqa: PT011
        LohnersatzInput("A", D(-1), D(0))


# --- params ----------------------------------------------------------------------------


def _broken(tmp_path: Path, old: str, new: str | None) -> Path:
    text = (PARAMS_DIR / "2025.yaml").read_text(encoding="utf-8")
    assert old in text
    path = tmp_path / "2025.yaml"
    path.write_text(text.replace(old, new or ""), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ('  rate_decimals: "exact"', None, "progressionsvorbehalt.rate_decimals: missing"),
        ('rate_decimals: "exact"', "rate_decimals: 4", "progressionsvorbehalt.rate_decimals: Input should be"),
        ('rate_decimals: "exact"', 'rate_decimals: "x"', "progressionsvorbehalt: Value error, rate_dec"),
        ('rate_decimals: "exact"', 'rate_decimals: "9"', "progressionsvorbehalt: Value error, rate_dec"),
        ('rate_decimals: "exact"', 'rate_decimals: "exact"\n  extra: "1"', "progressionsvorbehalt.extra: unknown key"),
    ],
)  # fmt: skip
def test_invalid_params_name_the_key(
    tmp_path: Path, old: str, new: str | None, message: str
) -> None:
    with pytest.raises(TaxParamsError, match=re.escape(message)):
        parse_params_file(_broken(tmp_path, old, new))


def test_params_values() -> None:
    for year in supported_years():
        s = load_params(year).progressionsvorbehalt
        assert (s.rate_decimals, s.provisional) == ("exact", True)
        assert "§ 32b Abs. 2 Nr. 1" in s.source and "BGBl." in s.source


# --- BMF building blocks ---------------------------------------------------------------


def _blocks(year: int) -> list[dict[str, Any]]:
    path = HERE / "golden" / f"blocks_{year}.yaml"
    assert path.is_file(), f"blocks file missing: {path.name}"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["year"] == year
    return list(data["blocks"])


def _block_params(only_missing: bool) -> list[Any]:
    return [
        pytest.param(y, b, id=f"{y}-{b['filing']}-{b['zve']}")
        for y in supported_years()
        for b in _blocks(y)
        if (b["bmf"] is None) is only_missing
    ]


@pytest.mark.parametrize(("year", "block"), _block_params(only_missing=False))
def test_block_matches_bmf(year: int, block: dict[str, Any]) -> None:
    got = income_tax(D(block["zve"]), _filing(block["filing"]), load_params(year).tariff)
    assert got == D(block["bmf"])  # tolerance 0


@pytest.mark.parametrize(("year", "block"), _block_params(only_missing=True))
@pytest.mark.xfail(strict=True, reason="blocks: pending, BMF value not collected yet (#86)")
def test_block_has_bmf_value(year: int, block: dict[str, Any]) -> None:
    assert block["bmf"] is not None


def test_blocks_cover_every_reference_zve() -> None:
    for year in supported_years():
        listed = {(D(b["zve"]), b["filing"]) for b in _blocks(year)}
        for case in _cases(year):
            if case["rule"] != "P":
                continue
            inp, f = case["input"], case["input"]["filing"]
            x = max(D(inp["zve"]).to_integral_value(rounding="ROUND_FLOOR"), D(0))
            if x == 0 or D(inp["lohnersatz"]) == 0:
                continue
            b = (x + D(inp["lohnersatz"])).to_integral_value(rounding="ROUND_FLOOR")
            assert (b, f) in listed, case["id"]
