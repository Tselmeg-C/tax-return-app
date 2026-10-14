"""#45 "Neu verarbeiten": POST /documents/{id}/reprocess and POST /documents/reprocess."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import event, select, update

from app.auth.service import create_session
from app.db.models import AuditLog, Document, Job
from app.domain.enums import AttentionReason, DocumentStatus, JobStatus, UserRole
from tests.api.conftest import Home, T
from tests.documents.conftest import metric_points
from tests.domain.factories import make_user

DONE, NA, FAILED = DocumentStatus.DONE, DocumentStatus.NEEDS_ATTENTION, DocumentStatus.FAILED


async def member(t: T, home: Home) -> Home:
    user = await make_user(t.session, home.household, role=UserRole.MEMBER)
    token, _ = await create_session(t.session, user, now=t.clock.now(), max_age=timedelta(days=30))
    await t.session.commit()
    return Home(home.household, user, token)


async def one(t: T, home: Home, doc_id: Any, body: Any = None) -> Any:
    return await t.api.post(f"/documents/{doc_id}/reprocess", json=body, cookie=home.cookie)


async def many(t: T, home: Home, ids: list[Any], **kw: Any) -> Any:
    body = {"document_ids": [str(i) for i in ids], **kw}
    return await t.api.post("/documents/reprocess", json=body, cookie=home.cookie)


async def jobs(t: T, doc_id: uuid.UUID) -> list[Job]:
    stmt = select(Job).where(Job.document_id == doc_id).execution_options(populate_existing=True)
    return list((await t.session.execute(stmt)).scalars())


async def test_single_requeues_and_clears_state(t: T) -> None:
    a = await t.home()
    doc = await t.doc(a, status=NA, attention_reason=AttentionReason.SUM_MISMATCH)
    r = await one(t, a, doc.id)
    assert r.status_code == 202, r.text
    assert r.json()["document"]["status"] == "queued"
    row = await t.fresh(Document, doc.id)
    assert (row.status, row.attention_reason, row.error_kind) == (DocumentStatus.QUEUED, None, None)
    (job,) = await jobs(t, doc.id)
    assert (job.status, job.fresh) == (JobStatus.QUEUED, False)
    (audit,) = [x for x in await t.audit("document") if x.after.get("reprocess")]
    assert audit.actor_user_id == a.user.id and audit.after["reprocess"] == "single"
    assert audit.before["status"] == "needs_attention" and audit.after["status"] == "queued"


async def test_failed_and_fresh_flag(t: T) -> None:
    a = await t.home()
    doc = await t.doc(a, status=FAILED, error_kind="LLMUnavailable")
    r = await one(t, a, doc.id, {"fresh": True})
    assert r.status_code == 202
    (job,) = await jobs(t, doc.id)
    assert job.fresh is True
    assert (await t.fresh(Document, doc.id)).error_kind is None


async def test_member_may_reprocess_single_but_not_bulk(t: T) -> None:
    a = await t.home()
    m = await member(t, a)
    doc = await t.doc(a)
    assert (await many(t, m, [doc.id])).status_code == 403
    assert (await one(t, m, doc.id)).status_code == 202
    assert (await many(t, a, [doc.id])).status_code == 200  # owner; now busy
    assert (await many(t, a, [doc.id])).json()["skipped"] == [{"id": str(doc.id), "reason": "busy"}]


async def test_busy_statuses_409(t: T) -> None:
    a = await t.home()
    for status in (DocumentStatus.QUEUED, DocumentStatus.PROCESSING):
        doc = await t.doc(a, status=status)
        r = await one(t, a, doc.id)
        assert (r.status_code, r.json()["detail"]) == (409, "document_busy")
    assert await t.count(Job) == 0


async def test_second_request_is_busy(t: T) -> None:
    a = await t.home()
    doc = await t.doc(a)
    assert (await one(t, a, doc.id)).status_code == 202
    assert (await one(t, a, doc.id)).status_code == 409
    assert len(await jobs(t, doc.id)) == 1


async def test_overrides_skip_even_mixed_documents(t: T) -> None:
    a = await t.home()
    doc = await t.doc(a, status=NA, attention_reason=AttentionReason.SUM_MISMATCH)
    await t.item(a, doc, overridden_by_user=False)
    await t.item(a, doc, overridden_by_user=True)
    plain = await t.doc(a)
    r = await one(t, a, doc.id)
    assert (r.status_code, r.json()["detail"]) == (409, "has_overrides")
    row = await t.fresh(Document, doc.id)
    assert row.status is NA and row.attention_reason is AttentionReason.SUM_MISMATCH
    assert await jobs(t, doc.id) == []
    b = await many(t, a, [doc.id, plain.id])
    assert b.json() == {
        "queued": [str(plain.id)],
        "skipped": [{"id": str(doc.id), "reason": "has_overrides"}],
    }


async def test_other_household_is_not_found(t: T) -> None:
    a, b = await t.home(), await t.home()
    doc = await t.doc(b)
    assert (await one(t, a, doc.id)).status_code == 404
    r = await many(t, a, [doc.id])
    assert r.json() == {"queued": [], "skipped": [{"id": str(doc.id), "reason": "not_found"}]}
    assert (await t.fresh(Document, doc.id)).status is DONE


async def test_bulk_validation(t: T) -> None:
    a = await t.home()
    doc = await t.doc(a)
    assert (await many(t, a, [])).status_code == 422
    r = await many(t, a, [doc.id, doc.id])
    assert (r.status_code, r.json()) == (422, {"detail": "invalid_request"})
    r = await many(t, a, [uuid.uuid4() for _ in range(26)])
    assert (r.status_code, r.json()["detail"]) == (422, "too_many")
    assert (await many(t, a, [uuid.uuid4() for _ in range(25)])).status_code == 200


async def test_requires_csrf_and_session(t: T) -> None:
    a = await t.home()
    doc = await t.doc(a)
    r = await t.api.post(f"/documents/{doc.id}/reprocess", cookie=a.cookie, csrf=False)
    assert r.status_code == 403
    assert (await t.api.post(f"/documents/{doc.id}/reprocess")).status_code == 401


async def _age_audit(t: T) -> None:
    await t.session.execute(
        update(AuditLog).values(created_at=AuditLog.created_at - timedelta(hours=2))
    )


async def test_single_rate_limit_20_per_hour(t: T) -> None:
    a = await t.home()
    docs = [await t.doc(a) for _ in range(21)]
    for d in docs[:20]:
        assert (await one(t, a, d.id)).status_code == 202
    r = await one(t, a, docs[20].id)
    assert (r.status_code, r.json()["detail"]) == (429, "rate_limited")
    assert 1 <= int(r.headers["Retry-After"]) <= 3600
    await _age_audit(t)
    assert (await one(t, a, docs[20].id)).status_code == 202


async def test_bulk_rate_limit_3_per_hour(t: T) -> None:
    a = await t.home()
    docs = [await t.doc(a) for _ in range(4)]
    for d in docs[:3]:
        assert (await many(t, a, [d.id])).status_code == 200
    r = await many(t, a, [docs[3].id])
    assert (r.status_code, r.json()["detail"]) == (429, "rate_limited")
    assert "Retry-After" in r.headers
    await _age_audit(t)
    assert (await many(t, a, [docs[3].id])).status_code == 200


async def test_bulk_counts_once_per_request_and_one_audit_row_per_document(t: T) -> None:
    a = await t.home()
    docs = [await t.doc(a) for _ in range(5)]
    assert len((await many(t, a, [d.id for d in docs])).json()["queued"]) == 5
    rows = [x for x in await t.audit("document") if x.after.get("reprocess")]
    assert len(rows) == 5 and len({x.after["reprocess"] for x in rows}) == 1


async def test_metrics_and_no_filename_in_logs(t: T, json_log: Any, metric_reader: Any) -> None:
    a = await t.home()
    doc = await t.doc(a, original_filename="geheim-rechnung.pdf")
    await one(t, a, doc.id)
    await one(t, a, doc.id)
    out = json_log.getvalue()
    assert "geheim" not in out and "documents.reprocess" in out
    points = metric_points(metric_reader)["belegbot.documents.reprocess"]
    assert {p.attributes["outcome"] for p in points} == {"queued", "busy"}


async def test_bulk_locks_documents_in_id_order_after_the_advisory_lock(t: T) -> None:
    a = await t.home()
    docs = [await t.doc(a) for _ in range(3)]
    ids = [d.id for d in docs]
    seen: list[tuple[str, Any]] = []

    def grab(conn: Any, cursor: Any, statement: str, params: Any, *rest: Any) -> None:
        seen.append((statement, params))

    engine = t.session.get_bind()
    event.listen(engine, "before_cursor_execute", grab)
    try:
        r = await many(t, a, list(reversed(ids)))
    finally:
        event.remove(engine, "before_cursor_execute", grab)
    assert r.status_code == 200
    texts = [s for s, _ in seen]
    adv = next(i for i, s in enumerate(texts) if "pg_advisory_xact_lock" in s)
    counts = [i for i, s in enumerate(texts) if "audit_log" in s and "SELECT" in s]
    assert counts and adv < min(counts)
    locks = [p for s, p in seen if "FROM document" in s and s.rstrip().endswith("FOR UPDATE")]
    flat = [str(x) for p in locks for x in (p if isinstance(p, (tuple, list)) else p.values())]
    locked = [uuid.UUID(x) for x in flat if x in {str(i) for i in ids}]
    assert locked == sorted(ids)
