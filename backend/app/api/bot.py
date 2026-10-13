"""Bot api (#11): the Telegram webhook and the web side of linking.

Webhook auth: the path `{secret}` and the `X-Telegram-Bot-Api-Secret-Token` header must both
equal `TELEGRAM_WEBHOOK_SECRET` (constant-time compare). Wrong path: 404, wrong / missing
header: 403; after that always 200 (Telegram retries every non-2xx), the update is handled in a
held task. The request log and spans carry the route template, never the concrete path.
"""

from __future__ import annotations

import asyncio
import hmac
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from app.api.deps import DbSession, Scope, SignedIn
from app.auth.clock import Clock
from app.bot.core import BotDeps, ChannelAdapter, handle
from app.bot.links import find_link_of_user, issue_code, unlink_user
from app.bot.telegram import parse_update
from app.config import Settings
from app.domain.enums import Channel

router = APIRouter()
log = structlog.stdlib.get_logger("app.api.bot")

SECRET_HEADER = "x-telegram-bot-api-secret-token"
MAX_UPDATE_BYTES = 1024 * 1024


def _same(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


async def _run(message: Any, adapter: ChannelAdapter, deps: BotDeps) -> None:
    await handle(message, adapter, deps)  # never raises


@router.post("/bot/telegram/{secret}")
async def telegram_webhook(secret: str, request: Request) -> Response:
    state = request.app.state
    settings: Settings = state.settings
    expected = (
        None
        if settings.telegram_webhook_secret is None
        else settings.telegram_webhook_secret.get_secret_value()
    )
    if expected is None or not _same(secret, expected):
        raise HTTPException(status_code=404, detail="not_found")
    if not _same(request.headers.get(SECRET_HEADER, ""), expected):
        raise HTTPException(status_code=403, detail="forbidden")
    adapter: ChannelAdapter | None = state.bot_adapter
    length = request.headers.get("content-length", "")
    if adapter is not None and not (length.isdigit() and int(length) > MAX_UPDATE_BYTES):
        try:
            message = parse_update(await request.json())
        except ValueError:
            message = None
        if message is not None:
            deps: BotDeps = state.bot_deps
            task = asyncio.create_task(_run(message, adapter, deps))
            state.bot_tasks.add(task)  # held, so it is not garbage-collected mid-run
            task.add_done_callback(state.bot_tasks.discard)
    return Response(status_code=200)


@router.post("/bot/link-code")
async def create_link_code(request: Request, user: SignedIn, scope: Scope) -> JSONResponse:
    clock: Clock = request.app.state.clock
    code, expires_at = await issue_code(scope, user.user_id, clock.now())
    # Shown once: not logged, not in a span, never cached.
    return JSONResponse(
        {"code": code, "expires_at": expires_at.isoformat()}, headers={"Cache-Control": "no-store"}
    )


@router.get("/bot/link")
async def get_link(user: SignedIn, db: DbSession) -> dict[str, Any]:
    found = await find_link_of_user(db, user.user_id, Channel.TELEGRAM)
    return {"linked": found is not None, "channel": Channel.TELEGRAM.value}


@router.delete("/bot/link", status_code=204)
async def delete_link(user: SignedIn, scope: Scope) -> Response:
    if await unlink_user(scope, user.user_id, Channel.TELEGRAM):
        log.info("bot.unlinked", user_id=str(user.user_id))
    return Response(status_code=204)
