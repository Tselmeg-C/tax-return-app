"""`GET /documents/{id}/preview` (#35)."""

from __future__ import annotations

import uuid

from app.documents.previews import ensure_preview
from tests.documents.conftest import Docs
from tests.pipeline import files


async def _doc(docs: Docs, cookie: str) -> uuid.UUID:
    response = await docs.upload(files.jpeg((800, 400)), cookie)
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["document"]["id"])


async def test_preview_roundtrip_scoping_and_delete(docs: Docs) -> None:
    user, cookie = await docs.user()
    _, other = await docs.user()
    doc_id = await _doc(docs, cookie)
    url = f"/documents/{doc_id}/preview"

    missing = await docs.api.get(url, cookie=cookie)
    assert missing.status_code == 404 and missing.json() == {"detail": "preview_not_found"}

    assert await ensure_preview(
        docs.storage, user.household_id, doc_id, files.jpeg((800, 400)), "image/jpeg"
    )
    ok = await docs.api.get(url, cookie=cookie)
    assert ok.status_code == 200
    assert ok.headers["content-type"] == "image/jpeg"
    assert ok.headers["x-content-type-options"] == "nosniff"
    assert ok.headers["cache-control"] == "private, no-store"
    assert ok.content[:2] == b"\xff\xd8"
    assert (await docs.api.get(url, cookie=other)).status_code == 404
    assert (await docs.api.get(url)).status_code == 401

    removed = await docs.api.request("DELETE", f"/documents/{doc_id}", cookie=cookie)
    assert removed.status_code == 204
    assert not any(name.endswith("preview.jpg") for name in docs.files())
