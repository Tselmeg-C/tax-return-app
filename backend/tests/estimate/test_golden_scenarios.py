"""Golden service scenarios (#17): `build_summary` vs hand-derived chains and BMF tariff values.

Expected tariff values come from the BMF Einkommensteuerrechner (`bmf_inputs_2025.yaml`, collected
with `tests.estimate.collect_bmf`), never from the engine; the deduction chain is derived by hand
in `scenarios/golden_2025.yaml`, line by line with the paragraph.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tests.domain.conftest import keys  # noqa: F401 (autouse: throwaway Fernet key)
from tests.estimate.helpers import build, load_yaml

SCENARIOS: list[dict[str, Any]] = load_yaml("golden_2025.yaml")["scenarios"]
BMF: dict[str, dict[str, str]] = {
    c["id"]: c["expected"] for c in load_yaml("bmf_inputs_2025.yaml")["cases"]
}
# Scenario -> the BMF case that is the tariff ESt of the zvE the tariff is applied to.
TARIFF_CASE = {
    "s1_single_wage_commute": "s1_single_41158",
    "s2_joint_iii_v_church": "s2_joint_66280",
    "s2c_joint_one_member_nw_share_above_kirchgeld": "s2_joint_66280",
    "s7b_joint_member_without_income_kirchgeld": "s2_joint_66280",
    "s3a_joint_two_children_kindergeld": "s3a_joint_43843",
    "s3b_joint_two_children_freibetraege": "s3b_joint_186614",
    "s5_single_35a_soli_on_reduced_est": "s5_single_73838",
    "s5b_single_35a_capped_by_est": "s5b_single_13353",
    "s6_single_agb_spenden": "s6_single_22676",
    "s7_joint_spouse_person_vorsorge": "s2_joint_66280",
    "s7_single_parent_entlastungsbetrag": "s7_single_27174",
    "s8_single_wage_estimate": "s8_single_46789",
    "s9_single_above_bbg_manual_kv": "s9_single_85010",
}
IDS = [s["id"] for s in SCENARIOS]


def test_every_bmf_value_is_collected() -> None:
    assert all(v is not None for v in BMF.values())
    for scenario in SCENARIOS:
        assert all(name in BMF for name in scenario["bmf"]), scenario["id"]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=IDS)
async def test_summary_matches_hand_derivation(
    db_session: AsyncSession, scenario: dict[str, Any]
) -> None:
    b = await build(db_session, scenario)
    result = await b.summary()
    expected = scenario["expected"]
    assert result["status"] == "ok"
    for key in (
        "gde",
        "zve_before_children",
        "zve",
        "tarifliche_est",
        "ermaessigung_35a",
        "festzusetzende_est",
        "soli",
        "kirchensteuer",
        "kindergeld_added",
        "withheld",
        "refund",
    ):
        assert result[key] == expected[key], (scenario["id"], key)
    if "kirchensteuer_method" in expected:
        assert result["kirchensteuer_method"] == expected["kirchensteuer_method"]
    assert result["entlastungsbetrag"] == expected.get("entlastungsbetrag", "0.00")
    assert result["pflicht"]["state"] == expected["pflicht"]["state"]
    assert result["pflicht"]["reasons"] == expected["pflicht"]["reasons"]
    missing = [(m["code"], m["person_id"]) for m in result["what_missing"]]
    wanted = [(m["code"], b.ids[m["person"]]) for m in expected["what_missing"]]
    assert sorted(missing) == sorted(wanted)
    codes = {n["code"] for n in result["notes"]}
    assert set(expected.get("notes", [])) <= codes


@pytest.mark.parametrize("scenario", SCENARIOS, ids=IDS)
def test_hand_derivation_is_consistent_with_bmf(scenario: dict[str, Any]) -> None:
    """The literal expectations tie back to the calculator: tariff ESt, Soli and the refund."""
    e = scenario["expected"]
    est = Decimal(e["tarifliche_est"])
    case = TARIFF_CASE.get(scenario["id"])
    if case is not None:
        bmf = BMF[case]
        assert est - Decimal(e["kindergeld_added"]) == Decimal(bmf["est"])
        if Decimal(e["ermaessigung_35a"]) == 0:
            assert Decimal(e["soli"]) == Decimal(bmf["soli"])
    withheld = sum(Decimal(v) for v in e["withheld"].values())
    owed = Decimal(e["festzusetzende_est"]) + Decimal(e["soli"]) + Decimal(e["kirchensteuer"])
    assert Decimal(e["refund"]) == withheld - owed
    assert Decimal(e["festzusetzende_est"]) == est - Decimal(e["ermaessigung_35a"])


def test_progression_composition_from_bmf() -> None:
    """s4: besonderer Steuersatz (§ 32b Abs. 2 Nr. 1) = ESt(zvE + L) / (zvE + L)."""
    e = next(s for s in SCENARIOS if s["id"] == "s4_single_elterngeld")["expected"]
    zve, total = 23100, 31100
    rate_est = int(BMF["s4_single_31100"]["est"])
    assert int(Decimal(e["tarifliche_est"])) == zve * rate_est // total
    assert int(Decimal(e["tarifliche_est"])) > int(BMF["s4_single_23100"]["est"])


def test_kindergeld_vs_freibetrag_decision_from_bmf() -> None:
    """s3a / s3b: the Guenstigerpruefung compares the BMF ESt saving with 12 x 255 per child."""
    kindergeld = 12 * 255
    a = int(BMF["s3a_joint_43843"]["est"]) - int(BMF["s3a_joint_34243"]["est"])
    b1 = int(BMF["s3b_joint_205814"]["est"]) - int(BMF["s3b_joint_196214"]["est"])
    b2 = int(BMF["s3b_joint_196214"]["est"]) - int(BMF["s3b_joint_186614"]["est"])
    assert a < kindergeld < min(b1, b2)


def test_single_parent_decision_from_bmf() -> None:
    """s7: the BMF saving of 2 x 4.800 Freibetrag stays below 12 x 255 Kindergeld."""
    assert int(BMF["s7_single_27174"]["est"]) - int(BMF["s7_single_17574"]["est"]) < 12 * 255
