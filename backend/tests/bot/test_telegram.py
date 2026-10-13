"""Telegram adapter (#11) with `httpx.MockTransport` and synthetic update JSON."""

from __future__ import annotations

import json
import secrets
from typing import Any

import httpx
import pytest

from app.bot.telegram import TelegramAdapter, TelegramError, parse_update
from app.domain.enums import Channel

TOKEN = "777:" + secrets.token_hex(12)


def private_message(**fields: Any) -> dict[str, Any]:
    return {
        "update_id": 41,
        "message": {
            "message_id": 7,
            "chat": {"id": 555000111, "type": "private"},
            "from": {"id": 555000111, "is_bot": False},
            **fields,
        },
    }


def test_text_update() -> None:
    msg = parse_update(private_message(text="/summary 2025"))
    assert msg is not None
    assert (msg.channel, msg.external_id, msg.update_id) == (Channel.TELEGRAM, "555000111", 41)
    assert msg.text == "/summary 2025" and msg.files == () or list(msg.files) == []
    assert msg.message_ref == "7"


def test_photo_uses_the_largest_size() -> None:
    msg = parse_update(
        private_message(
            photo=[
                {"file_id": "small", "width": 90, "height": 60, "file_size": 1000},
                {"file_id": "big", "width": 1280, "height": 960, "file_size": 90000},
                {"file_id": "mid", "width": 320, "height": 240, "file_size": 9000},
            ]
        )
    )
    assert msg is not None and [(f.ref, f.size, f.mime_type) for f in msg.files] == [
        ("big", 90000, "image/jpeg")
    ]


def test_document_update() -> None:
    msg = parse_update(
        private_message(
            document={"file_id": "d1", "file_size": 12, "mime_type": "application/pdf"},
            media_group_id="album",
        )
    )
    assert msg is not None and [(f.ref, f.size, f.mime_type) for f in msg.files] == [
        ("d1", 12, "application/pdf")
    ]


@pytest.mark.parametrize(
    "update",
    [
        None,
        "x",
        {"update_id": 1},
        {"update_id": 1, "edited_message": {"text": "x"}},
        {"update_id": 1, "callback_query": {"id": "1"}},
        {"update_id": 1, "channel_post": {"text": "x", "chat": {"id": 1, "type": "channel"}}},
        {"update_id": "1", "message": {}},
    ],
)
def test_other_updates_are_ignored(update: object) -> None:
    assert parse_update(update) is None


def test_groups_and_bots_are_ignored() -> None:
    group = private_message(text="hi")
    group["message"]["chat"]["type"] = "supergroup"
    assert parse_update(group) is None
    bot_sender = private_message(text="hi")
    bot_sender["message"]["from"]["is_bot"] = True
    assert parse_update(bot_sender) is None


def adapter_with(handler: Any) -> TelegramAdapter:
    return TelegramAdapter(TOKEN, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_send_text_and_delete_message() -> None:
    seen: list[tuple[str, dict[str, Any]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path.rsplit("/", 1)[1], json.loads(request.content)))
        return httpx.Response(200, json={"ok": True, "result": True})

    tg = adapter_with(handler)
    await tg.send_text("555", "Hallo")
    await tg.delete_message("555", "9")
    assert seen == [
        ("sendMessage", {"chat_id": 555, "text": "Hallo"}),
        ("deleteMessage", {"chat_id": 555, "message_id": 9}),
    ]


async def test_open_file_streams_and_errors_carry_no_secret() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getFile"):
            return httpx.Response(200, json={"ok": True, "result": {"file_path": "docs/f.pdf"}})
        assert request.url.path == f"/file/bot{TOKEN}/docs/f.pdf"
        return httpx.Response(200, content=b"%PDF-1.7 hello")

    tg = adapter_with(handler)
    assert b"".join([c async for c in tg.open_file("abc")]) == b"%PDF-1.7 hello"

    def failing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"ok": False, "description": f"bad {TOKEN}"})

    with pytest.raises(TelegramError) as caught:
        await adapter_with(failing).send_text("1", "x")
    assert TOKEN not in str(caught.value) and caught.value.method == "sendMessage"

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {request.url}")

    with pytest.raises(TelegramError) as caught2:
        await adapter_with(unreachable).send_text("1", "x")
    assert TOKEN not in str(caught2.value) and TOKEN not in repr(caught2.value)


async def test_polling_and_webhook_calls() -> None:
    seen: list[tuple[str, dict[str, Any]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[1]
        seen.append((method, json.loads(request.content)))
        result: Any = [private_message(text="x")] if method == "getUpdates" else True
        return httpx.Response(200, json={"ok": True, "result": result})

    tg = adapter_with(handler)
    assert len(await tg.get_updates(12, timeout=1)) == 1
    await tg.set_webhook("https://example.org/api/bot/telegram/s", "s")
    await tg.delete_webhook()
    assert seen[0][1]["offset"] == 12 and seen[0][1]["allowed_updates"] == ["message"]
    assert seen[1] == (
        "setWebhook",
        {
            "url": "https://example.org/api/bot/telegram/s",
            "secret_token": "s",
            "allowed_updates": ["message"],
        },
    )
