"""Edge cases of `build_summary` (#17): status, selection rules, Vorsorge, caching, scope."""

from __future__ import annotations

import copy
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import app.estimate.service as service
from app.db.models import Employment, Estimate, Lohnersatz
from app.db.scope import HouseholdScope
from app.domain.enums import AttentionReason, FilingStatus
from app.estimate.service import build_summary
from app.tax_params import UnsupportedTaxYear
from tests.domain.conftest import keys  # noqa: F401 (autouse: throwaway Fernet key)
from tests.domain.factories import make_household
from tests.estimate.helpers import (
    TODAY,
    add_item,
    build,
    load_yaml,
    needs_attention_document,
)

SCENARIOS = {s["id"]: s for s in load_yaml("golden_2025.yaml")["scenarios"]}
SINGLE = SCENARIOS["s1_single_wage_commute"]
JOINT = SCENARIOS["s2_joint_iii_v_church"]
AMOUNTS = ("refund", "zve", "festzusetzende_est", "soli", "kirchensteuer")


def pick(result: dict[str, Any]) -> dict[str, Any]:
    return {k: result[k] for k in AMOUNTS}


async def estimate_count(session: AsyncSession) -> int:
    return int((await session.execute(select(func.count()).select_from(Estimate))).scalar_one())


async def test_no_profile_is_a_status_not_an_error(db_session: AsyncSession) -> None:
    hh = await make_household(db_session)
    result = await build_summary(HouseholdScope(db_session, hh.id), 2025, TODAY)
    assert result["status"] == "no_profile"
    assert result["refund"] is None and result["pflicht"] is None and result["withheld"] is None
    assert result["what_missing"] == [] and result["attention_count"] == 0
    assert result["params_version"].startswith("2025-")
    assert await estimate_count(db_session) == 0


async def test_unsupported_year_raises(db_session: AsyncSession) -> None:
    hh = await make_household(db_session)
    with pytest.raises(UnsupportedTaxYear):
        await build_summary(HouseholdScope(db_session, hh.id), 1999, TODAY)


async def test_filing_status_reaches_the_engine_as_enum(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[Any] = []
    real = service.festsetzung

    def spy(zve: Any, ctx: Any, *args: Any) -> Any:
        seen.append(ctx.filing)
        return real(zve, ctx, *args)

    monkeypatch.setattr(service, "festsetzung", spy)
    b = await build(db_session, JOINT)
    await b.summary()
    assert seen == [FilingStatus.JOINT] and seen[0] is FilingStatus.JOINT


async def test_item_of_a_person_outside_the_return_is_ignored(db_session: AsyncSession) -> None:
    spec = copy.deepcopy(SINGLE)
    spec["persons"]["x"] = {"kind": "adult"}
    b = await build(db_session, spec)
    base = pick(await b.summary())
    await add_item(b, {"category": "spenden", "deductible": "5000.00", "person": "x"})
    await add_item(b, {"category": "wk_arbeitsmittel", "deductible": "9000.00", "person": "x"})
    result = await b.summary()
    assert pick(result) == base
    assert "person_not_in_return" in {n["code"] for n in result["notes"]}


async def test_employment_outside_the_return_is_ignored(db_session: AsyncSession) -> None:
    spec = copy.deepcopy(SINGLE)
    spec["persons"]["x"] = {"kind": "adult"}
    b = await build(db_session, spec)
    base = await b.summary()
    row = Employment(
        household_id=b.household.id,
        person_id=__import__("uuid").UUID(b.ids["x"]),
        year=2025,
        employer_name="Muster AG",
        steuerklasse=__import__("app.domain.enums", fromlist=["Steuerklasse"]).Steuerklasse.I,
        gross_wage=Decimal("99999.00"),
        lohnsteuer=Decimal("1.00"),
    )
    db_session.add(row)
    await db_session.flush()
    assert pick(await b.summary()) == pick(base)


async def test_overridden_item_is_kept_unreviewed_amount_item_is_excluded(
    db_session: AsyncSession,
) -> None:
    spec = copy.deepcopy(SINGLE)
    spec["employments"][0].update({"commute_km": 0, "office_days": 0, "homeoffice_days": 0})
    b = await build(db_session, spec)
    base = pick(await b.summary())
    doc = await needs_attention_document(b, AttentionReason.IMPLAUSIBLE_AMOUNT)
    item = {"category": "wk_arbeitsmittel", "deductible": "3000.00", "person": "a"}
    excluded = await add_item(b, item, document_id=doc.id)
    result = await b.summary()
    assert pick(result) == base  # not counted ...
    assert result["attention_count"] == 1
    assert [n for n in result["notes"] if n["code"] == "excluded_attention"] == [
        {"code": "excluded_attention", "item_id": str(excluded.id), "person_id": b.ids["a"]}
    ]
    excluded.overridden_by_user = True  # ... until the user confirms it
    await db_session.flush()
    kept = await b.summary()
    assert Decimal(kept["zve"]) == Decimal(base["zve"]) - (3000 - 1230)
    assert "excluded_attention" not in {n["code"] for n in kept["notes"]}


async def test_unreviewed_item_counts_with_a_note(db_session: AsyncSession) -> None:
    b = await build(db_session, SINGLE)
    doc = await needs_attention_document(b, AttentionReason.LOW_CONFIDENCE)
    item = await add_item(
        b,
        {"category": "wk_arbeitsmittel", "deductible": "3000.00", "person": "a"},
        document_id=doc.id,
    )
    result = await b.summary()
    assert any(
        n["code"] == "included_unreviewed" and n["item_id"] == str(item.id) for n in result["notes"]
    )


async def test_vorsorge_field_and_items_never_both(db_session: AsyncSession) -> None:
    b = await build(db_session, SINGLE)  # rv_employee / kv / pv set on the employment
    base = pick(await b.summary())
    for category, amount in (
        ("vorsorge_rv", "4836.00"),
        ("vorsorge_kv_pv", "4900.00"),
    ):
        await add_item(b, {"category": category, "deductible": amount, "person": "a"})
    result = await b.summary()
    assert pick(result) == base
    assert "kv_pv_unsplit" not in {n["code"] for n in result["notes"]}  # manual KV / PV wins
    assert all(m["code"] != "vorsorge_missing" for m in result["what_missing"])


async def test_vorsorge_from_items_when_no_field_is_set(db_session: AsyncSession) -> None:
    spec = copy.deepcopy(SINGLE)
    for key in ("rv_employee", "kv", "pv"):
        del spec["employments"][0][key]
    spec["employments"][0]["gross_wage"] = "20000.00"  # Uebergangsbereich: no estimate (#85)
    b = await build(db_session, spec)
    without = await b.summary()
    assert [m["code"] for m in without["what_missing"]] == ["vorsorge_missing"]
    assert without["vorsorge_estimated"] == []
    await add_item(b, {"category": "vorsorge_rv", "deductible": "4836.00", "person": "a"})
    with_items = await b.summary()
    assert with_items["what_missing"] == []
    assert Decimal(with_items["zve"]) < Decimal(without["zve"])


async def test_pauschbetrag_is_limited_by_the_wage_but_real_costs_are_not(
    db_session: AsyncSession,
) -> None:
    low = copy.deepcopy(SINGLE)
    low["employments"][0]["gross_wage"] = "800.00"  # costs 540 < Pauschbetrag 1230 > wage
    assert (await (await build(db_session, low)).summary())["gde"] == "0.00"  # § 9a, not negative


async def test_real_costs_above_the_wage_give_negative_einkuenfte(
    db_session: AsyncSession,
) -> None:
    high = copy.deepcopy(SINGLE)
    emp = high["employments"][0]
    emp.update(gross_wage="5000.00", commute_km=100, office_days=200, homeoffice_days=0)
    assert Decimal((await (await build(db_session, high)).summary())["gde"]) < 0


async def test_employment_without_wage_lists_no_lstb(db_session: AsyncSession) -> None:
    spec = copy.deepcopy(SINGLE)
    del spec["employments"][0]["gross_wage"]
    b = await build(db_session, spec)
    result = await b.summary()
    assert {"code": "no_lstb", "person_id": b.ids["a"]} in result["what_missing"]
    assert result["status"] == "ok"


async def test_kap_and_v_items_are_listed_as_not_supported(db_session: AsyncSession) -> None:
    b = await build(db_session, SINGLE)
    base = pick(await b.summary())
    await add_item(b, {"category": "kapital_bescheinigung", "deductible": "800.00"})
    await add_item(b, {"category": "v_afa", "deductible": "1200.00"})
    result = await b.summary()
    assert pick(result) == base
    assert {"code": "kap_v_not_supported", "person_id": None} in result["what_missing"]


async def test_household_level_item_in_a_joint_return_is_unassigned(
    db_session: AsyncSession,
) -> None:
    b = await build(db_session, JOINT)
    await add_item(b, {"category": "wk_arbeitsmittel", "deductible": "500.00"})
    result = await b.summary()
    assert {"code": "person_unassigned", "person_id": None} in result["what_missing"]


@pytest.mark.parametrize(("amount", "state"), [("410.00", "voluntary"), ("410.01", "mandatory")])
async def test_lohnersatz_limit_decides_the_pflicht(
    db_session: AsyncSession, amount: str, state: str
) -> None:
    spec = copy.deepcopy(SINGLE)
    spec["lohnersatz"] = [{"person": "a", "amount": amount}]
    b = await build(db_session, spec)
    assert (await b.summary())["pflicht"]["state"] == state


async def test_employment_with_explicit_zero_wage_is_not_a_wage_earner(
    db_session: AsyncSession,
) -> None:
    spec = copy.deepcopy(JOINT)
    spec["employments"][1]["gross_wage"] = "0.00"  # B: Steuerklasse V but no wages
    b = await build(db_session, spec)
    assert (await b.summary())["pflicht"]["state"] == "voluntary"


async def test_deadline_is_shifted_at_display_time_only(db_session: AsyncSession) -> None:
    spec = copy.deepcopy(SINGLE)
    spec["lohnersatz"] = [{"person": "a", "amount": "1000.00"}]  # mandatory: self deadline
    b = await build(db_session, spec, year=2026)
    result = await b.summary(date(2027, 8, 1))
    stored = (await db_session.execute(select(Estimate))).scalar_one().result
    assert result["pflicht"]["deadline"] == "2027-08-02"  # 2027-07-31 is a Saturday
    assert result["pflicht"]["overdue"] is False
    assert result["pflicht"]["deadline_kind"] == "self"
    assert stored["pflicht"]["deadline_statutory"] == "2027-07-31"
    assert "overdue" not in stored["pflicht"] and "deadline" not in stored["pflicht"]
    assert (await b.summary(date(2027, 8, 3)))["pflicht"]["overdue"] is True


async def test_estimate_row_is_not_duplicated_and_follows_the_inputs(
    db_session: AsyncSession,
) -> None:
    b = await build(db_session, SINGLE)
    first = await b.summary()
    again = await b.summary(date(2026, 6, 1))
    assert first["refund"] == again["refund"]
    assert await estimate_count(db_session) == 1
    row = (await db_session.execute(select(Estimate))).scalar_one()
    assert row.params_version == first["params_version"] and len(row.inputs_hash) == 64
    employment = (await db_session.execute(select(Employment))).scalar_one()
    employment.lohnsteuer = Decimal("9801.00")
    await db_session.flush()
    changed = await b.summary()
    assert changed["refund"] == "2108.00"
    assert await estimate_count(db_session) == 2
    hashes = {r.inputs_hash for r in (await db_session.execute(select(Estimate))).scalars()}
    assert len(hashes) == 2


@pytest.mark.parametrize(
    "change",
    [
        lambda b, spec: add_item(b, {"category": "spenden", "deductible": "10.00"}),
        lambda b, spec: _set(Employment, "gross_wage", Decimal("52001.00")),
        lambda b, spec: _set(Employment, "commute_km", 11),
        lambda b, spec: _set(Employment, "kv", Decimal("4001.00")),
        lambda b, spec: _lohnersatz(b),
    ],
    ids=["item", "wage", "commute", "kv", "lohnersatz"],
)
async def test_inputs_hash_changes_on_any_input(db_session: AsyncSession, change: Any) -> None:
    b = await build(db_session, SINGLE)
    await b.summary()
    await b.summary()
    assert await estimate_count(db_session) == 1
    action = change(b, SINGLE)
    if hasattr(action, "__await__"):
        await action
    else:
        model, field, value = action
        row = (await db_session.execute(select(model))).scalars().first()
        setattr(row, field, value)
    await db_session.flush()
    await b.summary()
    assert await estimate_count(db_session) == 2


def _set(model: Any, field: str, value: Any) -> tuple[Any, str, Any]:
    return model, field, value


async def _lohnersatz(b: Any) -> None:
    b.session.add(
        Lohnersatz(
            household_id=b.household.id,
            person_id=__import__("uuid").UUID(b.ids["a"]),
            year=2025,
            amount=Decimal("100.00"),
        )
    )


async def test_other_household_sees_nothing(db_session: AsyncSession) -> None:
    b = await build(db_session, JOINT)
    await b.summary()
    other = await make_household(db_session, "Anderer Haushalt")
    result = await build_summary(HouseholdScope(db_session, other.id), 2025, TODAY)
    assert result["status"] == "no_profile"
    assert await estimate_count(db_session) == 1


async def test_church_state_only_when_a_member_is_in_the_return(
    db_session: AsyncSession,
) -> None:
    spec = copy.deepcopy(SINGLE)
    spec["persons"]["a"]["religion"] = "rk"
    b = await build(db_session, spec)
    assert (await b.summary())["kirchensteuer"] == "692.37"  # 9 % Berlin of 7.693


async def test_person_vorsorge_wins_per_group_and_is_never_summed(
    db_session: AsyncSession,
) -> None:
    # Reference: the employment holds KV 3000 and no PV; the person row must give the same.
    ref = copy.deepcopy(SINGLE)
    ref["employments"][0].update(kv="3000.00")
    del ref["employments"][0]["pv"]
    base = pick(await (await build(db_session, ref)).summary())

    spec = copy.deepcopy(SINGLE)  # employment: rv 4836, kv 4000, pv 900
    spec["person_vorsorge"] = [{"person": "a", "kv": "3000.00"}]
    b = await build(db_session, spec)
    await add_item(b, {"category": "vorsorge_kv_pv", "deductible": "4900.00", "person": "a"})
    result = await b.summary()
    assert pick(result) == base  # person KV replaces employment KV + PV and the item
    assert "kv_pv_unsplit" not in {n["code"] for n in result["notes"]}


async def test_person_vorsorge_leaves_other_groups_to_employment(
    db_session: AsyncSession,
) -> None:
    spec = copy.deepcopy(SINGLE)
    base = await (await build(db_session, spec)).summary()
    spec["person_vorsorge"] = [{"person": "a", "basisrente": "1000.00"}]
    result = await (await build(db_session, spec)).summary()
    # rv / kv / pv still from the employment; the Basisrente is added on top (group-wise)
    assert Decimal(result["zve"]) < Decimal(base["zve"])


async def test_person_vorsorge_zero_counts_as_entered_but_stays_missing(
    db_session: AsyncSession,
) -> None:
    spec = copy.deepcopy(JOINT)
    spec["employments"] = spec["employments"][:1]  # b has no employment
    missing = await (await build(db_session, spec)).summary()
    assert [m["code"] for m in missing["what_missing"]] == ["vorsorge_missing"]
    spec["person_vorsorge"] = [{"person": "b", "kv": "0.00"}]
    zero = await (await build(db_session, spec)).summary()
    assert [m["code"] for m in zero["what_missing"]] == ["vorsorge_missing"]
    spec["person_vorsorge"] = [{"person": "b", "kv": "1200.00"}]
    entered = await (await build(db_session, spec)).summary()
    assert entered["what_missing"] == []
    assert Decimal(entered["zve"]) < Decimal(missing["zve"])


async def test_shares_household_removes_the_entlastungsbetrag(db_session: AsyncSession) -> None:
    from app.db.models import TaxProfile

    b = await build(db_session, SCENARIOS["s7_single_parent_entlastungsbetrag"])
    assert (await b.summary())["entlastungsbetrag"] == "4260.00"
    profile = (await db_session.execute(b.scope.select(TaxProfile))).scalar_one()
    profile.shares_household_with_adult = True
    await db_session.flush()
    result = await b.summary()
    assert (result["entlastungsbetrag"], result["gde"]) == ("0.00", "38770.00")
    assert "entlastung_household_shared" in {n["code"] for n in result["notes"]}


def _wage_only(wage: str = "60000.00") -> dict[str, Any]:
    spec = copy.deepcopy(SINGLE)
    spec["employments"][0] = {
        k: v for k, v in spec["employments"][0].items() if k not in ("rv_employee", "kv", "pv")
    } | {"gross_wage": wage}
    return spec


async def test_vorsorge_is_estimated_from_the_wage_when_nothing_else_exists(
    db_session: AsyncSession,
) -> None:
    result = await (await build(db_session, _wage_only())).summary()
    assert result["what_missing"] == []
    assert len(result["vorsorge_estimated"]) == 1
    assert "vorsorge_estimated" in {n["code"] for n in result["notes"]}
    assert "kv_pv_unsplit" not in {n["code"] for n in result["notes"]}


@pytest.mark.parametrize("wage", ["0.00", "24000.00"])
async def test_no_estimate_for_zero_wage_and_uebergangsbereich(
    db_session: AsyncSession, wage: str
) -> None:
    result = await (await build(db_session, _wage_only(wage))).summary()
    assert result["vorsorge_estimated"] == []
    assert "vorsorge_missing" in [m["code"] for m in result["what_missing"]]


async def test_estimate_fills_only_groups_without_another_source(
    db_session: AsyncSession,
) -> None:
    # Manual RV only: KV / PV and ALV are estimated, RV is the manual value (never summed).
    spec = _wage_only()
    spec["employments"][0]["rv_employee"] = "5580.00"
    from_rv = pick(await (await build(db_session, spec)).summary())
    est = pick(await (await build(db_session, _wage_only())).summary())
    assert from_rv == est  # manual RV equals the estimated RV: the total is not doubled

    # An item replaces the estimate of its group too (RV item 5580 = estimated RV).
    b = await build(db_session, _wage_only())
    await add_item(b, {"category": "vorsorge_rv", "deductible": "5580.00", "person": "a"})
    result = await b.summary()
    assert pick(result) == est
    # Manual KV (and PV) replace the estimate of KV / PV; RV and ALV stay estimated.
    spec = _wage_only()
    spec["employments"][0].update(kv="1000.00")
    smaller = await (await build(db_session, spec)).summary()
    assert Decimal(smaller["zve"]) > Decimal(est["zve"])
    assert len(smaller["vorsorge_estimated"]) == 1


async def test_estimate_follows_each_spouses_own_wage(db_session: AsyncSession) -> None:
    spec = copy.deepcopy(JOINT)
    for e in spec["employments"]:
        for k in ("rv_employee", "kv", "pv"):
            e.pop(k, None)
    result = await (await build(db_session, spec)).summary()
    assert len(result["vorsorge_estimated"]) == 1  # b earns 24.000: Uebergangsbereich
    assert [m["code"] for m in result["what_missing"]] == ["vorsorge_missing"]


async def test_manual_or_item_rv_replaces_the_rv_estimate(db_session: AsyncSession) -> None:
    base = await (await build(db_session, _wage_only())).summary()
    spec = _wage_only()
    spec["employments"][0]["rv_employee"] = "1000.00"  # differs from the estimated 5580
    manual = await (await build(db_session, spec)).summary()
    assert Decimal(manual["zve_before_children"]) > Decimal(base["zve_before_children"])
    b = await build(db_session, _wage_only())
    await add_item(b, {"category": "vorsorge_rv", "deductible": "1000.00", "person": "a"})
    item = await b.summary()
    assert item["zve_before_children"] == manual["zve_before_children"]  # same RV, nothing added


async def test_a_child_in_the_household_removes_the_childless_surcharge(
    db_session: AsyncSession,
) -> None:
    base = await (await build(db_session, _wage_only())).summary()
    spec = _wage_only()
    spec["persons"]["k1"] = {"kind": "child", "dob": "2015-03-01"}
    with_child = await (await build(db_session, spec)).summary()
    # PV without the 0.6 % surcharge is smaller, so the deduction is smaller, the zvE larger
    assert Decimal(with_child["zve_before_children"]) > Decimal(base["zve_before_children"])
