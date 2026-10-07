"""List, poll, download, delete; family members and documents (#6 criteria)."""

from __future__ import annotations

import io
import re
import uuid
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from app.auth.cli import run as cli_run
from app.auth.service import create_session
from app.db.models import (
    AppUser,
    AuditLog,
    Document,
    Extraction,
    Household,
    Job,
    Person,
    TaxItem,
)
from app.db.seed import seed, seed_id
from app.domain.enums import AuditAction, DocumentStatus, JobStatus, UserRole
from tests.documents import files
from tests.documents.conftest import Docs
from tests.domain.factories import make_extraction, make_person, make_tax_item

NOT_FOUND = {"detail": "not_found"}


async def _upload(docs: Docs, cookie: str, data: bytes | None = None, **kw: Any) -> dict[str, Any]:
    response = await docs.upload(data or files.pdf(), cookie, **kw)
    assert response.status_code == 201, response.text
    doc: dict[str, Any] = response.json()["document"]
    return doc


async def test_list_scoped_sorted_filtered(docs: Docs) -> None:
    user, cookie = await docs.user()
    _, other = await docs.user()
    ids = []
    for _ in range(3):
        ids.append((await _upload(docs, cookie))["id"])
        docs.clock.advance(timedelta(seconds=1))
    await _upload(docs, other)
    await docs.session.execute(
        update(Document).where(Document.id == ids[0]).values(status=DocumentStatus.PROCESSING)
    )
    await docs.session.execute(
        update(Document).where(Document.id == ids[1]).values(status=DocumentStatus.DONE)
    )
    await docs.session.commit()

    listed = (await docs.api.get("/documents", cookie=cookie)).json()["documents"]
    assert {d["id"] for d in listed} == set(ids)
    created = [d["created_at"] for d in listed]
    assert created == sorted(created, reverse=True)
    filtered = (await docs.api.get("/documents?status=queued,processing", cookie=cookie)).json()
    assert {d["id"] for d in filtered["documents"]} == {ids[0], ids[2]}
    for bad in ("limit=0", "limit=201", "status=nope"):
        assert (await docs.api.get(f"/documents?{bad}", cookie=cookie)).status_code == 422


async def test_download(docs: Docs) -> None:
    _, cookie = await docs.user()
    data = files.png()
    doc = await _upload(docs, cookie, data)
    response = await docs.api.get(f"/documents/{doc['id']}/file", cookie=cookie)
    assert response.status_code == 200
    assert response.content == data
    assert response.headers["content-type"] == "image/png"
    assert response.headers["content-length"] == str(len(data))
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "private, no-store"
    disposition = response.headers["content-disposition"]
    match = re.fullmatch(r'inline; filename="(.+)"', disposition)
    assert match and re.fullmatch(
        r"^beleg-[0-9a-f]{8}\.(jpg|png|gif|webp|tif|bmp|heic|heif|avif|jp2|jxl|pdf)$",
        match.group(1),
    )


async def test_download_names(docs: Docs) -> None:
    _, cookie = await docs.user()
    named = await _upload(docs, cookie, filename="UTF-8''M%C3%BCller%20%22Rechnung%22.pdf")
    response = await docs.api.get(f"/documents/{named['id']}/file", cookie=cookie)
    assert response.headers["content-disposition"] == (
        'inline; filename="M_ller _Rechnung_.pdf"; '
        "filename*=UTF-8''M%C3%BCller%20%22Rechnung%22.pdf"
    )
    for value in response.headers.values():
        assert "\r" not in value and "\n" not in value
    wrong = await _upload(docs, cookie, filename="UTF-8''rechnung.jpg")
    response = await docs.api.get(f"/documents/{wrong['id']}/file", cookie=cookie)
    assert 'filename="rechnung.jpg.pdf"' in response.headers["content-disposition"]


async def test_other_household_gets_404(docs: Docs) -> None:
    _, cookie_a = await docs.user()
    _, cookie_b = await docs.user()
    doc = await _upload(docs, cookie_a)
    random_id = uuid.uuid4()
    for path in ("/documents/{}", "/documents/{}/file"):
        theirs = await docs.api.get(path.format(doc["id"]), cookie=cookie_b)
        unknown = await docs.api.get(path.format(random_id), cookie=cookie_b)
        assert theirs.status_code == unknown.status_code == 404
        assert theirs.json() == unknown.json() == NOT_FOUND
    theirs = await docs.api.request("DELETE", f"/documents/{doc['id']}", cookie=cookie_b)
    unknown = await docs.api.request("DELETE", f"/documents/{random_id}", cookie=cookie_b)
    assert theirs.status_code == unknown.status_code == 404
    assert theirs.json() == unknown.json() == NOT_FOUND
    assert (await docs.api.get("/documents/not-a-uuid/file", cookie=cookie_a)).status_code == 422
    assert (await docs.api.get(f"/documents/{doc['id']}/file")).status_code == 401
    assert (await docs.api.get(f"/documents/{doc['id']}", cookie=cookie_a)).status_code == 200


async def test_seed_document_without_file(docs: Docs) -> None:
    await seed(docs.session)
    await docs.session.commit()
    owner = await docs.session.get(AppUser, seed_id("user/owner"))
    assert owner is not None
    cookie, _ = await create_session(
        docs.session, owner, now=docs.clock.now(), max_age=timedelta(days=1)
    )
    await docs.session.commit()
    seed_doc = seed_id("document/1")
    response = await docs.api.get(f"/documents/{seed_doc}/file", cookie=cookie)
    assert response.status_code == 404
    assert response.json() == {"detail": "file_not_found"}
    deleted = await docs.api.request("DELETE", f"/documents/{seed_doc}", cookie=cookie)
    assert deleted.status_code == 204


async def test_delete(docs: Docs) -> None:
    user, cookie = await docs.user()
    doc = await _upload(docs, cookie)
    directory = docs.root / f"households/{user.household_id}/documents/{doc['id']}"
    assert directory.is_dir()
    response = await docs.api.request("DELETE", f"/documents/{doc['id']}", cookie=cookie)
    assert response.status_code == 204
    assert await docs.session.get(Document, uuid.UUID(doc["id"])) is None
    jobs = await docs.session.execute(select(Job).where(Job.document_id == doc["id"]))
    assert jobs.first() is None
    assert not directory.exists()
    audits = (
        (
            await docs.session.execute(
                select(AuditLog).where(
                    AuditLog.entity_id == doc["id"], AuditLog.action == AuditAction.DELETE
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(audits) == 1
    before = audits[0].before
    assert before is not None and before["id"] == doc["id"]
    assert all(not isinstance(v, bytes | bytearray) for v in before.values())


async def test_delete_running_is_409(docs: Docs) -> None:
    _, cookie = await docs.user()
    doc = await _upload(docs, cookie)
    await docs.session.execute(
        update(Job)
        .where(Job.document_id == doc["id"])
        .values(status=JobStatus.RUNNING, locked_by="w", locked_until=func.now())
    )
    await docs.session.execute(
        update(Document).where(Document.id == doc["id"]).values(status=DocumentStatus.PROCESSING)
    )
    await docs.session.commit()
    response = await docs.api.request("DELETE", f"/documents/{doc['id']}", cookie=cookie)
    assert response.status_code == 409
    assert response.json() == {"detail": "document_busy"}
    assert await docs.session.get(Document, uuid.UUID(doc["id"])) is not None


async def test_delete_cascades_extraction_and_tax_item(docs: Docs) -> None:
    user, cookie = await docs.user()
    doc_json = await _upload(docs, cookie)
    doc = await docs.session.get(Document, uuid.UUID(doc_json["id"]))
    assert doc is not None
    hh = await docs.session.get(Household, user.household_id)
    assert hh is not None
    ext = await make_extraction(docs.session, hh, doc)
    item = await make_tax_item(docs.session, hh, document_id=doc.id, extraction_id=ext.id)
    await docs.session.commit()
    response = await docs.api.request("DELETE", f"/documents/{doc.id}", cookie=cookie)
    assert response.status_code == 204
    docs.session.expunge_all()
    assert await docs.session.get(Extraction, ext.id) is None
    assert await docs.session.get(TaxItem, item.id) is None


# --- family members (Decision 14) ----------------------------------------------------


async def test_disabled_uploader_keeps_documents(docs: Docs) -> None:
    uploader, cookie_u = await docs.user()
    hh = await docs.session.get(Household, uploader.household_id)
    assert hh is not None
    second, cookie_2 = await docs.user(hh, role=UserRole.MEMBER)
    data = files.pdf()
    doc_json = await _upload(docs, cookie_u, data)
    doc = await docs.session.get(Document, uuid.UUID(doc_json["id"]))
    assert doc is not None
    ext = await make_extraction(docs.session, hh, doc)
    item = await make_tax_item(docs.session, hh, document_id=doc.id, extraction_id=ext.id)
    await docs.session.commit()

    out = io.StringIO()
    code = await cli_run(
        ["disable", "--email", uploader.email], docs.session, out=out, clock=docs.clock
    )
    assert code == 0
    await docs.session.commit()
    docs.session.expunge_all()

    assert (await docs.session.get(Document, doc.id)) is not None
    assert (await docs.session.get(Extraction, ext.id)) is not None
    assert (await docs.session.get(TaxItem, item.id)) is not None
    jobs = (await docs.session.execute(select(Job).where(Job.document_id == doc.id))).scalars()
    assert [j.status for j in jobs] == [JobStatus.QUEUED]

    listed = (await docs.api.get("/documents", cookie=cookie_2)).json()["documents"]
    assert doc_json["id"] in {d["id"] for d in listed}
    got = await docs.api.get(f"/documents/{doc.id}/file", cookie=cookie_2)
    assert got.content == data
    await docs.session.execute(
        update(Job).where(Job.document_id == doc.id).values(status=JobStatus.FAILED)
    )
    await docs.session.execute(
        update(Document)
        .where(Document.id == doc.id)
        .values(status=DocumentStatus.FAILED, error_kind="ValueError")
    )
    await docs.session.commit()
    again = await docs.upload(data, cookie_2)
    assert again.status_code == 200 and again.json()["requeued"] is True
    await docs.session.execute(delete(TaxItem).where(TaxItem.id == item.id))
    await docs.session.commit()
    assert (
        await docs.api.request("DELETE", f"/documents/{doc.id}", cookie=cookie_2)
    ).status_code == 204


async def test_deleting_app_user_is_restricted(docs: Docs) -> None:
    uploader, cookie = await docs.user()
    hh = await docs.session.get(Household, uploader.household_id)
    assert hh is not None
    person = await make_person(docs.session, hh)
    uploader.person_id = person.id
    await docs.session.commit()
    doc_json = await _upload(docs, cookie)
    doc_id = uuid.UUID(doc_json["id"])

    nested = await docs.session.begin_nested()
    with pytest.raises(IntegrityError):
        await docs.session.delete(uploader)
        await docs.session.flush()
    await nested.rollback()
    docs.session.expunge_all()
    assert await docs.session.get(Document, doc_id) is not None

    person_row = await docs.session.get(Person, person.id)
    assert person_row is not None
    await docs.session.delete(person_row)
    await docs.session.commit()
    docs.session.expunge_all()
    reloaded = await docs.session.get(AppUser, uploader.id)
    assert reloaded is not None and reloaded.person_id is None
    assert await docs.session.get(Document, doc_id) is not None
