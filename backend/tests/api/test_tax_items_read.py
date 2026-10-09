"""#10 "API: read": `GET /tax-items`, `GET /documents?without_tax_item=true`, `GET /persons`,
`GET /meta/labels`."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.domain.enums import (
    LABELS_DE,
    Anlage,
    AttentionReason,
    Category,
    DocumentStatus,
    PersonKind,
)
from tests.api.conftest import T
from tests.domain.factories import make_person

NA = DocumentStatus.NEEDS_ATTENTION


async def test_list_year_newest_first_and_household_only(t: T) -> None:
    a, b = await t.home(), await t.home()
    base = datetime(2026, 1, 10, tzinfo=UTC)
    old = await t.doc(a, created_at=base)
    new = await t.doc(
        a, created_at=base + timedelta(days=1), status=NA, attention_reason="low_confidence"
    )
    i_old = await t.item(a, old, gross_amount=Decimal("312.40"), deductible_amount=Decimal("0"))
    i_new = await t.item(a, new)
    await t.item(a, year=2026)
    await t.item(b)  # household B, 2025

    r = await t.get(a, "/tax-items?year=2025")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 2
    assert [i["id"] for i in body["items"]] == [str(i_new.id), str(i_old.id)]
    first, second = body["items"]
    assert first["document"]["attention_reason"] == "low_confidence"
    assert second["gross_amount"] == "312.40" and second["deductible_amount"] == "0.00"
    assert first["version"] == 1 and first["overridden_by_user"] is False
    assert set(first) >= {"vendor", "confidence", "person_id", "labour_share_35a", "anlage"}


async def test_filters(t: T) -> None:
    a = await t.home()
    relevant = await t.item(a, confidence=Decimal("0.900"))
    irrelevant = await t.item(
        a, is_relevant=False, deductible_amount=Decimal("0"), category=Category.IRRELEVANT
    )
    uncertain = await t.item(
        a, is_relevant=False, deductible_amount=Decimal("0"), confidence=Decimal("0.300")
    )
    overridden_low = await t.item(
        a,
        is_relevant=False,
        deductible_amount=Decimal("0"),
        confidence=Decimal("0.300"),
        overridden_by_user=True,
    )
    attention_doc = await t.doc(a, status=NA, attention_reason="unsupported_year")
    attention_2024 = await t.item(
        a, attention_doc, year=2024, is_relevant=False, deductible_amount=Decimal("0")
    )

    async def ids(query: str) -> set[str]:
        r = await t.get(a, f"/tax-items?{query}")
        assert r.status_code == 200, r.text
        assert r.json()["total"] == len(r.json()["items"])
        return {i["id"] for i in r.json()["items"]}

    def s(*items: object) -> set[str]:
        return {str(i.id) for i in items}  # type: ignore[attr-defined]

    assert await ids("year=2025") == s(relevant, irrelevant, uncertain, overridden_low)
    assert await ids("year=2025&filter=all") == await ids("year=2025")
    assert await ids("year=2025&filter=relevant") == s(relevant)
    assert await ids("year=2025&filter=uncertain") == s(uncertain)
    assert await ids("year=2025&filter=manual") == s(overridden_low)
    assert await ids("year=2025&filter=attention") == s(attention_2024)
    assert await ids("filter=attention") == s(attention_2024)


async def test_document_id_any_year_and_other_household(t: T) -> None:
    a, b = await t.home(), await t.home()
    doc = await t.doc(a)
    item = await t.item(a, doc, year=2024)
    r = await t.get(a, f"/tax-items?document_id={doc.id}")
    assert [i["id"] for i in r.json()["items"]] == [str(item.id)]
    r = await t.get(b, f"/tax-items?document_id={doc.id}")
    assert r.status_code == 200 and r.json() == {"items": [], "total": 0}


async def test_query_validation_and_paging(t: T) -> None:
    a = await t.home()
    base = datetime(2026, 1, 10, tzinfo=UTC)
    items = [await t.item(a, await t.doc(a, created_at=base + timedelta(hours=n))) for n in (1, 2)]
    for query, code in [
        ("year=2024", "unsupported_year"),
        ("year=2025&filter=foo", "invalid_request"),
        ("year=2025&limit=201", "invalid_request"),
        ("year=2025&limit=0", "invalid_request"),
        ("year=2025&offset=-1", "invalid_request"),
        ("", "invalid_request"),
    ]:
        r = await t.get(a, f"/tax-items?{query}")
        assert r.status_code == 422 and r.json() == {"detail": code}, query
    r = await t.get(a, "/tax-items?year=2025&limit=1&offset=1")
    assert [i["id"] for i in r.json()["items"]] == [str(items[0].id)]
    assert r.json()["total"] == 2


async def test_documents_without_tax_item(t: T) -> None:
    a = await t.home()
    queued = await t.doc(a, status=DocumentStatus.QUEUED)
    processing = await t.doc(a, status=DocumentStatus.PROCESSING)
    failed = await t.doc(a, status=DocumentStatus.FAILED, error_kind="ValueError")
    lonely = await t.doc(a, status=NA, attention_reason="multiple_documents")
    with_item = await t.doc(a, status=NA, attention_reason="low_confidence")
    await t.item(a, with_item)
    await t.item(a)  # done document with item

    r = await t.get(a, "/documents?without_tax_item=true")
    assert r.status_code == 200, r.text
    got = {d["id"]: d for d in r.json()["documents"]}
    assert set(got) == {str(d.id) for d in (queued, processing, failed, lonely)}
    assert got[str(lonely.id)]["attention_reason"] == "multiple_documents"
    assert got[str(queued.id)]["attention_reason"] is None
    everything = (await t.get(a, "/documents")).json()["documents"]
    assert len(everything) == 6 and all("attention_reason" in d for d in everything)


async def test_persons(t: T) -> None:
    a, b = await t.home(), await t.home()
    kid = await make_person(
        t.session, a.household, kind=PersonKind.CHILD, first_name="Anna", dob=date(2020, 1, 1)
    )
    bea = await make_person(t.session, a.household, first_name="Bea", last_name="Muster")
    alex = await make_person(t.session, a.household, first_name="Alex")
    await make_person(t.session, b.household, first_name="Zoe")
    await t.session.commit()
    r = await t.get(a, "/persons")
    assert r.status_code == 200
    keys = ("id", "kind", "first_name", "last_name")  # #13 adds fields (additive only)
    assert [{k: p[k] for k in keys} for p in r.json()] == [
        {"id": str(alex.id), "kind": "adult", "first_name": "Alex", "last_name": None},
        {"id": str(bea.id), "kind": "adult", "first_name": "Bea", "last_name": "Muster"},
        {"id": str(kid.id), "kind": "child", "first_name": "Anna", "last_name": None},
    ]


async def test_meta_labels(t: T) -> None:
    a = await t.home()
    body = (await t.get(a, "/meta/labels")).json()
    cats = {c["code"]: c for c in body["categories"]}
    assert set(cats) == {c.value for c in Category}
    assert cats["handwerkerleistung"] == {
        "code": "handwerkerleistung",
        "label": "Handwerkerleistung",
        "group": "haushaltsnahe",
        "group_label": "Haushaltsnahe Aufwendungen (§35a)",
    }
    for key, enum in [
        ("anlagen", Anlage),
        ("attention_reasons", AttentionReason),
    ]:
        assert {x["code"]: x["label"] for x in body[key]} == {
            m.value: LABELS_DE[enum][m] for m in enum
        }
    assert {x["code"] for x in body["payment_methods"]} >= {"cash", "unknown"}
    assert {x["code"] for x in body["doc_types"]} >= {"generic_bill"}
    assert body["supported_years"] == [2025, 2026]


async def test_new_routes_need_a_session(t: T) -> None:
    some = uuid.uuid4()
    for method, path in [
        ("GET", "/tax-items?year=2025"),
        ("PATCH", f"/tax-items/{some}"),
        ("POST", f"/documents/{some}/tax-items"),
        ("GET", "/persons"),
        ("GET", "/meta/labels"),
    ]:
        r = await t.api.request(method, path, json={})
        assert r.status_code == 401, path
