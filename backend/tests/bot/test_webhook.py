"""Webhook route (#11): secrets, always-200, CSRF exemption, replay, logs and spans."""

from __future__ import annotations

import asyncio
import io
import logging
import secrets
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.api.public import CSRF_EXEMPT_PATHS, PUBLIC_PATHS, is_csrf_exempt
from app.bot import messages as m
from app.bot.links import hash_token
from tests.bot.conftest import BOT_TOKEN, WEBHOOK_SECRET, Bot, new_chat_id
from tests.documents.files import pdf

HEADER = "X-Telegram-Bot-Api-Secret-Token"
PATH = f"/bot/telegram/{WEBHOOK_SECRET}"


def update(chat: str, update_id: int, **fields: Any) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "chat": {"id": int(chat), "type": "private"},
            "from": {"id": int(chat), "is_bot": False},
            **fields,
        },
    }


async def post(
    bot: Bot, body: Any, *, path: str = PATH, header: str | None = WEBHOOK_SECRET
) -> Any:
    headers = {} if header is None else {HEADER: header}
    # No X-Requested-With: the route is CSRF-exempt.
    response = await bot.api.post(path, json=body, headers=headers, csrf=False)
    await asyncio.gather(*bot.api.app.state.bot_tasks)
    return response


async def test_secret_checks(bot: Bot) -> None:
    body = update(new_chat_id(), 1, text="/start")
    assert (await post(bot, body, path="/bot/telegram/" + "x" * 24)).status_code == 404
    assert (await post(bot, body, header=None)).status_code == 403
    assert (await post(bot, body, header="y" * 24)).status_code == 403
    assert bot.adapter.sent == []
    ok = await post(bot, body)
    assert ok.status_code == 200
    assert bot.adapter.texts() == [m.HELP]


async def test_unconfigured_secret_is_404(
    migrated_database: str, db_connection: Any, clock: Any, tmp_path: Any
) -> None:
    from tests.auth.conftest import auth_settings, running_app

    settings = auth_settings(migrated_database, storage_path=tmp_path / "s")
    async with running_app(settings, clock=clock, conn=db_connection) as api:
        response = await api.post("/bot/telegram/anything-anything-1", json={}, csrf=False)
        assert response.status_code == 404


async def test_route_is_public_and_csrf_exempt() -> None:
    assert "/bot/telegram/{secret}" in PUBLIC_PATHS | CSRF_EXEMPT_PATHS
    assert (
        is_csrf_exempt(PATH)
        and not is_csrf_exempt("/bot/telegram/")
        and not is_csrf_exempt("/bot/telegram/a/b")
    )
    assert not is_csrf_exempt("/documents")


async def test_always_200_even_on_garbage_or_handler_errors(
    bot: Bot, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    chat = new_chat_id()
    for body in ({"nonsense": True}, [1, 2], "text"):
        assert (await post(bot, body)).status_code == 200
    raw = await bot.api.post(
        PATH, content=b"{not json", headers={HEADER: WEBHOOK_SECRET}, csrf=False
    )
    assert raw.status_code == 200

    async def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError(f"detail {chat}")

    monkeypatch.setattr("app.bot.core.find_link", boom)
    assert (await post(bot, update(chat, 5, text="/summary"))).status_code == 200
    assert "bot.handler_failed" in caplog.text and "RuntimeError" in caplog.text
    assert chat not in caplog.text and "detail" not in caplog.text


async def test_group_and_edited_updates_are_ignored(bot: Bot) -> None:
    chat = await bot.linked()
    group = update(chat.chat_id, 7, text="/year")
    group["message"]["chat"]["type"] = "group"
    assert (await post(bot, group)).status_code == 200
    edited = {"update_id": 8, "edited_message": group["message"]}
    assert (await post(bot, edited)).status_code == 200
    assert bot.adapter.sent == []


async def test_full_flow_through_the_webhook_and_replay(bot: Bot) -> None:
    chat = await bot.linked()
    await bot.send(chat.chat_id, "/unlink")
    code = await bot.code_for(chat.household, chat.user)
    tg_chat = new_chat_id()
    await post(bot, update(tg_chat, 10, text=f"/link {code}"))
    assert bot.adapter.texts(tg_chat) == [m.LINKED]
    assert bot.adapter.deleted == [(tg_chat, "10")]
    # a file: acknowledged first, handled in the held task
    data = pdf()
    ref = bot.adapter.add_file(data)
    body = update(
        tg_chat,
        11,
        document={"file_id": ref, "file_size": len(data), "mime_type": "application/pdf"},
    )
    assert (await post(bot, body)).status_code == 200
    assert bot.adapter.texts(tg_chat)[-1] == m.RECEIVED
    assert len(await bot.documents()) == 1
    # Telegram retries the same update: nothing happens twice
    sent = len(bot.adapter.sent)
    assert (await post(bot, body)).status_code == 200
    assert len(bot.adapter.sent) == sent and len(await bot.documents()) == 1
    undo = update(tg_chat, 12, text="/undo")
    await post(bot, undo)
    await post(bot, undo)
    assert bot.adapter.texts(tg_chat).count(m.UNDONE) == 1


async def test_album_updates_arriving_together_are_all_handled(bot: Bot) -> None:
    chat = await bot.linked()
    bodies = []
    for i in range(3):
        data = pdf()
        ref = bot.adapter.add_file(data)
        bodies.append(
            update(
                chat.chat_id,
                20 + i,
                document={"file_id": ref, "file_size": len(data), "mime_type": "application/pdf"},
            )
        )
    # all three requests are in flight before any task runs
    responses = [
        await bot.api.post(PATH, json=b, headers={HEADER: WEBHOOK_SECRET}, csrf=False)
        for b in bodies
    ]
    await asyncio.gather(*bot.api.app.state.bot_tasks)
    assert [r.status_code for r in responses] == [200, 200, 200]
    assert len(await bot.documents()) == 3
    assert bot.adapter.texts(chat.chat_id).count(m.RECEIVED) == 3


# --- privacy -------------------------------------------------------------------------


async def test_nothing_secret_in_logs_and_spans(
    bot: Bot,
    spans: InMemorySpanExporter,
    json_log: io.StringIO,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    chat = await bot.linked()
    await bot.send(chat.chat_id, "/unlink")
    code = await bot.code_for(chat.household, chat.user)
    tg_chat = new_chat_id()
    sentinel = "sentinel-" + secrets.token_hex(8)
    await post(bot, update(tg_chat, 1, text=f"/link {code}"))
    await post(bot, update(tg_chat, 2, text=sentinel))
    await post(bot, update(tg_chat, 3, text="/summary 2025"))
    await post(bot, update(tg_chat, 4, text="/year 1999"))
    data = pdf()
    ref = bot.adapter.add_file(data)
    await post(
        bot,
        update(
            tg_chat,
            5,
            document={
                "file_id": ref,
                "file_size": len(data),
                "mime_type": "application/pdf",
                "file_name": f"{sentinel}.pdf",
            },
        ),
    )
    await post(bot, update(tg_chat, 6, text="/undo"))
    await post(bot, update(new_chat_id(), 7, text="/link 00000000"))
    assert (
        await post(bot, update(tg_chat, 8, text="x"), path="/bot/telegram/" + "z" * 24)
    ).status_code == 404

    finished = spans.get_finished_spans()
    assert finished
    exported = "\n".join(span.to_json() for span in finished)
    logged = json_log.getvalue() + caplog.text
    assert "/bot/telegram" in exported
    assert "bot.command" in logged and "/bot/telegram/{secret}" in logged
    needles = {
        "webhook secret": WEBHOOK_SECRET,
        "bot token": BOT_TOKEN,
        "chat id": tg_chat,
        "old chat id": chat.chat_id,
        "link code": code,
        "code hash": hash_token(code),
        "message text": sentinel,
        "e-mail": chat.user.email,
        "wrong secret": "z" * 24,
    }
    for label, needle in needles.items():
        assert needle not in logged, f"{label} in logs"
        assert needle not in exported, f"{label} in spans"
    assert datetime.now(UTC)  # (keeps the import used)


async def test_httpx_loggers_are_quiet() -> None:
    from app.bot.telegram import TelegramAdapter

    TelegramAdapter("1:abc", client=httpx.AsyncClient())
    assert logging.getLogger("httpx").level >= logging.WARNING
    assert logging.getLogger("httpcore").level >= logging.WARNING
