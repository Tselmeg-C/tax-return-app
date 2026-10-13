"""Telegram adapter (#11): plain `httpx` against the Bot API; maps updates to `IncomingMessage`.

Never log or raise a URL, the token, a chat id, a file path or `str(exc)`: the file download
URL contains the bot token. Errors are `TelegramError` carrying only the Bot API method name.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.bot.core import IncomingFile, IncomingMessage
from app.domain.enums import Channel

API = "https://api.telegram.org"
GET_FILE_LIMIT = 20 * 1024 * 1024  # Bot API `getFile` limit
REQUEST_TIMEOUT = httpx.Timeout(30.0, read=60.0)
CHUNK = 64 * 1024


class TelegramError(Exception):
    """A Bot API call failed. `method` is the Bot API method (a fixed name), never a URL."""

    def __init__(self, method: str) -> None:
        super().__init__(method)
        self.method = method


class TelegramAdapter:
    max_file_bytes = GET_FILE_LIMIT

    def __init__(self, token: str, *, client: httpx.AsyncClient | None = None) -> None:
        # The client libraries log full request URLs (= the token) at INFO.
        for name in ("httpx", "httpcore"):
            logging.getLogger(name).setLevel(logging.WARNING)
        self._token = token
        self._client = client or httpx.AsyncClient(timeout=REQUEST_TIMEOUT)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _call(
        self, method: str, payload: dict[str, Any], *, timeout: float | None = None
    ) -> Any:
        try:
            response = await self._client.post(
                f"{API}/bot{self._token}/{method}",
                json=payload,
                timeout=timeout if timeout is not None else REQUEST_TIMEOUT,
            )
            body = response.json()
        except (httpx.HTTPError, ValueError):
            raise TelegramError(method) from None
        if response.status_code != 200 or not isinstance(body, dict) or not body.get("ok"):
            raise TelegramError(method)
        return body.get("result")

    async def send_text(self, external_id: str, text: str) -> None:
        await self._call("sendMessage", {"chat_id": int(external_id), "text": text})

    async def delete_message(self, external_id: str, message_ref: str) -> None:
        await self._call(
            "deleteMessage", {"chat_id": int(external_id), "message_id": int(message_ref)}
        )

    async def open_file(self, ref: str) -> AsyncIterator[bytes]:
        info = await self._call("getFile", {"file_id": ref})
        path = info.get("file_path") if isinstance(info, dict) else None
        if not isinstance(path, str) or not path:
            raise TelegramError("getFile")
        try:
            async with self._client.stream("GET", f"{API}/file/bot{self._token}/{path}") as resp:
                if resp.status_code != 200:
                    raise TelegramError("file")
                async for chunk in resp.aiter_bytes(CHUNK):
                    yield chunk
        except httpx.HTTPError:
            raise TelegramError("file") from None

    async def get_updates(self, offset: int | None, timeout: int = 30) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"timeout": timeout, "allowed_updates": ["message"]}
        if offset is not None:
            payload["offset"] = offset
        result = await self._call("getUpdates", payload, timeout=timeout + 15)
        return [u for u in result if isinstance(u, dict)] if isinstance(result, list) else []

    async def set_webhook(self, url: str, secret: str) -> None:
        await self._call(
            "setWebhook",
            {"url": url, "secret_token": secret, "allowed_updates": ["message"]},
        )

    async def delete_webhook(self) -> None:
        await self._call("deleteWebhook", {})


def parse_update(update: object) -> IncomingMessage | None:
    """Map a Telegram update to a message. Only a `message` in a private chat counts: groups,
    channels, edits and callbacks give `None`."""
    if not isinstance(update, dict):
        return None
    update_id = update.get("update_id")
    message = update.get("message")
    if (
        not isinstance(update_id, int)
        or isinstance(update_id, bool)
        or not isinstance(message, dict)
    ):
        return None
    chat = message.get("chat")
    sender = message.get("from")
    if not isinstance(chat, dict) or chat.get("type") != "private":
        return None
    if not isinstance(sender, dict) or sender.get("is_bot"):
        return None
    chat_id = chat.get("id")
    if not isinstance(chat_id, int) or isinstance(chat_id, bool):
        return None
    files: list[IncomingFile] = []
    photos = message.get("photo")
    if isinstance(photos, list) and photos:
        sizes = [p for p in photos if isinstance(p, dict) and isinstance(p.get("file_id"), str)]
        if sizes:
            best = max(
                sizes,
                key=lambda p: (
                    _int(p.get("width")) * _int(p.get("height")),
                    _int(p.get("file_size")),
                ),
            )
            files.append(IncomingFile(best["file_id"], _size(best), "image/jpeg"))
    doc = message.get("document")
    if isinstance(doc, dict) and isinstance(doc.get("file_id"), str):
        mime = doc.get("mime_type")
        files.append(
            IncomingFile(doc["file_id"], _size(doc), mime if isinstance(mime, str) else None)
        )
    text = message.get("text")
    message_id = message.get("message_id")
    return IncomingMessage(
        channel=Channel.TELEGRAM,
        external_id=str(chat_id),
        update_id=update_id,
        text=text if isinstance(text, str) else None,
        files=files,
        message_ref=str(message_id) if isinstance(message_id, int) else None,
    )


def _int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _size(obj: dict[str, Any]) -> int | None:
    value = obj.get("file_size")
    return value if isinstance(value, int) and not isinstance(value, bool) else None
