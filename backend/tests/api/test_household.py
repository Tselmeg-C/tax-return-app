"""#13 household api: persons, profile, employments, child rows, copy, delete, audit.

Fictional names; every Steuer-ID is generated at runtime (`tests.household.steuer_ids`). The
fixed clock is 2026-01-15 (`FakeClock`).
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AppUser, AuditLog, ChildYear, Document, Employment, Person, TaxProfile
from app.domain.enums import FilingStatus
from tests.api.conftest import Home, T
from tests.household.steuer_ids import generate_steuer_id, spaced


async def call(t: T, home: Home | None, method: str, path: str, body: Any = None) -> httpx.Response:
    return await t.api.request(method, path, json=body, cookie=home.cookie if home else None)


async def person(t: T, home: Home, **body: Any) -> dict[str, Any]:
    body.setdefault("kind", "adult")
    body.setdefault("first_name", "Alex")
    r = await call(t, home, "POST", "/persons", body)
    assert r.status_code == 201, r.text
    found: dict[str, Any] = r.json()
    return found


async def child(t: T, home: Home, dob: str, **body: Any) -> dict[str, Any]:
    body.setdefault("first_name", "Kim")
    return await person(t, home, kind="child", dob=dob, **body)


async def profile(
    t: T, home: Home, year: int, taxpayer: str, spouse: str | None = None
) -> httpx.Response:
    return await call(
        t,
        home,
        "PUT",
        f"/household/{year}/profile",
        {
            "filing_status": "joint" if spouse else "single",
            "bundesland": "be",
            "taxpayer_person_id": taxpayer,
            "spouse_person_id": spouse,
        },
    )


async def job(t: T, home: Home, person_id: str, year: int = 2025, **body: Any) -> httpx.Response:
    values = {"person_id": person_id, "year": year, "employer_name": "Muster GmbH"}
    values.setdefault("steuerklasse", "1")
    values.update(body)
    return await call(t, home, "POST", "/employments", values)


async def household(t: T, home: Home, year: int = 2025) -> dict[str, Any]:
    r = await call(t, home, "GET", f"/household/{year}")
    assert r.status_code == 200, r.text
    found: dict[str, Any] = r.json()
    return found


def err(r: httpx.Response) -> tuple[int, str, str | None]:
    body = r.json()
    return r.status_code, body["detail"], body.get("field")


async def audits(session: AsyncSession, entity: str | None = None) -> list[AuditLog]:
    # Every row of a test shares `now()` (one outer transaction), so callers compare sets or
    # take the rows that are new since an earlier call (`since`).
    stmt = select(AuditLog)
    if entity:
        stmt = stmt.where(AuditLog.entity == entity)
    return list((await session.execute(stmt.execution_options(populate_existing=True))).scalars())


async def since(session: AsyncSession, seen: list[AuditLog]) -> list[tuple[str, str]]:
    ids = {r.id for r in seen}
    return sorted((r.entity, r.action.value) for r in await audits(session) if r.id not in ids)


# --- persons -------------------------------------------------------------------------------


async def test_persons_masked_ordered_and_scoped(t: T) -> None:
    a, b = await t.home(), await t.home()
    sid = generate_steuer_id()
    me = await person(t, a, first_name="Bea", steuer_id=spaced(sid), link_to_me=True)
    assert me["steuer_id_masked"] == f"XX XXX XXX {sid[-3:]}"
    assert me["is_me"] is True
    await child(t, a, "2020-01-01", first_name="Anna")
    await person(t, a, first_name="Alex")
    await person(t, b, first_name="Zoe")
    r = await call(t, a, "GET", "/persons")
    names = [(p["first_name"], p["kind"], p["is_me"], p["steuer_id_masked"]) for p in r.json()]
    assert names == [
        ("Alex", "adult", False, None),
        ("Bea", "adult", True, f"XX XXX XXX {sid[-3:]}"),
        ("Anna", "child", False, None),
    ]
    assert sid not in r.text and sid not in (await call(t, a, "GET", "/household/2025")).text
    stored = await t.fresh(Person, uuid.UUID(me["id"]))
    assert stored.steuer_id == sid  # normalised (no spaces), encrypted at rest


async def test_link_to_me_twice_is_409(t: T) -> None:
    a = await t.home()
    await person(t, a, link_to_me=True)
    r = await call(
        t, a, "POST", "/persons", {"kind": "adult", "first_name": "X", "link_to_me": True}
    )
    assert err(r)[:2] == (409, "already_linked")


@pytest.mark.parametrize("case", range(8))
async def test_invalid_steuer_ids(t: T, case: int) -> None:
    from tests.household.test_validation import _bad_ids

    a = await t.home()
    _, value = _bad_ids()[case]
    r = await call(
        t, a, "POST", "/persons", {"kind": "adult", "first_name": "A", "steuer_id": value}
    )
    assert err(r) == (422, "invalid_steuer_id", "steuer_id")
    assert value not in r.text


async def test_duplicate_steuer_id(t: T) -> None:
    a = await t.home()
    sid = generate_steuer_id()
    await person(t, a, steuer_id=sid)
    r = await call(t, a, "POST", "/persons", {"kind": "adult", "first_name": "B", "steuer_id": sid})
    assert err(r) == (422, "duplicate_steuer_id", "steuer_id")
    assert sid not in r.text
    other = await person(t, a, first_name="C")
    r = await call(t, a, "PATCH", f"/persons/{other['id']}", {"steuer_id": spaced(sid)})
    assert err(r) == (422, "duplicate_steuer_id", "steuer_id")


async def test_person_field_rules(t: T) -> None:
    a = await t.home()
    cases = [
        ({"kind": "adult", "first_name": "A", "dob": "2026-01-16"}, "invalid_dob"),  # tomorrow
        ({"kind": "adult", "first_name": "A", "dob": "1899-12-31"}, "invalid_dob"),
        ({"kind": "child", "first_name": "A"}, "invalid_dob"),
        ({"kind": "adult", "first_name": "A", "disability_grade": 15}, "invalid_disability_grade"),
        ({"kind": "adult", "first_name": "A", "disability_grade": 110}, "invalid_disability_grade"),
        ({"kind": "adult", "first_name": "  "}, "invalid_name"),
        ({"kind": "adult", "first_name": "A" * 101}, "invalid_name"),
    ]
    for body, code in cases:
        r = await call(t, a, "POST", "/persons", body)
        assert err(r)[:2] == (422, code), body
    p = await person(t, a)
    r = await call(t, a, "PATCH", f"/persons/{p['id']}", {"kind": "child"})
    assert err(r) == (422, "kind_immutable", "kind")
    assert (await call(t, a, "POST", "/persons", {"kind": "alien", "first_name": "A"})).json() == {
        "detail": "invalid_request"
    }


async def test_patch_merges_and_removes_steuer_id(t: T) -> None:
    a = await t.home()
    p = await person(t, a, steuer_id=generate_steuer_id(), religion="rk")
    r = await call(t, a, "PATCH", f"/persons/{p['id']}", {"last_name": "Muster"})
    assert r.json()["religion"] == "rk" and r.json()["steuer_id_masked"] is not None
    r = await call(t, a, "PATCH", f"/persons/{p['id']}", {"steuer_id": None})
    assert r.status_code == 200 and r.json()["steuer_id_masked"] is None
    assert r.json()["last_name"] == "Muster"


async def test_merkzeichen_stored_audited_and_validated(t: T) -> None:
    a = await t.home()
    p = await person(t, a)
    assert p["merkzeichen_h_bl_tbl"] is False  # default
    seen = await audits(t.session)
    r = await call(t, a, "PATCH", f"/persons/{p['id']}", {"merkzeichen_h_bl_tbl": True})
    assert r.status_code == 200 and r.json()["merkzeichen_h_bl_tbl"] is True
    [last] = [r for r in await audits(t.session, "person") if r.id not in {s.id for s in seen}]
    assert last.after == {"merkzeichen_h_bl_tbl": True} and last.before == {
        "merkzeichen_h_bl_tbl": False
    }
    row = (
        await t.session.execute(select(Person).where(Person.id == uuid.UUID(p["id"])))
    ).scalar_one()
    assert row.merkzeichen_h_bl_tbl is True
    seen = await audits(t.session)
    await call(t, a, "PATCH", f"/persons/{p['id']}", {"merkzeichen_h_bl_tbl": True})
    assert await since(t.session, seen) == []  # unchanged: no audit row
    for bad in (None, "yes", 1):
        r = await call(t, a, "PATCH", f"/persons/{p['id']}", {"merkzeichen_h_bl_tbl": bad})
        assert r.status_code == 422, bad
    c = await child(t, a, "2020-05-05", merkzeichen_h_bl_tbl=True)
    assert c["merkzeichen_h_bl_tbl"] is True


# --- children ------------------------------------------------------------------------------


async def test_max_months_and_child_rules(t: T) -> None:
    a = await t.home()
    newborn = await child(t, a, "2025-03-10", first_name="A")
    turns25 = await child(t, a, "2000-06-15", first_name="B")
    too_old = await child(t, a, "1999-06-15", first_name="C")
    disabled = await child(t, a, "1999-06-15", first_name="D", disability_grade=50)
    turns18 = await child(t, a, "2007-04-01", first_name="E")
    unborn = await child(t, a, "2026-01-05", first_name="F")
    got = {c["person_id"]: c["max_months"] for c in (await household(t, a, 2025))["children"]}
    assert got == {
        newborn["id"]: 10,
        turns25["id"]: 6,
        too_old["id"]: 0,
        disabled["id"]: 12,
        turns18["id"]: 12,
    }
    later = {c["person_id"] for c in (await household(t, a, 2026))["children"]}
    assert unborn["id"] in later

    def put(pid: str, months: int, year: int = 2025) -> Any:
        body = {"months": months, "allowance_share": "full"}
        return call(t, a, "PUT", f"/household/{year}/children/{pid}", body)

    assert err(await put(too_old["id"], 0))[:2] == (422, "child_too_old")
    assert err(await put(unborn["id"], 1))[:2] == (422, "child_not_born")
    assert err(await put(newborn["id"], 11)) == (422, "months_exceed", "months")
    ok = await put(newborn["id"], 10)
    assert ok.status_code == 200 and ok.json()["in_household"] is True
    adult = await person(t, a)
    assert err(await put(adult["id"], 1))[:2] == (422, "not_a_child")
    entry = next(c for c in (await household(t, a))["children"] if c["person_id"] == newborn["id"])
    assert entry["child_year"]["months"] == 10
    r = await call(t, a, "DELETE", f"/household/2025/children/{newborn['id']}")
    assert r.status_code == 204
    r = await call(t, a, "DELETE", f"/household/2025/children/{newborn['id']}")
    assert err(r)[:2] == (404, "not_found")


async def test_dob_change_rechecks_child_rows(t: T) -> None:
    a = await t.home()
    kid = await child(t, a, "2015-01-01")
    r = await call(
        t,
        a,
        "PUT",
        f"/household/2025/children/{kid['id']}",
        {"months": 12, "allowance_share": "half"},
    )
    assert r.status_code == 200
    r = await call(t, a, "PATCH", f"/persons/{kid['id']}", {"dob": "2025-06-01"})
    assert err(r) == (422, "months_exceed", "dob")
    assert (await t.fresh(Person, uuid.UUID(kid["id"]))).dob == date(2015, 1, 1)


# --- profile -------------------------------------------------------------------------------


async def test_profile_rules(t: T) -> None:
    a, b = await t.home(), await t.home()
    x, y = await person(t, a, first_name="X"), await person(t, a, first_name="Y")
    kid = await child(t, a, "2020-01-01")
    foreign = await person(t, b)
    r = await profile(t, a, 2025, x["id"], y["id"])
    assert r.status_code == 200, r.text
    assert r.json()["filing_status"] == "joint"
    assert (await household(t, a))["profile"]["spouse_person_id"] == y["id"]
    t.session.expunge_all()
    loaded = (await t.session.execute(select(TaxProfile))).scalar_one()
    assert loaded.filing_status is FilingStatus.JOINT

    def body(**kw: Any) -> dict[str, Any]:
        values = {"filing_status": "joint", "bundesland": "by", "taxpayer_person_id": x["id"]}
        values.update(kw)
        return values

    cases = [
        (body(), "spouse_required"),
        (body(filing_status="single", spouse_person_id=y["id"]), "spouse_not_allowed"),
        (body(spouse_person_id=kid["id"]), "person_must_be_adult"),
        (body(spouse_person_id=foreign["id"]), "unknown_person"),
        (body(spouse_person_id=str(uuid.uuid4())), "unknown_person"),
        (body(spouse_person_id=x["id"]), "spouse_same_person"),
        (body(bundesland="xx", spouse_person_id=y["id"]), "invalid_request"),
    ]
    for sent, code in cases:
        r = await call(t, a, "PUT", "/household/2025/profile", sent)
        assert (r.status_code, r.json()["detail"]) == (422, code), sent


async def test_filing_status_switch_keeps_employments(t: T) -> None:
    a = await t.home()
    x, y = await person(t, a, first_name="X"), await person(t, a, first_name="Y")
    await profile(t, a, 2025, x["id"], y["id"])
    assert (await job(t, a, y["id"])).status_code == 201
    await profile(t, a, 2025, x["id"])
    jobs = (await household(t, a))["employments"]
    assert [(j["person_id"], j["in_return"]) for j in jobs] == [(y["id"], False)]
    assert err(await job(t, a, y["id"]))[:2] == (422, "person_not_in_return")
    await profile(t, a, 2025, x["id"], y["id"])
    assert [j["in_return"] for j in (await household(t, a))["employments"]] == [True]


async def test_multiple_employments_and_days(t: T) -> None:
    a = await t.home()
    x = await person(t, a)
    assert err(await job(t, a, x["id"]))[:2] == (422, "profile_required")
    await profile(t, a, 2025, x["id"])
    first = await job(t, a, x["id"], steuerklasse="1", office_days=200, homeoffice_days=100)
    assert first.status_code == 201
    second = await job(t, a, x["id"], steuerklasse="6", employer_name="Zweit AG", office_days=66)
    assert err(second) == (422, "days_exceed_year", "office_days")
    second = await job(t, a, x["id"], steuerklasse="6", employer_name="Zweit AG", office_days=65)
    assert second.status_code == 201, second.text
    jobs = (await household(t, a))["employments"]
    assert sorted(j["steuerklasse"] for j in jobs) == ["1", "6"]
    r = await job(t, a, x["id"], steuerklasse="3", has_factor=True)
    assert err(r) == (422, "factor_requires_iv", "has_factor")
    r = await call(t, a, "PATCH", f"/employments/{first.json()['id']}", {"homeoffice_days": 101})
    assert err(r)[:2] == (422, "days_exceed_year")
    r = await call(t, a, "PATCH", f"/employments/{first.json()['id']}", {"year": 2026})
    assert err(r)[:2] == (422, "invalid_request")
    r = await call(t, a, "PATCH", f"/employments/{first.json()['id']}", {"commute_km": 12})
    assert r.status_code == 200 and r.json()["commute_km"] == 12 and r.json()["in_return"]
    r = await call(t, a, "DELETE", f"/employments/{first.json()['id']}")
    assert r.status_code == 204


async def test_unsupported_year_everywhere(t: T) -> None:
    a = await t.home()
    x = await person(t, a)
    kid = await child(t, a, "2020-01-01")
    await profile(t, a, 2025, x["id"])
    child_body = {"months": 1, "allowance_share": "full"}
    responses = [
        await call(t, a, "GET", "/household/2024"),
        await profile(t, a, 2024, x["id"]),
        await call(t, a, "PUT", f"/household/2024/children/{kid['id']}", child_body),
        await call(t, a, "DELETE", f"/household/2024/children/{kid['id']}"),
        await call(t, a, "POST", "/household/2024/copy", {"from_year": 2025}),
        await call(t, a, "POST", "/household/2026/copy", {"from_year": 2024}),
        await job(t, a, x["id"], year=2024),
    ]
    for r in responses:
        assert (r.status_code, r.json()["detail"]) == (422, "unsupported_year"), r.request.url


async def test_copy(t: T) -> None:
    a = await t.home()
    x, y = await person(t, a, first_name="X"), await person(t, a, first_name="Y")
    young = await child(t, a, "2018-05-01", first_name="A")
    old = await child(t, a, "2001-03-20", first_name="B")  # turns 25 in March 2026
    await profile(t, a, 2025, x["id"], y["id"])
    await job(t, a, x["id"], office_days=100)
    await job(t, a, y["id"], steuerklasse="4", has_factor=True, commute_km=7)
    for kid in (young, old):
        r = await call(
            t,
            a,
            "PUT",
            f"/household/2025/children/{kid['id']}",
            {"months": 12, "allowance_share": "full"},
        )
        assert r.status_code == 200
    r = await call(t, a, "POST", "/household/2026/copy", {"from_year": 2025})
    assert r.status_code == 200, r.text
    new, before = r.json(), await household(t, a, 2025)
    strip = ("year",)
    assert {k: v for k, v in new["profile"].items() if k not in strip} == {
        k: v for k, v in before["profile"].items() if k not in strip
    }

    def jobs(h: dict[str, Any]) -> list[Any]:
        keep = ("person_id", "employer_name", "steuerklasse", "has_factor", "commute_km")
        return sorted(
            (
                tuple(j[k] for k in (*keep, "office_days", "homeoffice_days"))
                for j in h["employments"]
            ),
            key=str,
        )

    assert jobs(new) == jobs(before)
    months = {c["person_id"]: c["child_year"]["months"] for c in new["children"]}
    assert months == {young["id"]: 12, old["id"]: 3}
    assert new["years_with_profile"] == [2025, 2026]
    again = await call(t, a, "POST", "/household/2026/copy", {"from_year": 2025})
    assert err(again)[:2] == (409, "year_not_empty")
    b = await t.home()
    empty = await call(t, b, "POST", "/household/2026/copy", {"from_year": 2025})
    assert err(empty)[:2] == (404, "nothing_to_copy")


# --- delete person -------------------------------------------------------------------------


async def test_delete_person(t: T) -> None:
    a = await t.home()
    with_item = await person(t, a, first_name="Item")
    await t.item(a, person_id=uuid.UUID(with_item["id"]))
    r = await call(t, a, "DELETE", f"/persons/{with_item['id']}")
    assert r.status_code == 409 and r.json() == {"detail": "person_has_tax_items", "count": 1}
    assert await t.fresh(Person, uuid.UUID(with_item["id"])) is not None

    taxpayer = await person(t, a, first_name="Tax")
    await profile(t, a, 2025, taxpayer["id"])
    r = await call(t, a, "DELETE", f"/persons/{taxpayer['id']}")
    assert r.status_code == 409 and r.json() == {"detail": "person_in_profile", "years": [2025]}

    kid = await child(t, a, "2020-01-01")
    await call(
        t,
        a,
        "PUT",
        f"/household/2025/children/{kid['id']}",
        {"months": 12, "allowance_share": "full"},
    )
    seen = await audits(t.session)
    assert (await call(t, a, "DELETE", f"/persons/{kid['id']}")).status_code == 204
    assert await t.count(ChildYear) == 0
    assert await since(t.session, seen) == [
        ("child_year", "delete"),
        ("person", "delete"),
    ]

    linked = await person(t, a, first_name="Me", link_to_me=True)
    doc = await t.doc(a)
    assert (await call(t, a, "DELETE", f"/persons/{linked['id']}")).status_code == 204
    user = await t.fresh(AppUser, a.user.id)
    assert user.person_id is None
    assert (await t.fresh(Document, doc.id)) is not None


async def test_other_household_ids_are_404_like_random(t: T) -> None:
    a, b = await t.home(), await t.home()
    theirs = await person(t, b)
    kid = await child(t, b, "2020-01-01")
    await profile(t, b, 2025, theirs["id"])
    their_job = (await job(t, b, theirs["id"])).json()
    random = str(uuid.uuid4())
    for target in (theirs["id"], random):
        for method, path, body in [
            ("PATCH", f"/persons/{target}", {"first_name": "Q"}),
            ("DELETE", f"/persons/{target}", None),
        ]:
            r = await call(t, a, method, path, body)
            assert r.status_code == 404 and r.json() == {"detail": "not_found"}
    for target in (kid["id"], random):
        body = {"months": 1, "allowance_share": "full"}
        r = await call(t, a, "PUT", f"/household/2025/children/{target}", body)
        assert r.status_code == 404 and r.json() == {"detail": "not_found"}
    for target in (their_job["id"], random):
        r = await call(t, a, "PATCH", f"/employments/{target}", {"commute_km": 1})
        assert r.status_code == 404 and r.json() == {"detail": "not_found"}
        r = await call(t, a, "DELETE", f"/employments/{target}")
        assert r.status_code == 404 and r.json() == {"detail": "not_found"}


async def test_session_and_csrf(t: T) -> None:
    a = await t.home()
    some = uuid.uuid4()
    writes = [
        ("POST", "/persons"),
        ("PATCH", f"/persons/{some}"),
        ("DELETE", f"/persons/{some}"),
        ("PUT", "/household/2025/profile"),
        ("POST", "/household/2025/copy"),
        ("PUT", f"/household/2025/children/{some}"),
        ("DELETE", f"/household/2025/children/{some}"),
        ("POST", "/employments"),
        ("PATCH", f"/employments/{some}"),
        ("DELETE", f"/employments/{some}"),
    ]
    for method, path in [*writes, ("GET", "/household/2025"), ("GET", "/persons")]:
        r = await t.api.request(method, path, json={})
        assert r.status_code == 401, path
    for method, path in writes:
        r = await t.api.request(method, path, json={}, cookie=a.cookie, csrf=False)
        assert r.status_code == 403 and r.json() == {"detail": "csrf"}, path


# --- audit ---------------------------------------------------------------------------------


async def test_audit_rows(t: T, monkeypatch: pytest.MonkeyPatch) -> None:
    a = await t.home()
    p = await person(t, a, link_to_me=True)
    rows = await audits(t.session)
    assert sorted((r.entity, r.action.value) for r in rows) == [
        ("app_user", "update"),
        ("person", "create"),
    ]
    assert all(r.actor_user_id == a.user.id for r in rows)
    sid = generate_steuer_id()
    seen = await audits(t.session)
    await call(t, a, "PATCH", f"/persons/{p['id']}", {"steuer_id": sid})
    [last] = [r for r in await audits(t.session) if r.id not in {s.id for s in seen}]
    assert last.before == {"steuer_id": "[redacted]"} and last.after == {"steuer_id": "[redacted]"}
    seen = await audits(t.session)
    r = await call(t, a, "PATCH", f"/persons/{p['id']}", {"steuer_id": spaced(sid)})
    assert r.status_code == 200
    r = await call(t, a, "PATCH", f"/persons/{p['id']}", {"first_name": "Alex"})
    assert await since(t.session, seen) == []  # no-op PATCHes write nothing

    await profile(t, a, 2025, p["id"])
    assert await since(t.session, seen) == [("tax_profile", "create")]
    seen = await audits(t.session)
    await profile(t, a, 2025, p["id"])  # same values: no row
    e = (await job(t, a, p["id"])).json()
    assert await since(t.session, seen) == [("employment", "create")]
    seen = await audits(t.session)
    await call(t, a, "PATCH", f"/employments/{e['id']}", {"office_days": 3})
    [update] = [r for r in await audits(t.session) if r.id not in {s.id for s in seen}]
    assert update.before == {"office_days": 0} and update.after == {"office_days": 3}
    seen = await audits(t.session)
    await call(t, a, "DELETE", f"/employments/{e['id']}")
    assert await since(t.session, seen) == [("employment", "delete")]

    seen = await audits(t.session)
    from app.household import service

    original = service._audit

    async def audit_then_fail(*args: Any, **kwargs: Any) -> None:
        await original(*args, **kwargs)  # the audit row is written, then the request fails
        raise RuntimeError("injected")

    monkeypatch.setattr(service, "_audit", audit_then_fail)
    with pytest.raises(RuntimeError):
        await call(t, a, "POST", "/persons", {"kind": "adult", "first_name": "Rolled"})
    monkeypatch.undo()
    assert await since(t.session, seen) == []
    names = [p.first_name for p in (await t.session.execute(select(Person))).scalars()]
    assert "Rolled" not in names
    assert await t.count(Employment) == 0


async def test_meta_labels_for_household(t: T) -> None:
    from app.domain.enums import (
        LABELS_DE,
        AllowanceShare,
        Bundesland,
        PersonKind,
        Religion,
        Steuerklasse,
    )

    a = await t.home()
    body = (await call(t, a, "GET", "/meta/labels")).json()
    for key, enum in [
        ("bundeslaender", Bundesland),
        ("filing_statuses", FilingStatus),
        ("steuerklassen", Steuerklasse),
        ("religions", Religion),
        ("allowance_shares", AllowanceShare),
        ("person_kinds", PersonKind),
    ]:
        assert body[key] == [{"code": m.value, "label": LABELS_DE[enum][m]} for m in enum]
    assert body["steuerklassen"][3] == {"code": "4", "label": "IV"}
