"""Fixtures for the bot tests (#11): a `FakeAdapter`, the app with the fake adapter on the
rolled-back test connection, and helpers to build linked chats.

All ids, chat ids, codes and secrets are generated at runtime; no network, no Telegram.
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker

from app.auth.clock import FakeClock
from app.bot.core import BotDeps, IncomingFile, IncomingMessage, handle
from app.bot.links import CODE_TTL, create_link, issue_code
from app.db.models import AppUser, AuditLog, Document, Household
from app.db.scope import HouseholdScope
from app.domain.enums import Channel
from tests.auth.conftest import (  # noqa: F401 (fixtures)
    Api,
    _auth_span_exporter,
    auth_settings,
    clock,
    json_log,
    random_email,
    running_app,
    spans,
)
from tests.domain.conftest import keys  # noqa: F401 (autouse: throwaway Fernet key)
from tests.domain.factories import make_household, make_user

WEBHOOK_SECRET = "wh_" + secrets.token_hex(16)
BOT_TOKEN = "1234567:" + secrets.token_hex(16)


def new_chat_id() -> str:
    return str(secrets.randbelow(9 * 10**9) + 10**9)


class FakeAdapter:
    """Records what the core sends; serves bytes (or raises) for `open_file`."""

    def __init__(self) -> None:
        self.max_file_bytes = 20 * 1024 * 1024
        self.sent: list[tuple[str, str]] = []
        self.deleted: list[tuple[str, str]] = []
        self.files: dict[str, bytes | Exception] = {}

    async def send_text(self, external_id: str, text: str) -> None:
        self.sent.append((external_id, text))

    async def delete_message(self, external_id: str, message_ref: str) -> None:
        self.deleted.append((external_id, message_ref))

    async def open_file(self, ref: str) -> AsyncIterator[bytes]:
        data = self.files[ref]
        if isinstance(data, Exception):
            raise data
        for i in range(0, len(data), 7):
            yield data[i : i + 7]

    def add_file(self, data: bytes | Exception) -> str:
        ref = "file_" + secrets.token_hex(6)
        self.files[ref] = data
        return ref

    def texts(self, chat: str | None = None) -> list[str]:
        return [t for c, t in self.sent if chat is None or c == chat]


@dataclass
class Chat:
    household: Household
    user: AppUser
    chat_id: str


@dataclass
class Bot:
    api: Api
    session: AsyncSession
    clock: FakeClock
    adapter: FakeAdapter
    root: Path
    update_ids: dict[str, int] = field(default_factory=dict)

    @property
    def deps(self) -> BotDeps:
        deps: BotDeps = self.api.app.state.bot_deps
        return deps

    async def linked(self, household: Household | None = None, **user_kw: Any) -> Chat:
        hh = household or await make_household(self.session)
        user = await make_user(self.session, hh, email=random_email(), **user_kw)
        chat_id = new_chat_id()
        await create_link(HouseholdScope(self.session, hh.id), user.id, Channel.TELEGRAM, chat_id)
        return Chat(hh, user, chat_id)

    async def code_for(self, household: Household, user: AppUser) -> str:
        code, _ = await issue_code(
            HouseholdScope(self.session, household.id), user.id, self.clock.now()
        )
        return code

    def message(
        self,
        chat_id: str,
        text: str | None = None,
        *,
        files: tuple[IncomingFile, ...] = (),
        update_id: int | None = None,
        message_ref: str | None = None,
    ) -> IncomingMessage:
        if update_id is None:
            update_id = self.update_ids.get(chat_id, 100) + 1
        self.update_ids[chat_id] = max(update_id, self.update_ids.get(chat_id, 0))
        return IncomingMessage(Channel.TELEGRAM, chat_id, update_id, text, files, message_ref)

    async def send(self, chat_id: str, text: str | None = None, **kw: Any) -> list[str]:
        """Send one message; returns the replies it caused."""
        before = len(self.adapter.sent)
        await handle(self.message(chat_id, text, **kw), self.adapter, self.deps)
        return [t for _, t in self.adapter.sent[before:]]

    async def send_file(self, chat_id: str, data: bytes | Exception, **kw: Any) -> list[str]:
        ref = self.adapter.add_file(data)
        mime = kw.pop("mime", None)
        size = kw.pop("size", len(data) if isinstance(data, bytes) else None)
        return await self.send(chat_id, files=(IncomingFile(ref, size, mime),), **kw)

    async def count(self, model: Any) -> int:
        return int((await self.session.execute(select(func.count()).select_from(model))).scalar())

    async def audit(self, entity: str) -> list[AuditLog]:
        stmt = select(AuditLog).where(AuditLog.entity == entity).order_by(AuditLog.created_at)
        return list((await self.session.execute(stmt)).scalars().all())

    async def documents(self) -> list[Document]:
        return list((await self.session.execute(select(Document))).scalars().all())


def bot_settings(database_url: str, root: Path, **overrides: Any) -> Any:
    return auth_settings(
        database_url,
        storage_path=root,
        telegram_bot_token=BOT_TOKEN,
        telegram_webhook_secret=WEBHOOK_SECRET,
        bot_mode="webhook",
        **overrides,
    )


@pytest.fixture
async def bot(
    migrated_database: str,
    db_connection: AsyncConnection,
    db_session: AsyncSession,
    clock: FakeClock,  # noqa: F811
    tmp_path: Path,
) -> AsyncIterator[Bot]:
    root = tmp_path / "storage"
    adapter = FakeAdapter()
    settings = bot_settings(migrated_database, root)
    async with running_app(settings, clock=clock, conn=db_connection, bot_adapter=adapter) as api:
        # The handler opens its own sessions: put them on the rolled-back test connection.
        api.app.state.bot_deps.sessionmaker = async_sessionmaker(
            bind=db_connection, join_transaction_mode="create_savepoint", expire_on_commit=False
        )
        yield Bot(api, db_session, clock, adapter, root)


__all__ = ["CODE_TTL", "Bot", "Chat", "FakeAdapter"]
