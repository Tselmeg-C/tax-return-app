"""`uv run python -m app.bot.cli set-webhook|delete-webhook`.

The URL is built from `APP_BASE_URL` (`<base>/api/bot/telegram/<secret>`, through the web
proxy). Prints the host only, never the URL, the secret or the token.
"""

from __future__ import annotations

import argparse
import asyncio
from urllib.parse import urlsplit

from app.bot.telegram import TelegramAdapter, TelegramError
from app.config import SettingsError, load_settings


async def run(command: str) -> int:
    try:
        settings = load_settings()
    except SettingsError as exc:
        print(f"error: {exc}")
        return 1
    if settings.telegram_bot_token is None:
        print("error: TELEGRAM_BOT_TOKEN is not set")
        return 1
    adapter = TelegramAdapter(settings.telegram_bot_token.get_secret_value())
    try:
        if command == "set-webhook":
            if settings.telegram_webhook_secret is None:
                print("error: TELEGRAM_WEBHOOK_SECRET is not set")
                return 1
            secret = settings.telegram_webhook_secret.get_secret_value()
            await adapter.set_webhook(f"{settings.app_base_url}/api/bot/telegram/{secret}", secret)
            print(f"webhook set for host {urlsplit(settings.app_base_url).netloc}")
        else:
            await adapter.delete_webhook()
            print("webhook deleted")
    except TelegramError as exc:
        print(f"error: Telegram call {exc.method} failed")
        return 1
    finally:
        await adapter.aclose()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app.bot.cli")
    parser.add_argument("command", choices=["set-webhook", "delete-webhook"])
    return asyncio.run(run(parser.parse_args().command))


if __name__ == "__main__":
    raise SystemExit(main())
