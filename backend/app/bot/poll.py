"""Polling mode for local development: `uv run python -m app.bot.poll` (not in the Procfile).

Deletes a registered webhook first (Telegram refuses `getUpdates` while one is set). Needs
`TELEGRAM_BOT_TOKEN`. Logs carry class names only.
"""

from __future__ import annotations

import asyncio
import signal

import structlog

from app.auth.clock import SystemClock
from app.bot.core import BotDeps, handle
from app.bot.telegram import TelegramAdapter, TelegramError, parse_update
from app.config import SettingsError, load_settings
from app.db.session import create_engine, create_sessionmaker
from app.observability import API_SERVICE_NAME, setup_observability
from app.observability.logs import set_level
from app.storage import storage_from_settings

log = structlog.stdlib.get_logger("app.bot.poll")
RETRY_SECONDS = 5.0


async def amain() -> int:
    try:
        settings = load_settings()
    except SettingsError as exc:
        log.error("poll startup failed", reason=str(exc))  # names variables, never values
        return 1
    if settings.telegram_bot_token is None:
        log.error("poll startup failed", reason="TELEGRAM_BOT_TOKEN: not set")
        return 1
    set_level(settings.log_level)
    engine = create_engine(settings)
    adapter = TelegramAdapter(settings.telegram_bot_token.get_secret_value())
    deps = BotDeps(
        create_sessionmaker(engine), storage_from_settings(settings), settings, SystemClock()
    )
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stopping.set)
    offset: int | None = None
    try:
        await adapter.delete_webhook()
        log.info("poll.started")
        while not stopping.is_set():
            try:
                updates = await adapter.get_updates(offset)
            except TelegramError as exc:
                log.warning("poll.get_updates_failed", error_kind=type(exc).__name__)
                await asyncio.sleep(RETRY_SECONDS)
                continue
            for update in updates:
                uid = update.get("update_id")
                if isinstance(uid, int):
                    offset = uid + 1
                message = parse_update(update)
                if message is not None:
                    await handle(message, adapter, deps)
    finally:
        await adapter.aclose()
        await engine.dispose()
    return 0


def main() -> int:
    observability = setup_observability(API_SERVICE_NAME)
    try:
        return asyncio.run(amain())
    finally:
        observability.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
