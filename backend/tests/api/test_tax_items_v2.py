"""#61 tax item editing v2: vendor, dates, payment method, items without a document."""

from __future__ import annotations

import io
import json
import secrets
import uuid
from datetime import date
from typing import Any

import pytest
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from sqlalchemy import func, select

from app.db.models import Estimate, TaxItem
from app.db.scope import HouseholdScope
from app.domain.enums import AuditAction
from app.estimate.service import build_summary
from tests.api.conftest import Home, T
from tests.documents.conftest import metric_points
from tests.estimate.helpers import add_item, build, load_yaml

SINGLE = {s["id"]: s for s in load_yaml("golden_2025.yaml")["scenarios"]}["s1_single_wage_commute"]


def new_item(**kw: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "category": "handwerkerleistung",
        "is_relevant": True,
        "gross_amount": "312.40",
        "deductible_amount": "180.00",
        "labour_share_35a": "180.00",
        "year": 2025,
        "vendor": "Muster Handwerk",
    }
    body.update(kw)
    return body


async def post(t: T, home: Home, body: dict[str, Any]) -> Any:
    return await t.api.post("/tax-items", json=body, cookie=home.cookie)


async def delete(t: T, home: Home, item_id: Any, version: Any = 1) -> Any:
    return await t.api.request(
        "DELETE", f"/tax-items/{item_id}?version={version}", cookie=home.cookie
    )


async def test_patch_new_fields(t: T, metric_reader: InMemoryMetricReader) -> None:
    a = await t.home()
    item = await t.item(a)
    body = {
        "version": 1,
        "vendor": "  Muster GmbH ",
        "invoice_date": "2025-03-01",
        "payment_date": "2025-03-05",
        "payment_method": "bank_transfer",
    }
    r = await t.patch(a, item.id, body)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["vendor"] == "Muster GmbH" and out["invoice_date"] == "2025-03-01"
    assert out["payment_date"] == "2025-03-05" and out["payment_method"] == "bank_transfer"
    assert out["version"] == 2 and out["overridden_by_user"] is True
    assert (out["year"], out["deductible_amount"]) == (2025, "100.00")
    (row,) = await t.audit("tax_item")
    assert row.after is not None and row.after["vendor"] == "[redacted]"
    assert row.after["payment_method"] == "bank_transfer"
    fields = {
        p.attributes["field"]
        for p in metric_points(metric_reader)["belegbot.tax_item.overrides"]
        if p.attributes
    }
    assert fields == {"vendor", "invoice_date", "payment_date", "payment_method"}

    cleared = await t.patch(a, item.id, {"version": 2, "vendor": "  ", "invoice_date": None})
    assert cleared.status_code == 200
    assert cleared.json()["vendor"] is None and cleared.json()["invoice_date"] is None


async def test_edit_does_not_rederive_year_or_relevance(t: T) -> None:
    a = await t.home()
    item = await t.item(a, category="handwerkerleistung", labour_share_35a=None)
    r = await t.patch(
        a, item.id, {"version": 1, "payment_date": "2024-12-30", "payment_method": "cash"}
    )
    assert r.status_code == 200
    out = r.json()
    assert (out["year"], out["is_relevant"], out["deductible_amount"]) == (2025, True, "100.00")


@pytest.mark.parametrize(
    ("body", "code", "field"),
    [
        ({"vendor": "x" * 201}, "invalid_text", "vendor"),
        ({"vendor": 5}, "invalid_text", "vendor"),
        ({"vendor": "a\nb"}, "invalid_text", "vendor"),
        ({"invoice_date": "01.03.2025"}, "invalid_date", "invoice_date"),
        ({"payment_date": "2025-02-30"}, "invalid_date", "payment_date"),
        ({"payment_date": "1999-01-01"}, "invalid_date", "payment_date"),
        ({"payment_date": 20250101}, "invalid_date", "payment_date"),
        ({"payment_method": "bitcoin"}, "invalid_payment_method", "payment_method"),
        ({"payment_method": ["cash"]}, "invalid_payment_method", "payment_method"),
        ({"payment_method": None}, "invalid_request", "payment_method"),
    ],
)
async def test_patch_validation(t: T, body: dict[str, Any], code: str, field: str) -> None:
    a = await t.home()
    item = await t.item(a)
    r = await t.patch(a, item.id, {"version": 1, **body})
    assert r.status_code == 422
    assert r.json() == {"detail": code, "field": field}
    for value in body.values():
        if isinstance(value, str):
            assert value not in r.text
    assert (await t.fresh(TaxItem, item.id)).version == 1 and await t.audit() == []


async def test_patch_conflict_and_other_household(t: T) -> None:
    a, b = await t.home(), await t.home()
    item = await t.item(a)
    assert (await t.patch(a, item.id, {"version": 1, "vendor": "A"})).status_code == 200
    late = await t.patch(a, item.id, {"version": 1, "vendor": "B"})
    assert late.status_code == 409 and late.json()["tax_item"]["vendor"] == "A"
    foreign = await t.patch(b, item.id, {"version": 2, "vendor": "C"})
    assert foreign.status_code == 404 and foreign.json() == {"detail": "not_found"}


async def test_post_without_document(t: T, metric_reader: InMemoryMetricReader) -> None:
    a = await t.home()
    r = await post(t, a, new_item(invoice_date="2025-02-01", payment_method="bank_transfer"))
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["document"] is None and out["document_id"] is None
    assert out["overridden_by_user"] is True and out["version"] == 1
    assert out["vendor"] == "Muster Handwerk" and out["payment_method"] == "bank_transfer"
    assert out["confidence"] is None and out["anlage"] is not None
    (row,) = await t.audit()
    assert (row.entity, row.action) == ("tax_item", AuditAction.CREATE)
    assert row.after is not None and row.after["vendor"] == "[redacted]"
    points = metric_points(metric_reader)["belegbot.tax_item.manual_items"]
    assert [p.attributes for p in points] == [{"attention_reason": "none"}]
    listed = await t.get(a, "/tax-items?year=2025&filter=manual")
    assert [i["id"] for i in listed.json()["items"]] == [out["id"]]
    default = await post(t, a, new_item(vendor="Zweit"))
    assert default.json()["payment_method"] == "unknown"


@pytest.mark.parametrize(
    ("body", "code", "field"),
    [
        ({"vendor": None}, "vendor_required", "vendor"),
        ({"vendor": "   "}, "vendor_required", "vendor"),
        ({"reason": "x" * 501}, "invalid_text", "reason"),
        ({"year": 2024}, "unsupported_year", "year"),
        ({"payment_method": "x"}, "invalid_payment_method", "payment_method"),
        ({"invoice_date": "nope"}, "invalid_date", "invoice_date"),
        ({"gross_amount": "1,5"}, "invalid_amount", "gross_amount"),
    ],
)
async def test_post_validation(t: T, body: dict[str, Any], code: str, field: str) -> None:
    a = await t.home()
    r = await post(t, a, new_item(**body))
    assert r.status_code == 422 and r.json() == {"detail": code, "field": field}
    assert await t.count(TaxItem) == 0 and await t.audit() == []


async def test_post_household_level_person_refused_and_required_fields(t: T) -> None:
    a = await t.home()
    missing = await post(t, a, {"vendor": "x"})
    assert missing.status_code == 422 and missing.json() == {"detail": "invalid_request"}
    r = await post(t, a, new_item(person_id=str(uuid.uuid4())))
    assert r.status_code == 422 and r.json()["field"] == "person_id"


async def test_delete_document_less_item(t: T, metric_reader: InMemoryMetricReader) -> None:
    a, b = await t.home(), await t.home()
    created = (await post(t, a, new_item())).json()
    assert (await delete(t, b, created["id"])).status_code == 404  # IDOR
    stale = await delete(t, a, created["id"], version=7)
    assert stale.status_code == 409 and stale.json()["detail"] == "version_conflict"
    assert (await delete(t, a, created["id"], "x")).status_code == 422
    ok = await delete(t, a, created["id"])
    assert ok.status_code == 204 and ok.content == b""
    assert await t.count(TaxItem) == 0
    rows = await t.audit("tax_item")
    assert {r.action for r in rows} == {AuditAction.CREATE, AuditAction.DELETE}
    (gone,) = [r for r in rows if r.action is AuditAction.DELETE]
    assert gone.before is not None and gone.before["vendor"] == "[redacted]"
    assert metric_points(metric_reader)["belegbot.tax_item.deleted"]
    assert (await delete(t, a, created["id"])).status_code == 404


async def test_delete_with_document_refused(t: T) -> None:
    a = await t.home()
    item = await t.item(a)
    r = await delete(t, a, item.id)
    assert r.status_code == 409 and r.json() == {"detail": "delete_not_allowed"}
    assert await t.count(TaxItem) == 1 and await t.audit() == []


async def _estimate_rows(t: T) -> int:
    return int((await t.session.execute(select(func.count()).select_from(Estimate))).scalar_one())


async def test_estimate_follows_payment_method_but_not_vendor_or_dates(t: T) -> None:
    built = await build(t.session, SINGLE)
    a = await t.home(built.household)
    scope = HouseholdScope(t.session, built.household.id)
    item = await add_item(
        built,
        {"category": "handwerkerleistung", "deductible": "3000.00", "labour_35a": "2000.00",
         "payment_method": "bank_transfer", "overridden": True},
    )  # fmt: skip
    await t.session.commit()
    today = date(2026, 6, 1)
    base = await build_summary(scope, 2025, today)
    assert await _estimate_rows(t) == 1

    r = await t.patch(a, item.id, {"version": 1, "vendor": "Neu", "invoice_date": "2025-01-02"})
    assert r.status_code == 200
    t.session.expire_all()
    same = await build_summary(scope, 2025, today)
    assert same["refund"] == base["refund"] and await _estimate_rows(t) == 1

    r = await t.patch(a, item.id, {"version": 2, "payment_method": "cash"})
    assert r.status_code == 200
    t.session.expire_all()  # the api wrote through its own session
    cash = await build_summary(scope, 2025, today)
    assert await _estimate_rows(t) == 2
    assert "cash_excluded" in {n["code"] for n in cash["notes"]}
    assert cash["refund"] != base["refund"]


async def test_document_less_item_counts_in_estimate(t: T) -> None:
    built = await build(t.session, SINGLE)
    a = await t.home(built.household)
    scope = HouseholdScope(t.session, built.household.id)
    before = await build_summary(scope, 2025, date(2026, 6, 1))
    r = await post(
        t, a, new_item(category="spenden", labour_share_35a=None, gross_amount="500.00",
                       deductible_amount="500.00", person_id=None, payment_method="bank_transfer"),
    )  # fmt: skip
    assert r.status_code == 201, r.text
    after = await build_summary(scope, 2025, date(2026, 6, 1))
    assert after["refund"] != before["refund"]


async def test_no_value_leaks(
    t: T, json_log: io.StringIO, metric_reader: InMemoryMetricReader
) -> None:
    tag = secrets.token_hex(6)
    vendor, reason = f"Vendor{tag}", f"Reason{tag}"
    a = await t.home()
    created = await post(t, a, new_item(vendor=vendor, reason=reason))
    item_id = created.json()["id"]
    bad = await t.patch(a, item_id, {"version": 1, "vendor": vendor * 20})
    assert bad.status_code == 422 and tag not in bad.text
    bad_date = await t.patch(a, item_id, {"version": 1, "payment_date": f"x{tag}"})
    assert tag not in bad_date.text
    assert (await t.patch(a, item_id, {"version": 1, "vendor": f"New{tag}"})).status_code == 200
    stale = await delete(t, a, item_id, version=1)
    assert stale.status_code == 409
    assert (await delete(t, a, item_id, version=2)).status_code == 204
    audit = json.dumps([[r.before, r.after] for r in await t.audit("tax_item")])
    logs = json_log.getvalue()
    attrs = [
        str(v)
        for pts in metric_points(metric_reader).values()
        for p in pts
        for v in (p.attributes or {}).values()
    ]
    assert '"tax_item.created_manual"' in logs and '"tax_item.deleted"' in logs
    assert tag not in audit and tag not in logs and not [v for v in attrs if tag in v]
