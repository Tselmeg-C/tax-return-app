"""#10 "API: edit": PATCH /tax-items/{id}, POST /documents/{id}/tax-items, delete audit."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.exc import DBAPIError

from app import tax_items as service
from app.db.models import AuditLog, Document, Job, TaxItem
from app.domain.enums import (
    ActorType,
    AttentionReason,
    AuditAction,
    Category,
    DocumentStatus,
    JobKind,
    JobStatus,
)
from app.tax_params import load_params
from tests.api.conftest import T
from tests.documents.conftest import metric_points
from tests.domain.factories import make_person

NA = DocumentStatus.NEEDS_ATTENTION
HW = Category.HANDWERKERLEISTUNG


async def test_patch_category_recomputes_mapping_and_audits_once(t: T) -> None:
    a = await t.home()
    item = await t.item(a)
    before_audit = len(await t.audit())
    r = await t.patch(a, item.id, {"version": 1, "category": "krankheitskosten"})
    assert r.status_code == 200, r.text
    body = r.json()
    entry = load_params(2025).mapping[Category.KRANKHEITSKOSTEN]
    assert body["category"] == "krankheitskosten"
    assert (body["anlage"], body["zeile"]) == (entry.anlage.value, entry.zeile)
    assert body["version"] == 2 and body["overridden_by_user"] is True
    rows = await t.audit()
    assert len(rows) == before_audit + 1
    row = rows[-1]
    assert (row.entity, row.action, row.actor_type) == ("tax_item", AuditAction.UPDATE, "user")
    assert row.actor_user_id == a.user.id and row.entity_id == item.id
    assert row.before is not None and row.after is not None
    assert set(row.before) == set(row.after)
    assert {"category", "anlage", "zeile", "overridden_by_user", "version"} <= set(row.after)
    assert not set(row.after) & {"gross_amount", "deductible_amount", "year", "vendor"}
    assert row.before["category"] == "wk_arbeitsmittel"


async def test_passt_so_then_noop(t: T) -> None:
    a = await t.home()
    item = await t.item(a)
    r = await t.patch(a, item.id, {"version": 1})
    assert r.status_code == 200 and r.json()["overridden_by_user"] is True
    assert r.json()["version"] == 2
    assert len(await t.audit("tax_item")) == 1
    again = await t.patch(a, item.id, {"version": 2})
    assert again.status_code == 200 and again.json()["version"] == 2
    assert len(await t.audit("tax_item")) == 1


async def test_patch_resolves_needs_attention(t: T) -> None:
    a = await t.home()
    doc = await t.doc(a, status=NA, attention_reason=AttentionReason.LOW_CONFIDENCE)
    item = await t.item(a, doc)
    r = await t.patch(a, item.id, {"version": 1})
    assert r.status_code == 200
    assert r.json()["document"]["status"] == "done"
    assert r.json()["document"]["attention_reason"] is None
    stored = await t.fresh(Document, doc.id)
    assert stored.status is DocumentStatus.DONE and stored.attention_reason is None
    assert len(await t.audit("tax_item")) == 1
    (doc_row,) = await t.audit("document")
    assert doc_row.after is not None
    assert doc_row.after["status"] == "done" and doc_row.after["attention_reason"] is None


CASES: list[tuple[str, dict[str, Any], dict[str, Any], int, str, str]] = [
    # (name, item values, body, status, code, field)
    ("comma", {}, {"gross_amount": "12,50"}, 422, "invalid_amount", "gross_amount"),
    ("one decimal", {}, {"deductible_amount": "12.5"}, 422, "invalid_amount", "deductible_amount"),
    ("number", {}, {"gross_amount": 12.5}, 422, "invalid_amount", "gross_amount"),
    ("year", {}, {"year": 2024}, 422, "unsupported_year", "year"),
    (
        "relevant irrelevant",
        {"category": Category.IRRELEVANT, "is_relevant": False, "deductible_amount": 0},
        {"is_relevant": True},
        422,
        "category_irrelevant",
        "is_relevant",
    ),
    (
        "irrelevant deductible",
        {},
        {"is_relevant": False, "deductible_amount": "5.00"},
        422,
        "irrelevant_not_deductible",
        "deductible_amount",
    ),
    (
        "exceeds",
        {"gross_amount": Decimal("312.40")},
        {"deductible_amount": "400.00"},
        422,
        "deductible_exceeds_gross",
        "deductible_amount",
    ),
    (
        "sign",
        {"gross_amount": Decimal("312.40")},
        {"deductible_amount": "-5.00"},
        422,
        "deductible_exceeds_gross",
        "deductible_amount",
    ),
    (
        "labour negative",
        {"category": HW},
        {"labour_share_35a": "-1.00"},
        422,
        "labour_share_invalid",
        "labour_share_35a",
    ),
    (
        "labour too big",
        {"category": HW},
        {"labour_share_35a": "100.01"},
        422,
        "labour_share_invalid",
        "labour_share_35a",
    ),
    (
        "labour not allowed",
        {},
        {"labour_share_35a": "10.00"},
        422,
        "labour_share_not_allowed",
        "labour_share_35a",
    ),
]


@pytest.mark.parametrize(
    ("values", "body", "status", "code", "field"),
    [c[1:] for c in CASES],
    ids=[c[0] for c in CASES],
)
async def test_validation(
    t: T,
    values: dict[str, Any],
    body: dict[str, Any],
    status: int,
    code: str,
    field: str,
) -> None:
    a = await t.home()
    item = await t.item(a, **values)
    r = await t.patch(a, item.id, {"version": 1, **body})
    assert r.status_code == status, r.text
    assert r.json() == {"detail": code, "field": field}
    for value in body.values():
        assert str(value) not in r.text
    stored = await t.fresh(TaxItem, item.id)
    assert stored.version == 1 and stored.overridden_by_user is False
    assert await t.audit() == []


@pytest.mark.parametrize("key", ["anlage", "overridden_by_user", "zeile", "vendor"])
async def test_non_editable_keys_refused(t: T, key: str) -> None:
    a = await t.home()
    item = await t.item(a)
    r = await t.patch(a, item.id, {"version": 1, key: "n"})
    assert r.status_code == 422 and r.json() == {"detail": "invalid_request"}


async def test_derived_values(t: T) -> None:
    a = await t.home()
    item = await t.item(a)
    r = await t.patch(a, item.id, {"version": 1, "is_relevant": False})
    assert r.status_code == 200 and r.json()["deductible_amount"] == "0.00"

    hw = await t.item(a, category=HW, labour_share_35a=Decimal("60.00"))
    r = await t.patch(a, hw.id, {"version": 1, "category": "wk_arbeitsmittel"})
    assert r.status_code == 200 and r.json()["labour_share_35a"] is None

    person = await make_person(t.session, a.household)
    await t.session.commit()
    hw2 = await t.item(a, category=HW)
    r = await t.patch(a, hw2.id, {"version": 1, "person_id": str(person.id)})
    assert r.json() == {"detail": "person_not_allowed", "field": "person_id"}

    mine = await t.item(a, person_id=person.id)
    r = await t.patch(a, mine.id, {"version": 1, "category": "handwerkerleistung"})
    assert r.status_code == 200 and r.json()["person_id"] is None

    r = await t.patch(a, mine.id, {"version": 2, "category": "irrelevant"})
    assert r.status_code == 200
    assert r.json()["is_relevant"] is False and r.json()["deductible_amount"] == "0.00"
    assert r.json()["anlage"] is None and r.json()["zeile"] is None


async def test_person_checks(t: T) -> None:
    a, b = await t.home(), await t.home()
    other = await make_person(t.session, b.household)
    mine = await make_person(t.session, a.household, first_name="Alex")
    await t.session.commit()
    item = await t.item(a)
    r = await t.patch(a, item.id, {"version": 1, "person_id": str(other.id)})
    assert r.status_code == 422 and r.json() == {"detail": "unknown_person", "field": "person_id"}
    r = await t.patch(a, item.id, {"version": 1, "person_id": str(uuid.uuid4())})
    assert r.json() == {"detail": "unknown_person", "field": "person_id"}
    r = await t.patch(a, item.id, {"version": 1, "person_id": str(mine.id)})
    assert r.status_code == 200 and r.json()["person_id"] == str(mine.id)
    r = await t.patch(a, item.id, {"version": 2, "person_id": None})
    assert r.status_code == 200 and r.json()["person_id"] is None


async def test_unsupported_stored_year_can_be_marked_irrelevant(t: T) -> None:
    a = await t.home()
    item = await t.item(a, year=2024, anlage=None, zeile=None)
    r = await t.patch(a, item.id, {"version": 1, "is_relevant": False, "year": 2024})
    assert r.status_code == 200, r.text
    assert r.json()["year"] == 2024 and r.json()["is_relevant"] is False


async def test_sequential_conflict(t: T) -> None:
    a = await t.home()
    member = await t.home(a.household)
    item = await t.item(a)
    first = await t.patch(a, item.id, {"version": 1, "deductible_amount": "80.00"})
    assert first.status_code == 200
    second = await t.patch(member, item.id, {"version": 1, "deductible_amount": "90.00"})
    assert second.status_code == 409
    assert second.json()["detail"] == "version_conflict"
    assert second.json()["tax_item"]["version"] == 2
    assert second.json()["tax_item"]["deductible_amount"] == "80.00"
    assert (await t.fresh(TaxItem, item.id)).deductible_amount == Decimal("80.00")
    assert len(await t.audit("tax_item")) == 1


async def test_busy_document(t: T) -> None:
    a = await t.home()
    doc = await t.doc(a, status=DocumentStatus.PROCESSING)
    t.session.add(
        Job(
            household_id=a.household.id,
            kind=JobKind.PROCESS_DOCUMENT,
            document_id=doc.id,
            status=JobStatus.RUNNING,
            max_attempts=5,
            locked_by="test-worker",
            locked_until=datetime.now(UTC) + timedelta(minutes=5),
        )
    )
    await t.session.commit()
    item = await t.item(a, doc)
    r = await t.patch(a, item.id, {"version": 1, "deductible_amount": "1.00"})
    assert r.status_code == 409 and r.json() == {"detail": "document_busy"}
    stored = await t.fresh(TaxItem, item.id)
    assert stored.version == 1 and stored.deductible_amount == Decimal("100.00")
    r = await t.post_item(a, doc.id, _manual())
    assert r.status_code == 409 and r.json() == {"detail": "document_busy"}


async def test_other_household_is_not_found(t: T) -> None:
    a, b = await t.home(), await t.home()
    item = await t.item(b)
    foreign = await t.patch(a, item.id, {"version": 1})
    random = await t.patch(a, uuid.uuid4(), {"version": 1})
    assert foreign.status_code == random.status_code == 404
    assert foreign.json() == random.json() == {"detail": "not_found"}
    doc = await t.doc(b, status=NA, attention_reason=AttentionReason.UNREADABLE)
    r = await t.post_item(a, doc.id, _manual())
    assert r.status_code == 404 and r.json() == {"detail": "not_found"}


def _manual(**kw: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "category": "handwerkerleistung",
        "is_relevant": True,
        "gross_amount": "312.40",
        "deductible_amount": "180.00",
        "labour_share_35a": "180.00",
        "year": 2025,
    }
    body.update(kw)
    return body


async def test_manual_item(t: T) -> None:
    a = await t.home()
    doc = await t.doc(a, status=NA, attention_reason=AttentionReason.UNREADABLE)
    r = await t.post_item(a, doc.id, _manual())
    assert r.status_code == 201, r.text
    body = r.json()
    entry = load_params(2025).mapping[HW]
    assert body["overridden_by_user"] is True and body["version"] == 1
    assert (body["anlage"], body["zeile"]) == (entry.anlage.value, entry.zeile)
    assert body["confidence"] is None and body["reason"] is None
    assert body["labour_share_35a"] == "180.00" and body["document"]["status"] == "done"
    stored = await t.fresh(TaxItem, uuid.UUID(body["id"]))
    assert stored.extraction_id is None and stored.document_id == doc.id
    actions = {(row.entity, row.action) for row in await t.audit()}
    assert actions == {("tax_item", AuditAction.CREATE), ("document", AuditAction.UPDATE)}
    assert len(await t.audit()) == 2

    again = await t.post_item(a, doc.id, _manual())
    assert again.status_code == 409 and again.json() == {"detail": "tax_item_exists"}


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (NA, AttentionReason.DOC_TYPE_NOT_SUPPORTED),
        (NA, AttentionReason.POSSIBLE_DUPLICATE),
        (DocumentStatus.DONE, None),
        (DocumentStatus.FAILED, None),
    ],
)
async def test_manual_item_not_allowed(
    t: T, status: DocumentStatus, reason: AttentionReason | None
) -> None:
    a = await t.home()
    doc = await t.doc(a, status=status, attention_reason=reason)
    r = await t.post_item(a, doc.id, _manual())
    assert r.status_code == 409 and r.json() == {"detail": "manual_item_not_allowed"}
    assert await t.count(TaxItem) == 0


async def test_manual_item_validation(t: T) -> None:
    a = await t.home()
    doc = await t.doc(a, status=NA, attention_reason=AttentionReason.MULTIPLE_DOCUMENTS)
    r = await t.post_item(a, doc.id, _manual(year=2024))
    assert r.json() == {"detail": "unsupported_year", "field": "year"}
    r = await t.post_item(a, doc.id, _manual(gross_amount="1.234,56"))
    assert r.json() == {"detail": "invalid_amount", "field": "gross_amount"}
    r = await t.post_item(a, doc.id, {"category": "spenden"})
    assert r.status_code == 422 and r.json() == {"detail": "invalid_request"}
    assert await t.count(TaxItem) == 0 and await t.audit() == []


async def test_delete_document_with_overridden_item(t: T) -> None:
    a = await t.home()
    doc = await t.doc(a)
    item = await t.item(a, doc)
    assert (await t.patch(a, item.id, {"version": 1, "deductible_amount": "50.00"})).is_success
    r = await t.api.request("DELETE", f"/documents/{doc.id}", cookie=a.cookie)
    assert r.status_code == 204
    assert await t.fresh(TaxItem, item.id) is None
    deletes = sorted(
        (row for row in await t.audit() if row.action is AuditAction.DELETE),
        key=lambda row: row.entity != "tax_item",
    )
    assert [(row.entity, row.entity_id) for row in deletes] == [
        ("tax_item", item.id),
        ("document", doc.id),
    ]
    assert deletes[0].before is not None
    assert deletes[0].before["deductible_amount"] == "50.00"
    assert deletes[0].before["overridden_by_user"] is True
    assert deletes[0].actor_type is ActorType.USER


async def test_rolled_back_patch_counts_nothing(
    t: T, monkeypatch: pytest.MonkeyPatch, metric_reader: Any
) -> None:
    a = await t.home()
    doc = await t.doc(a, status=NA, attention_reason=AttentionReason.LOW_CONFIDENCE)
    item = await t.item(a, doc)
    real = service.record

    async def failing(*args: Any, **kw: Any) -> AuditLog | None:
        row = await real(*args, **kw)
        if kw.get("entity") == "document":
            raise DBAPIError("injected", {}, Exception())
        return row

    monkeypatch.setattr(service, "record", failing)
    with pytest.raises(DBAPIError):
        await t.patch(a, item.id, {"version": 1, "category": "spenden"})
    assert await t.audit() == []
    stored = await t.fresh(TaxItem, item.id)
    assert stored.version == 1 and stored.category is Category.WK_ARBEITSMITTEL
    points = metric_points(metric_reader)
    assert not [name for name in points if name.startswith("belegbot.tax_item")]


def _attrs(points: dict[str, list[Any]], name: str) -> list[tuple[dict[str, Any], int]]:
    return [(dict(p.attributes), p.value) for p in points.get(name, [])]


async def test_override_metrics(t: T, metric_reader: Any) -> None:
    a = await t.home()
    item = await t.item(a)
    body = {"version": 1, "category": "spenden", "deductible_amount": "60.00"}
    assert (await t.patch(a, item.id, body)).status_code == 200
    points = metric_points(metric_reader)
    overrides = _attrs(points, "belegbot.tax_item.overrides")
    assert sorted(overrides, key=lambda x: x[0]["field"]) == [
        ({"field": "category", "category": "wk_arbeitsmittel", "origin": "pipeline"}, 1),
        ({"field": "deductible_amount", "category": "wk_arbeitsmittel", "origin": "pipeline"}, 1),
    ]
    assert _attrs(points, "belegbot.tax_item.reviews") == [
        ({"outcome": "changed", "origin": "pipeline"}, 1)
    ]

    assert (await t.patch(a, item.id, {"version": 2, "gross_amount": "70.00"})).is_success
    other = await t.item(a)
    assert (await t.patch(a, other.id, {"version": 1, "deductible_amount": "100.00"})).is_success
    points = metric_points(metric_reader)
    overrides = _attrs(points, "belegbot.tax_item.overrides")
    assert ({"field": "gross_amount", "category": "spenden", "origin": "user"}, 1) in overrides
    reviews = dict(
        (tuple(sorted(x.items())), v) for x, v in _attrs(points, "belegbot.tax_item.reviews")
    )
    assert reviews == {
        (("origin", "pipeline"), ("outcome", "changed")): 1,
        (("origin", "user"), ("outcome", "changed")): 1,
        (("origin", "pipeline"), ("outcome", "confirmed")): 1,
    }
    assert len(overrides) == 3  # the unchanged deductible amount counts no override

    doc = await t.doc(a, status=NA, attention_reason=AttentionReason.UNREADABLE)
    assert (await t.post_item(a, doc.id, _manual())).status_code == 201
    points = metric_points(metric_reader)
    assert _attrs(points, "belegbot.tax_item.manual_items") == [
        ({"attention_reason": "unreadable"}, 1)
    ]
