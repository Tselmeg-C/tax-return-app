# ruff: noqa: E501
"""Kinder, Günstigerprüfung and `festsetzung` (#87): reference cases, issue figures, validation."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.domain.enums import FilingStatus
from app.tax.assessment import festsetzung
from app.tax.deductions import ChildInput, ReturnContext
from app.tax.kinder import guenstigerpruefung, kinder_amounts
from app.tax.progression import tariff_with_progression
from app.tax.tariff import income_tax
from app.tax_params import TaxParamsError, load_params, parse_params_file, supported_years

D = Decimal
HERE = Path(__file__).parent
PARAMS_DIR = Path(__file__).resolve().parents[2] / "app" / "tax" / "params"
REQUIRED = ("id", "rule", "source", "arithmetic", "input", "expected")


def _load(group: str, year: int) -> list[dict[str, Any]]:
    path = HERE / "reference" / f"{group}_{year}.yaml"
    assert path.is_file(), f"reference file missing: {path.name}"  # never skipped
    cases = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(cases, list) and cases
    for case in cases:
        for key in REQUIRED:
            assert case.get(key) is not None, f"{case.get('id')}: missing {key}"
        assert "§" in case["source"]
    assert len({c["id"] for c in cases}) == len(cases)
    return cases


def _child(raw: dict[str, Any]) -> ChildInput:
    return ChildInput(
        f"p-{raw['id']}",
        date.fromisoformat(raw["dob"]),
        raw["grade"],
        raw["months"],
        raw["half"],
        True,
    )


def _ctx(year: int, filing: str) -> ReturnContext:
    joint = filing == "joint"
    return ReturnContext(
        year, FilingStatus.JOINT if joint else FilingStatus.SINGLE, "p-A", "p-B" if joint else None
    )


def run_case(year: int, inp: dict[str, Any]) -> Any:
    return festsetzung(
        D(inp["zve"]),
        _ctx(year, inp["filing"]),
        inp.get("state"),
        D(inp.get("lohnersatz", "0")),
        [_child(c) for c in inp["children"]],
        D(inp.get("ermaessigung_35a", "0")),
        load_params(year),
    )


def _params(group: str) -> list[Any]:
    return [
        pytest.param(y, c, id=f"{y}-{c['id']}") for y in supported_years() for c in _load(group, y)
    ]


@pytest.mark.parametrize(("year", "case"), _params("festsetzung"))
def test_festsetzung_reference_case(year: int, case: dict[str, Any]) -> None:
    r = run_case(year, case["input"])
    e = case["expected"]
    assert [ln.person_id for ln in r.kinder.lines if ln.used_freibetrag] == [
        f"p-{k}" for k in e["used"]
    ]
    assert r.kinder.zve_tariff == D(e["zve_tariff"])
    assert r.kinder.zve_bmg == D(e["zve_bmg"])
    assert r.kindergeld_added == D(e["kindergeld_added"])
    assert r.tarifliche_est_without_kindergeld == D(e["tarifliche_est_without_kindergeld"])
    assert r.tarifliche_est == D(e["tarifliche_est"])
    assert r.ermaessigung_applied == D(e["ermaessigung_applied"])
    assert r.festzusetzende_est == D(e["festzusetzende_est"])
    assert r.bmg == D(e["bmg"])
    assert r.soli == D(e["soli"])
    assert r.kist == D(e["kist"])
    assert sorted(n.code.value for n in r.notes) == sorted(e["notes"])


@pytest.mark.parametrize(("year", "case"), _params("kinder"))
def test_kinder_amounts_reference_case(year: int, case: dict[str, Any]) -> None:
    inp = case["input"]
    child = ChildInput("p-K", date(2015, 5, 5), None, inp["months"], inp["half"], True)
    freibetrag, kindergeld = kinder_amounts(child, load_params(year).kinder)
    assert freibetrag == D(case["expected"]["freibetrag"])
    assert kindergeld == D(case["expected"]["kindergeld"])


def test_every_year_has_both_files_and_the_issue_cases() -> None:
    assert supported_years() == (2025, 2026)
    for year in supported_years():
        ids = {c["id"] for c in _load("festsetzung", year)}
        prefixes = {i.split("_")[0] for i in ids}
        assert prefixes >= {f"C{n}" for n in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 14, 15)}
        assert prefixes >= {f"C{n}" for n in (16, 17, 18, 19, 20)} | {"P10", "P11"}
        assert len(_load("kinder", year)) == 10


# The figures of the issue's table (PM hand computation, 2025): they pin the YAML against drift.
ISSUE_2025 = {
    "C1_single_half": ("22202", "20672", "85.91", "1653.76"),
    "C2_joint_2_children": ("21016", "14896", "0", "1340.64"),
    "C3_kindergeld_wins": ("3278", "1048", "0", "94.32"),
    "C4_five_months": ("21251", "19976", "0", "1797.84"),
    "C7_tie_single_above": ("8041", "6511", "0", "585.99"),
    "C7_tie_single_at": ("8042", "6512", "0", "586.08"),
    "C7_tie_joint_above": ("16082", "13022", "0", "1171.98"),
    "C7_tie_joint_at": ("16084", "13024", "0", "1172.16"),
    "C9_second_child_kindergeld": ("17828", "11778", "0", "1060.02"),
    "C10_small_second_child": ("17828", "14258", "0", "1283.22"),
    "C12_no_children": ("22688", "22688", "325.82", "1815.04"),
    "C15_joint_half": ("21230", "19700", "0", "1773.00"),
    "C16_progression": ("24016", "22486", "301.78", "1798.88"),
    "C20_high_income_soli": ("81232", "75112", "4131.16", "6008.96"),
}


@pytest.mark.parametrize("case_id", sorted(ISSUE_2025))
def test_issue_figures_2025(case_id: str) -> None:
    case = next(c for c in _load("festsetzung", 2025) if c["id"] == case_id)
    r = run_case(2025, case["input"])
    tar, bmg, soli, kist = ISSUE_2025[case_id]
    assert (r.tarifliche_est, r.bmg, r.soli, r.kist) == (D(tar), D(bmg), D(soli), D(kist))


def test_issue_figures_2026() -> None:
    by_id = {c["id"]: c for c in _load("festsetzung", 2026)}
    for cid, (tar, bmg) in {
        "C5_single_60000": ("13946", "12392"),
        "C2_joint_2_children": ("20788", "14572"),
        "C11_seven_months": ("14055.50", "13149"),
    }.items():
        r = run_case(2026, by_id[cid]["input"])
        assert (r.tarifliche_est, r.bmg) == (D(tar), D(bmg))


def test_issue_35a_and_pinned_progression_cases_2025() -> None:
    by_id = {c["id"]: c for c in _load("festsetzung", 2025)}
    c17 = run_case(2025, by_id["C17_35a_with_kindergeld"]["input"])
    assert (c17.festzusetzende_est, c17.bmg, c17.kist) == (D(2078), D(0), D(0))
    c18 = run_case(2025, by_id["C18_35a_progression"]["input"])
    assert (c18.festzusetzende_est, c18.soli) == (D(22816), D("158.98"))
    c19 = run_case(2025, by_id["C19_35a_exceeds"]["input"])
    assert (c19.ermaessigung_applied, c19.festzusetzende_est) == (D(0), D(0))
    p10 = run_case(2025, by_id["P10_with_35a"]["input"])
    assert (p10.tarifliche_est, p10.festzusetzende_est) == (D(9097), D(7897))  # not 7320 - 1200
    p11 = run_case(2025, by_id["P11_35a_exceeds"]["input"])
    assert (p11.tarifliche_est, p11.ermaessigung_applied, p11.festzusetzende_est) == (
        D(642),
        D(642),
        D(0),
    )  # 358 lost


def test_35a_cap_includes_the_kindergeld_addition() -> None:
    """C1 (2025): tarifliche ESt 20 672 + 1 530 = 22 202. Ermäßigung 21 000 > 20 672 but < 22 202:
    fully applied (cap incl. Kindergeld, provisional reading), festzusetzende ESt 1 202; the
    Bemessungsgrundlage max(20 672 - 21 000, 0) = 0."""
    case = next(c for c in _load("festsetzung", 2025) if c["id"] == "C1_single_half")
    r = run_case(2025, {**case["input"], "ermaessigung_35a": "21000"})
    assert (r.ermaessigung_applied, r.festzusetzende_est, r.bmg, r.soli, r.kist) == (
        D(21000),
        D(1202),
        D(0),
        D(0),
        D(0),
    )


def test_c13_sequential_differs_from_all_together_and_none() -> None:
    """C9: sequential 17 828; both Freibeträge 11 778 + 6 120 = 17 898; none 17 922."""
    case = next(c for c in _load("festsetzung", 2025) if c["id"] == "C9_second_child_kindergeld")
    r = run_case(2025, case["input"])
    assert r.tarifliche_est == D(17828)
    tariff = load_params(2025).tariff
    assert income_tax(D(70800), FilingStatus.JOINT, tariff) + 6120 == D(17898) != r.tarifliche_est
    assert income_tax(D(90000), FilingStatus.JOINT, tariff) == D(17922)


def test_permuting_children_changes_nothing() -> None:
    case = next(c for c in _load("festsetzung", 2025) if c["id"] == "C10_small_second_child")
    inp = case["input"]
    first = run_case(2025, inp)
    swapped = run_case(2025, {**inp, "children": list(reversed(inp["children"]))})
    assert swapped == first


# --- Soli on the Bemessungsgrundlage ---------------------------------------------------------


@pytest.mark.parametrize(
    ("year", "filing", "zve", "freigrenze"),
    [
        (2025, "single", "80000", 19950),
        (2025, "joint", "250000", 39900),
        (2026, "single", "80000", 20350),
        (2026, "joint", "250000", 40700),
    ],
)
def test_soli_edge_is_decided_on_the_bmg_not_the_tax(
    year: int, filing: str, zve: str, freigrenze: int
) -> None:
    """Ermäßigung 35a moves the BMG onto / one euro above the Freigrenze; the tax (with the
    Kindergeld addition) stays far above it, so only the BMG can decide."""
    p = load_params(year)
    ctx = _ctx(year, filing)
    kids = [_kid(half=filing == "single")]
    base = festsetzung(D(zve), ctx, None, D(0), kids, D(0), p)
    assert base.bmg > freigrenze + 1
    at = festsetzung(D(zve), ctx, None, D(0), kids, base.bmg - freigrenze, p)
    above = festsetzung(D(zve), ctx, None, D(0), kids, base.bmg - freigrenze - 1, p)
    assert (at.bmg, at.soli) == (D(freigrenze), D(0))
    assert above.bmg == D(freigrenze + 1) and above.soli == D("0.11")  # 0.119 x 1, down to cents
    assert at.tarifliche_est > freigrenze and at.festzusetzende_est > freigrenze


# --- validation ------------------------------------------------------------------------------


def _fest(**kw: Any) -> Any:
    args: dict[str, Any] = {
        "zve": D(50000),
        "ctx": _ctx(2025, "single"),
        "state": "BY",
        "lohn": D(0),
        "children": [],
        "erm": D(0),
        "params": load_params(2025),
    }
    args.update(kw)
    return festsetzung(
        args["zve"],
        args["ctx"],
        args["state"],
        args["lohn"],
        args["children"],
        args["erm"],
        args["params"],
    )


def _kid(pid: str = "p-K1", **kw: Any) -> ChildInput:
    args: dict[str, Any] = {"dob": date(2015, 5, 5), "grade": None, "months": 12, "half": True}
    args.update(kw)
    return ChildInput(pid, args["dob"], args["grade"], args["months"], args["half"], True)


def test_rejects_float_nan_negative() -> None:
    with pytest.raises(TypeError):
        _fest(zve=50000.0)
    with pytest.raises(TypeError):
        _fest(lohn=1.5)
    with pytest.raises(TypeError):
        _fest(erm=100.0)
    for bad in (D("NaN"), D("Infinity")):
        for key in ("zve", "lohn", "erm"):
            with pytest.raises(ValueError, match="finite"):
                _fest(**{key: bad})
    with pytest.raises(ValueError, match="lohnersatz"):
        _fest(lohn=D(-1))
    with pytest.raises(ValueError, match="ermaessigung_35a"):
        _fest(erm=D(-1))


def test_negative_zve_is_a_loss_not_an_error() -> None:
    r = _fest(zve=D(-100), children=[_kid()])
    assert (r.tarifliche_est, r.festzusetzende_est, r.bmg, r.kinder.zve_bmg) == (D(0),) * 4


def test_duplicate_children_and_bad_inputs() -> None:
    with pytest.raises(ValueError, match="same person_id"):
        _fest(children=[_kid(), _kid()])
    with pytest.raises(TypeError):
        _fest(children=["x"])
    with pytest.raises(ValueError, match="born after"):
        _fest(children=[_kid(dob=date(2026, 1, 1))])
    assert _fest(children=[_kid(dob=date(2026, 1, 1), months=0)]).kinder.lines == ()
    with pytest.raises(ValueError, match="differ"):
        _fest(params=load_params(2026))
    with pytest.raises(TypeError):
        _fest(ctx="single")


def test_filing_must_be_a_member_not_a_string() -> None:
    p = load_params(2025)
    with pytest.raises(TypeError, match="FilingStatus"):
        guenstigerpruefung(D(1), [], "joint", lambda z: z, p.kinder, year=2025)  # type: ignore[arg-type]


def test_unknown_state_is_rejected_by_church_tax() -> None:
    with pytest.raises(ValueError, match="unknown state"):
        _fest(zve=D(80000), state="XX")


def test_notes_carry_codes_and_ids_only() -> None:
    r = _fest(children=[_kid(dob=date(1990, 3, 3))])
    assert [(n.code.value, n.person_id, n.item_id) for n in r.notes] == [
        ("child_age_review", "p-K1", None)
    ]
    assert "1990" not in repr(r.notes)


def test_lohnersatz_zero_equals_plain_tariff() -> None:
    p = load_params(2025)
    for zve in (D(30000), D(80000)):
        assert tariff_with_progression(
            zve, FilingStatus.SINGLE, D(0), p.tariff, p.progressionsvorbehalt
        ) == income_tax(zve, FilingStatus.SINGLE, p.tariff)
        assert _fest(zve=zve).tarifliche_est == income_tax(zve, FilingStatus.SINGLE, p.tariff)


def test_in_household_changes_nothing() -> None:
    a = _fest(zve=D(80000), children=[_kid()])
    kid = ChildInput("p-K1", date(2015, 5, 5), None, 12, True, False)
    assert _fest(zve=D(80000), children=[kid]) == a


def test_chain_composes_the_deduction_functions() -> None:
    """werbungskosten -> Sonderausgaben (+ Vorsorge) -> agB stub -> festsetzung runs end to end."""
    from app.tax.deductions import (
        EmploymentInput,
        VorsorgeInput,
        sonderausgaben,
        vorsorge,
        werbungskosten,
    )

    year, p = 2025, load_params(2025)
    ctx = _ctx(year, "single")
    wk = werbungskosten(ctx, [], [EmploymentInput("p-A", None, 0, 0)], p.werbungskosten)
    einkuenfte = D(60000) - wk.total_applied
    gde = einkuenfte  # Entlastungsbetrag (#81) = 0
    so = sonderausgaben(ctx, [], [], gde, D(0), p.sonderausgaben)
    vs = vorsorge(
        ctx, [VorsorgeInput("p-A", True, D(5000), None, D(0), D(3000), D(500), D(0))], p.vorsorge
    )
    agb = D(0)  # stub until #76 lands: a plain number
    zve = max(gde - so.applied - vs.total - agb, D(0))
    r = festsetzung(zve, ctx, "BY", D(0), [_kid()], D(0), p)
    assert (
        r.festzusetzende_est <= income_tax(zve, FilingStatus.SINGLE, p.tariff) + r.kindergeld_added
    )
    assert r.kinder.zve_bmg == max(zve - D(4800), D(0))


# --- params ---------------------------------------------------------------------------------


@pytest.mark.parametrize(("year", "kfb", "kg"), [(2025, "3336", "255"), (2026, "3414", "259")])
def test_kinder_values(year: int, kfb: str, kg: str) -> None:
    k = load_params(year).kinder
    assert (k.kinderfreibetrag, k.bea_freibetrag, k.kindergeld_per_month) == (
        D(kfb),
        D(1464),
        D(kg),
    )
    assert "§ 32 Abs. 6" in k.source and "BGBl. 2024 I Nr. 449" in k.source and "§ 66" in k.source
    assert "UNCONFIRMED" in k.source and k.provisional
    assert (k.kinderfreibetrag + k.bea_freibetrag) * 2 == D(9600 if year == 2025 else 9756)


def _broken(tmp_path: Path, edit: Any) -> Path:
    data = yaml.safe_load((PARAMS_DIR / "2025.yaml").read_text(encoding="utf-8"))
    edit(data["kinder"])
    path = tmp_path / "2025.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (lambda k: k.pop("kindergeld_per_month"), "kinder.kindergeld_per_month: missing"),
        (
            lambda k: k.update(kinderfreibetrag=3336),
            "kinder.kinderfreibetrag: Value error, must be a quoted",
        ),
        (lambda k: k.update(extra="1"), "kinder.extra: unknown key"),
        (
            lambda k: k.update(bea_freibetrag="0"),
            "kinder: Value error, bea_freibetrag must be positive",
        ),
        (lambda k: k.pop("source"), "kinder.source: missing"),
    ],
)
def test_kinder_section_validation_names_the_key(tmp_path: Path, edit: Any, message: str) -> None:
    with pytest.raises(TaxParamsError, match=re.escape(message)):
        parse_params_file(_broken(tmp_path, edit))
