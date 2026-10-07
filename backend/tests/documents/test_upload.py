"""`POST /documents` (#6 Upload criteria)."""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.clock import FakeClock
from app.db.models import AuditLog, Document, Job
from app.domain.enums import ActorType, AuditAction, DocumentStatus, JobStatus
from tests.auth.conftest import auth_settings, running_app
from tests.documents import files
from tests.documents.conftest import Docs, committed_user


def _only_document_files(docs: Docs, before: set[str]) -> set[str]:
    return docs.files() - before


@pytest.mark.parametrize("name", list(files.accepted()))
async def test_accepted_types(docs: Docs, name: str) -> None:
    data, mime = files.accepted()[name]
    user, cookie = await docs.user()
    response = await docs.upload(data, cookie)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["duplicate"] is False
    doc = body["document"]
    assert doc["mime_type"] == mime
    assert doc["status"] == "queued"
    assert doc["size_bytes"] == len(data)
    assert doc["channel"] == "web"
    assert "sha256" not in doc and "storage_key" not in doc

    jobs = (await docs.session.execute(select(Job).where(Job.document_id == doc["id"]))).scalars()
    job_rows = list(jobs)
    assert len(job_rows) == 1
    assert job_rows[0].status is JobStatus.QUEUED and job_rows[0].attempts == 0
    audits = (
        (await docs.session.execute(select(AuditLog).where(AuditLog.entity_id == doc["id"])))
        .scalars()
        .all()
    )
    assert [(a.action, a.actor_type) for a in audits] == [(AuditAction.CREATE, ActorType.USER)]
    key = f"households/{user.household_id}/documents/{doc['id']}/original"
    assert (docs.root / key).read_bytes() == data


async def test_pdf_junk_window(docs: Docs) -> None:
    _, cookie = await docs.user()
    ok = await docs.upload(files.pdf(junk=100), cookie)
    assert ok.status_code == 201 and ok.json()["document"]["mime_type"] == "application/pdf"
    late = await docs.upload(files.pdf(junk=1100), cookie)
    assert late.status_code == 415


async def test_type_from_content(docs: Docs) -> None:
    _, cookie = await docs.user()
    response = await docs.upload(files.png(), cookie, headers={"Content-Type": "application/pdf"})
    assert response.json()["document"]["mime_type"] == "image/png"


async def _assert_nothing_left(docs: Docs, before_files: set[str], counts: tuple[int, ...]) -> None:
    assert docs.files() == before_files
    assert await docs.counts() == counts


@pytest.mark.parametrize("name", list(files.rejected()))
async def test_rejected_types(docs: Docs, name: str) -> None:
    _, cookie = await docs.user()
    before_files, counts = docs.files(), await docs.counts()
    headers = {"Content-Type": "image/svg+xml"} if name == "svg" else {}
    filename = "UTF-8''x.svg" if name == "svg" else None
    response = await docs.upload(files.rejected()[name], cookie, filename=filename, headers=headers)
    assert response.status_code == 415
    assert response.json() == {"detail": "unsupported_type"}
    await _assert_nothing_left(docs, before_files, counts)


async def test_empty_body(docs: Docs) -> None:
    _, cookie = await docs.user()
    before_files, counts = docs.files(), await docs.counts()
    response = await docs.upload(b"", cookie)
    assert response.status_code == 422
    assert response.json() == {"detail": "empty_file"}
    await _assert_nothing_left(docs, before_files, counts)


@pytest.fixture
async def small_docs(
    migrated_database: str,
    db_connection: object,
    db_session: AsyncSession,
    clock: FakeClock,
    tmp_path: Path,
) -> AsyncIterator[Docs]:
    root = tmp_path / "storage"
    settings = auth_settings(migrated_database, storage_path=root, max_upload_mb=1)
    async with running_app(settings, clock=clock, conn=db_connection) as api:  # type: ignore[arg-type]
        yield Docs(api=api, root=root, session=db_session, clock=clock)


async def test_size_limit(small_docs: Docs) -> None:
    docs = small_docs
    _, cookie = await docs.user()
    limit = 1_048_576
    exact = files.png() + b"\x00" * 0
    exact = exact + secrets.token_bytes(limit - len(exact))
    assert (await docs.upload(exact, cookie)).status_code == 201

    before_files, counts = docs.files(), await docs.counts()
    over = files.png() + secrets.token_bytes(limit)
    over = over[: limit + 1]
    response = await docs.upload(over, cookie)
    assert response.status_code == 413
    assert response.json() == {"detail": "too_large", "max_bytes": limit}
    await _assert_nothing_left(docs, before_files, counts)

    consumed = 0

    async def body() -> AsyncIterator[bytes]:
        nonlocal consumed
        for _ in range(40):
            consumed += 1
            yield files.png() + b"\x00" * 50_000

    response = await docs.upload(body(), cookie, headers={"Content-Length": "2000000"})
    assert response.status_code == 413
    assert consumed <= 1, "the body was read past the first chunk"

    response = await docs.upload(body(), cookie)  # chunked, no Content-Length
    assert response.status_code == 413
    await _assert_nothing_left(docs, before_files, counts)


async def test_duplicate_per_household(docs: Docs) -> None:
    user, cookie = await docs.user()
    data = files.png()
    first = await docs.upload(data, cookie, filename="UTF-8''erst.png")
    counts = await docs.counts()
    second = await docs.upload(data, cookie, filename="UTF-8''anders.pdf")
    assert second.status_code == 200
    body = second.json()
    assert body["duplicate"] is True and body["requeued"] is False
    assert body["document"]["id"] == first.json()["document"]["id"]
    assert body["document"]["original_filename"] == "erst.png"
    assert await docs.counts() == counts
    assert len([f for f in docs.files() if f.endswith("/original")]) == 1

    _, other_cookie = await docs.user()  # household B
    other = await docs.upload(data, other_cookie)
    assert other.status_code == 201
    assert other.json()["document"]["id"] != first.json()["document"]["id"]


async def test_duplicate_of_failed_requeues(docs: Docs) -> None:
    _, cookie = await docs.user()
    data = files.jpeg()
    first = (await docs.upload(data, cookie)).json()["document"]
    await docs.session.execute(
        update(Job)
        .where(Job.document_id == first["id"])
        .values(status=JobStatus.FAILED, last_error_kind="ValueError")
    )
    await docs.session.execute(
        update(Document)
        .where(Document.id == first["id"])
        .values(status=DocumentStatus.FAILED, error_kind="ValueError")
    )
    await docs.session.commit()

    again = await docs.upload(data, cookie, filename="UTF-8''neu.jpg")
    assert again.status_code == 200
    assert again.json()["duplicate"] is True and again.json()["requeued"] is True
    assert again.json()["document"]["status"] == "queued"
    assert again.json()["document"]["original_filename"] is None
    jobs = (
        (await docs.session.execute(select(Job.status).where(Job.document_id == first["id"])))
        .scalars()
        .all()
    )
    assert sorted(jobs) == sorted([JobStatus.FAILED, JobStatus.QUEUED])


async def test_auth_and_csrf(docs: Docs) -> None:
    assert (await docs.upload(files.png(), None)).status_code == 401
    _, cookie = await docs.user()
    response = await docs.upload(files.png(), cookie, csrf=False)
    assert response.status_code == 403 and response.json() == {"detail": "csrf"}


async def test_multipart_is_raw_bytes(docs: Docs) -> None:
    _, cookie = await docs.user()
    before = docs.files()
    boundary = "x" + secrets.token_hex(4)
    body = (
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
            f'filename="../../etc/passwd"\r\nContent-Type: image/png\r\n\r\n'
        ).encode()
        + files.png()
        + f"\r\n--{boundary}--\r\n".encode()
    )
    response = await docs.upload(
        body, cookie, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}
    )
    assert response.status_code == 415
    assert docs.files() == before


NAMES = {
    "UTF-8''Rechnung%20Zahnarzt%20M%C3%BCller.pdf": "Rechnung Zahnarzt Müller.pdf",
    "UTF-8''Rechnung%20Zahnarzt%20Mu%CC%88ller.pdf": "Rechnung Zahnarzt Müller.pdf",
    "UTF-8''..%2F..%2Fetc%2Fpasswd": "passwd",
    "UTF-8''C%3A%5CUsers%5Cx%5Cbeleg.jpg": "beleg.jpg",
    "UTF-8''a%0D%0AX-Evil%3A%201.jpg": "aX-Evil: 1.jpg",
    "UTF-8''abc%E2%80%AE.pdf": "abc.pdf",
    "UTF-8''...": None,
    "UTF-8''%2F": None,
    "UTF-8''": None,
    "Rechnung.pdf": None,
    "UTF-8''%FF": None,
    "UTF-8''" + "a" * 2993: None,
    "UTF-8''" + quote("ä" * 300 + ".pdf"): "ä" * 125 + ".pdf",
}


async def test_filenames(docs: Docs) -> None:
    user, cookie = await docs.user()
    prefix = f"households/{user.household_id}/documents/"
    cases: list[tuple[str | None, str | None]] = [*NAMES.items(), (None, None)]
    for raw, expected in cases:
        before = docs.files()
        response = await docs.upload(files.pdf(), cookie, filename=raw)
        assert response.status_code == 201, raw
        doc = response.json()["document"]
        assert doc["original_filename"] == expected, raw
        assert docs.files() - before == {f"{prefix}{doc['id']}/original"}
        one = await docs.api.get(f"/documents/{doc['id']}", cookie=cookie)
        assert one.json()["original_filename"] == expected
    listed = (await docs.api.get("/documents?limit=200", cookie=cookie)).json()["documents"]
    assert "Rechnung Zahnarzt Müller.pdf" in {d["original_filename"] for d in listed}


# --- concurrency (two real connections) ---------------------------------------------


async def test_concurrent_same_bytes(
    committed: async_sessionmaker[AsyncSession],
    migrated_database: str,
    clock: FakeClock,
    tmp_path: Path,
) -> None:
    user = await committed_user(committed, clock, None)
    root = tmp_path / "storage"
    settings = auth_settings(migrated_database, storage_path=root)
    data = files.png()
    async with running_app(settings, clock=clock) as api:
        transport = httpx.ASGITransport(app=api.app)
        headers = {
            "X-Requested-With": "belegbot",
            "Content-Type": "application/octet-stream",
            "Cookie": f"{api.cookie_name}={user.cookie}",
        }

        async def one() -> httpx.Response:
            async with httpx.AsyncClient(transport=transport, base_url=api.base_url) as client:
                return await client.post("/documents", content=data, headers=headers)

        first, second = await asyncio.gather(one(), one())
    assert sorted([first.status_code, second.status_code]) == [200, 201]
    assert first.json()["document"]["id"] == second.json()["document"]["id"]
    async with committed() as session:
        assert (await session.execute(select(func.count()).select_from(Document))).scalar() == 1
        assert (await session.execute(select(func.count()).select_from(Job))).scalar() == 1
    originals = [p for p in root.rglob("original")]
    assert len(originals) == 1
    assert list((root / "tmp").iterdir()) == []
