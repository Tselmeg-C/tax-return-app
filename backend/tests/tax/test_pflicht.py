"""Pflichtveranlagung (#92): reference cases (fail, never skip) and the `filing` params."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.domain.enums import (
    ALL_ENUMS,
    LABELS_DE,
    FilingStatus,
    PflichtCheck,
    PflichtReason,
    PflichtState,
    Steuerklasse,
)
from app.tax.deductions.models import ReturnContext
from app.tax.pflicht import PflichtEmployment, PflichtPerson, pflichtveranlagung
from app.tax_params import (
    PARAMS_DIR,
    TaxParamsError,
    load_params,
    parse_params_file,
    supported_years,
)

HERE = Path(__file__).parent / "reference"
REQUIRED = ("id", "source", "arithmetic", "input", "expected")


def _cases() -> list[Any]:
    out = []
    for year in supported_years():
        path = HERE / f"pflicht_{year}.yaml"
        assert path.is_file(), f"reference file missing: {path.name}"
        cases = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert len(cases) >= 15
        assert len({c["id"] for c in cases}) == len(cases)
        out += [pytest.param(year, c, id=f"{year}-{c['id']}") for c in cases]
    return out


def _run(year: int, inp: dict[str, Any]) -> Any:
    joint = inp["filing"] == "joint"
    ctx = ReturnContext(
        year, FilingStatus.JOINT if joint else FilingStatus.SINGLE, "p-A", "p-B" if joint else None
    )
    emps = [
        PflichtEmployment(
            f"p-{e['person']}",
            Steuerklasse(e["klasse"]),
            e.get("factor", False),
            e.get("concurrent", True),
        )
        for e in inp["employments"]
    ]
    people = [
        PflichtPerson(
            f"p-{x['person']}",
            Decimal(x["lohnersatz"]),
            None if x["other"] is None else Decimal(x["other"]),
        )
        for x in inp["persons"]
    ]
    return pflichtveranlagung(
        ctx,
        emps,
        people,
        date.fromisoformat(inp["today"]),
        load_params(year),
        advised=inp.get("advised", False),
    )


@pytest.mark.parametrize(("year", "case"), _cases())
def test_reference_case(year: int, case: dict[str, Any]) -> None:
    for key in REQUIRED:
        assert case.get(key) is not None, f"{case.get('id')}: missing {key}"
    assert "§" in case["source"]
    exp = case["expected"]
    r = _run(year, case["input"])
    assert r.state.value == exp["state"]
    assert [x.value for x in r.reasons] == exp["reasons"]
    assert r.deadline == date.fromisoformat(exp["deadline"])
    assert r.deadline_kind.value == exp["kind"]
    assert r.overdue is exp["overdue"]
    assert set(exp.get("not_checked_has", [])) <= {c.value for c in r.not_checked}


def test_always_not_checked_and_order() -> None:
    r = _run(2025, {"filing": "single", "today": "2026-01-01", "employments": [],
                    "persons": []})  # fmt: skip
    assert r.state is PflichtState.VOLUNTARY
    assert [c.value for c in r.not_checked] == [c.value for c in PflichtCheck]


def _persons() -> list[dict[str, Any]]:
    return [{"person": p, "lohnersatz": "0", "other": "0"} for p in ("A", "B")]


def test_lone_klasse_vi_is_multiple_employers() -> None:
    inp = {"filing": "single", "today": "2026-01-01", "persons": _persons()[:1]}
    inp["employments"] = [{"person": "A", "klasse": "6", "concurrent": False}]
    r = _run(2025, inp)
    assert r.state is PflichtState.MANDATORY
    assert PflichtReason.MULTIPLE_EMPLOYERS in r.reasons


def test_joint_with_klasse_vi_spouse_is_mandatory() -> None:
    inp = {"filing": "joint", "today": "2026-01-01", "persons": _persons()}
    inp["employments"] = [{"person": "A", "klasse": "3"}, {"person": "B", "klasse": "6"}]
    r = _run(2025, inp)
    assert r.state is PflichtState.MANDATORY
    assert PflichtReason.STEUERKLASSE_COMBINATION in r.reasons


def test_year_mismatch_and_float() -> None:
    ctx = ReturnContext(2025, FilingStatus.SINGLE, "p-A")
    with pytest.raises(ValueError, match="another year"):
        pflichtveranlagung(ctx, [], [], date(2026, 1, 1), load_params(2026))
    with pytest.raises(TypeError):
        PflichtPerson("p-A", 1.5, None)  # type: ignore[arg-type]


def test_labels_cover_new_enums() -> None:
    for enum in (PflichtState, PflichtReason, PflichtCheck):
        assert enum in ALL_ENUMS
        assert set(LABELS_DE[enum]) == set(enum)


@pytest.mark.parametrize("year", [2025, 2026])
def test_filing_values(year: int) -> None:
    f = load_params(year).filing
    assert f.deadline_self == date(year + 1, 7, 31)
    assert f.deadline_advised == date(year + 2, 2, 29 if (year + 2) % 4 == 0 else 28)
    assert "§ 149 Abs. 2" in f.source and "Abs. 3" in f.source and "BGBl" in f.source
    assert not f.provisional


def _broken(tmp_path: Path, edit: Any) -> Path:
    data = yaml.safe_load((PARAMS_DIR / "2025.yaml").read_text(encoding="utf-8"))
    edit(data)
    path = tmp_path / "2025.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (lambda d: d["filing"].pop("deadline_self"), "filing.deadline_self"),
        (lambda d: d["filing"].update(deadline_self=date(2026, 7, 31)), "filing.deadline_self"),
        (lambda d: d["filing"].update(deadline_advised="31.07.2026"), "filing.deadline_advised"),
        (
            lambda d: d["filing"].update(deadline_advised="2026-07-01"),
            "deadline_advised must be after",
        ),
        (lambda d: d["filing"].pop("source"), "filing.source"),
        (lambda d: d["filing"].update(surprise="1"), "filing.surprise"),
        (lambda d: d.pop("filing"), "filing: missing"),
    ],
)
def test_filing_invalid(tmp_path: Path, edit: Any, message: str) -> None:
    with pytest.raises(TaxParamsError) as exc:
        parse_params_file(_broken(tmp_path, edit))
    assert message in str(exc.value)
