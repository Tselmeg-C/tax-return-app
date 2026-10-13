"""#17 api: `GET /summary`, the new employment / Lohnersatz fields, labels, audit and privacy."""

from __future__ import annotations

import io
import json
import re
from typing import Any

from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import select

from app.db.models import AuditLog
from tests.api.conftest import Home, T
from tests.api.test_household import call, job, person, profile

MONEY = re.compile(r"^-?\d{1,9}\.\d{2}$")
# Unusual values that appear nowhere else (amounts and a name must never reach logs / spans).
WAGE, LST, LABEL = "73245.19", "11876.53", "Zeta"


async def household_with_job(t: T, home: Home, **fields: Any) -> tuple[str, str]:
    me = (await person(t, home, first_name=f"Name{LABEL}"))["id"]
    assert (await profile(t, home, 2025, me)).status_code == 200
    r = await job(t, home, me, employer_name=f"Employer{LABEL}", **fields)
    assert r.status_code == 201, r.text
    return me, r.json()["id"]


async def test_summary_requires_a_session(t: T) -> None:
    assert (await t.get(None, "/summary?year=2025")).status_code == 401


async def test_summary_without_profile_and_unsupported_year(t: T) -> None:
    home = await t.home()
    ok = await t.get(home, "/summary?year=2025")
    assert ok.status_code == 200
    body = ok.json()
    assert body["status"] == "no_profile" and body["refund"] is None and body["pflicht"] is None
    bad = await t.get(home, "/summary?year=1999")
    assert bad.status_code == 422
    assert bad.json() == {"detail": "unsupported_year", "field": "year"}
    assert (await t.get(home, "/summary")).status_code == 422
    assert (await t.get(home, "/summary?year=abc")).status_code == 422


async def test_summary_shape_and_money_strings(t: T) -> None:
    home = await t.home()
    me, emp = await household_with_job(t, home)
    r = await call(
        t,
        home,
        "PATCH",
        f"/employments/{emp}",
        {"gross_wage": WAGE, "lohnsteuer": LST, "rv_employee": "6800.00", "kv": "3900.00"},
    )
    assert r.status_code == 200
    assert r.json()["gross_wage"] == WAGE and r.json()["pv"] is None
    body = (await t.get(home, "/summary?year=2025")).json()
    assert body["status"] == "ok" and body["year"] == 2025
    assert re.fullmatch(r"2025-[0-9a-f]{12}", body["params_version"])
    for key in (
        "refund",
        "zve",
        "zve_before_children",
        "gde",
        "tarifliche_est",
        "ermaessigung_35a",
        "festzusetzende_est",
        "soli",
        "kirchensteuer",
        "kindergeld_added",
    ):
        assert MONEY.fullmatch(body[key]), key
    assert all(MONEY.fullmatch(v) for v in body["withheld"].values())
    assert all(MONEY.fullmatch(a["amount"]) for a in body["by_anlage"])
    assert body["provisional"] is True and "kinder" in body["provisional_sections"]
    assert set(body["pflicht"]) == {
        "state",
        "reasons",
        "not_checked",
        "deadline_statutory",
        "deadline",
        "deadline_kind",
        "overdue",
    }
    assert body["pflicht"]["deadline"] == "2029-12-31" and body["pflicht"]["overdue"] is False
    assert isinstance(body["attention_count"], int) and isinstance(body["notes"], list)
    assert body["what_missing"] == [] or all(
        set(m) == {"code", "person_id"} for m in body["what_missing"]
    )


async def test_summary_is_scoped_to_the_household(t: T) -> None:
    a, b = await t.home(), await t.home()
    await household_with_job(t, a)
    assert (await t.get(a, "/summary?year=2025")).json()["status"] == "ok"
    assert (await t.get(b, "/summary?year=2025")).json()["status"] == "no_profile"


async def test_labels_cover_the_codes_the_summary_returns(t: T) -> None:
    home = await t.home()
    labels = (await t.get(home, "/meta/labels")).json()
    for group in (
        "deduction_notes",
        "estimate_missing",
        "pflicht_states",
        "pflicht_reasons",
        "pflicht_checks",
        "pflicht_deadline_kinds",
        "anlagen",
    ):
        assert labels[group] and all(set(x) == {"code", "label"} for x in labels[group]), group
    codes = {x["code"] for x in labels["estimate_missing"]}
    assert codes == {"no_lstb", "vorsorge_missing", "kap_v_not_supported", "person_unassigned"}


async def test_employment_money_fields_validation(t: T) -> None:
    home = await t.home()
    _, emp = await household_with_job(t, home)
    for field, bad in (("lohnsteuer", 11876.53), ("gross_wage", "-1.00"), ("kv", "12.5")):
        r = await call(t, home, "PATCH", f"/employments/{emp}", {field: bad})
        assert r.status_code == 422, (field, r.text)
        assert r.json() == {"detail": "invalid_amount", "field": field}
        assert str(bad) not in r.text
    cleared = await call(t, home, "PATCH", f"/employments/{emp}", {"gross_wage": "10.00"})
    assert cleared.json()["gross_wage"] == "10.00"
    cleared = await call(t, home, "PATCH", f"/employments/{emp}", {"gross_wage": None})
    assert cleared.json()["gross_wage"] is None


async def test_employment_create_with_amounts_and_copy_leaves_them_behind(t: T) -> None:
    home = await t.home()
    me = (await person(t, home))["id"]
    await profile(t, home, 2025, me)
    r = await job(t, home, me, gross_wage="1000.00", lohnsteuer="100.00")
    assert r.status_code == 201 and r.json()["lohnsteuer"] == "100.00"
    copied = await call(t, home, "POST", "/household/2026/copy", {"from_year": 2025})
    assert copied.status_code == 200
    jobs = copied.json()["employments"]
    assert len(jobs) == 1 and jobs[0]["gross_wage"] is None and jobs[0]["lohnsteuer"] is None


async def test_lohnersatz_put_get_delete_and_audit(t: T) -> None:
    home = await t.home()
    me, _ = await household_with_job(t, home)
    url = f"/household/2025/lohnersatz/{me}"
    put = await call(t, home, "PUT", url, {"amount": "8000.00"})
    assert put.status_code == 200 and put.json()["amount"] == "8000.00"
    again = await call(t, home, "PUT", url, {"amount": "8500.50"})
    assert again.json()["amount"] == "8500.50"
    year = (await t.get(home, "/household/2025")).json()
    assert year["lohnersatz"] == [{"person_id": me, "year": 2025, "amount": "8500.50"}]
    summary = (await t.get(home, "/summary?year=2025")).json()
    assert summary["pflicht"]["state"] == "mandatory"
    assert "side_income_over_limit" in summary["pflicht"]["reasons"]
    assert (await call(t, home, "DELETE", url)).status_code == 204
    assert (await call(t, home, "DELETE", url)).status_code == 404
    assert (await t.get(home, "/household/2025")).json()["lohnersatz"] == []
    actions = [r.action.value for r in await t.audit("lohnersatz")]
    assert sorted(actions) == ["create", "delete", "update"]


async def test_lohnersatz_rejections(t: T) -> None:
    home = await t.home()
    me, _ = await household_with_job(t, home)
    stranger = (await person(t, home, first_name="Fremd"))["id"]
    url = f"/household/2025/lohnersatz/{me}"
    for bad in (8000, "8000", "-1.00", None):
        r = await call(t, home, "PUT", url, {"amount": bad})
        assert r.status_code == 422 and r.json() == {"detail": "invalid_amount", "field": "amount"}
    other = await call(t, home, "PUT", f"/household/2025/lohnersatz/{stranger}", {"amount": "1.00"})
    assert other.json() == {"detail": "person_not_in_return", "field": "person_id"}
    no_profile = await call(t, home, "PUT", f"/household/2026/lohnersatz/{me}", {"amount": "1.00"})
    assert no_profile.json() == {"detail": "profile_required", "field": "year"}
    unsupported = await call(t, home, "PUT", f"/household/1999/lohnersatz/{me}", {"amount": "1.00"})
    assert unsupported.json()["detail"] == "unsupported_year"
    foreign = await t.home()
    r = await call(t, foreign, "PUT", url, {"amount": "1.00"})
    assert r.status_code == 422 and r.json()["detail"] == "profile_required"


async def test_no_amount_or_name_in_logs_spans_errors_or_audit(
    t: T, json_log: io.StringIO, spans: InMemorySpanExporter
) -> None:
    home = await t.home()
    me, emp = await household_with_job(t, home, gross_wage=WAGE)
    bodies = []
    r = await call(t, home, "PATCH", f"/employments/{emp}", {"lohnsteuer": LST, "kv": "400.00"})
    bodies.append(r.text)
    r = await call(t, home, "PATCH", f"/employments/{emp}", {"pv": 99.5})
    assert r.status_code == 422
    bodies.append(r.text)
    r = await call(t, home, "PUT", f"/household/2025/lohnersatz/{me}", {"amount": "8123.45"})
    bodies.append(r.text)
    bodies.append((await t.get(home, "/summary?year=2025")).text)
    bodies.append((await t.get(home, "/summary?year=1999")).text)
    logs = json_log.getvalue()
    assert '"estimate.built"' in logs
    span_values = [
        str(v) for s in spans.get_finished_spans() for v in (s.attributes or {}).values()
    ]
    rows = list((await t.session.execute(select(AuditLog))).scalars())
    audit = json.dumps([[r.before, r.after] for r in rows])
    for sentinel in (WAGE, LST, LABEL, "8123.45", "99.5"):
        assert sentinel not in logs, sentinel
        assert not [v for v in span_values if sentinel in v], sentinel
    for sentinel in (LABEL,):
        assert sentinel not in audit
    for sentinel in (WAGE, LST, LABEL, "8123.45"):  # amounts only where they belong
        for body in bodies[1:2] + bodies[4:5]:
            assert sentinel not in body, sentinel
