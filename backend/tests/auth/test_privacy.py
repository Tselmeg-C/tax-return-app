"""No e-mail, token, token hash, cookie value or `next` in logs, spans or exceptions (#5)."""

from __future__ import annotations

import io
import logging
import secrets

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.tokens import hash_token
from tests.auth.conftest import Api, session_cookie_value, token_from_mail
from tests.domain.factories import make_household, make_user


async def test_full_flow_leaks_nothing(
    api: Api,
    db_session: AsyncSession,
    spans: InMemorySpanExporter,
    json_log: io.StringIO,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    email = f"sentinel-{secrets.token_hex(8)}@example.com"
    next_path = f"/belege-{secrets.token_hex(6)}"
    household = await make_household(db_session)
    await make_user(db_session, household, email=email)
    await db_session.commit()

    # Request (with messy casing) → verify → me → logout.
    requested = await api.post(
        "/auth/magic-link", json={"email": f" {email.upper()} ", "next": next_path}
    )
    assert requested.status_code == 202
    await api.drain()
    token = token_from_mail(api.outbox[-1])
    verified = await api.post("/auth/verify", json={"token": token})
    assert verified.status_code == 200 and verified.json() == {"next": next_path}
    cookie = session_cookie_value(verified, api.cookie_name)
    assert cookie
    me = await api.get("/auth/me", cookie=cookie)
    assert me.status_code == 200
    assert (await api.post("/auth/logout", cookie=cookie)).status_code == 204

    # A failed verify (the used token again) and a rate-limited request.
    assert (await api.post("/auth/verify", json={"token": token})).status_code == 400
    for _ in range(10):
        await api.post("/auth/magic-link", json={"email": email}, ip="198.51.100.200")
    limited = await api.post("/auth/magic-link", json={"email": email}, ip="198.51.100.200")
    assert limited.status_code == 429
    # An invalid address that contains the sentinel too.
    invalid = await api.post("/auth/magic-link", json={"email": f"{email} x"})
    assert invalid.status_code == 422 and email not in invalid.text
    await api.drain()

    finished = spans.get_finished_spans()
    assert finished, "no spans captured"
    exported = "\n".join(span.to_json() for span in finished)
    logged = json_log.getvalue() + caplog.text
    assert "auth.login.succeeded" in logged and "auth.rate_limited" in logged

    needles = {
        "e-mail": email,
        "e-mail (upper)": email.upper(),
        "token": token,
        "token hash": hash_token(token),
        "cookie": cookie,
        "cookie hash": hash_token(cookie),
        "next": next_path,
    }
    for label, needle in needles.items():
        assert needle not in exported, f"{label} in spans"
        assert needle not in logged, f"{label} in logs"
    for record in caplog.records:
        if record.exc_info and record.exc_info[1] is not None:
            message = str(record.exc_info[1])
            for label, needle in needles.items():
                assert needle not in message, f"{label} in exception"
