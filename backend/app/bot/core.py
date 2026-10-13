"""Channel-agnostic bot core (#11). No Telegram import: adapters map their updates to
`IncomingMessage` and implement `ChannelAdapter`; WhatsApp later only needs another adapter.

Privacy: never log message text, file bytes or names, chat ids, link codes or `str(exc)`.
Logs carry user / household ids, the command name (a fixed set), outcome codes and
`type(exc).__name__`. Replies to a linked chat may hold amounts and vendors (accepted).
"""

from __future__ import annotations

import asyncio
import uuid
import weakref
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.clock import Clock
from app.auth.ratelimit import RateLimiter
from app.bot import messages as msg_
from app.bot.links import (
    LinkedUser,
    claim_update,
    consume_code,
    create_link,
    external_hash,
    find_link,
    normalise_code,
    set_year,
    unlink_user,
)
from app.bot.render import render_missing, render_summary
from app.config import Settings
from app.db.models import Document, Person, TaxItem
from app.db.scope import HouseholdScope
from app.documents.service import (
    DocumentBusy,
    EmptyFile,
    UnsupportedType,
    delete_document,
    ingest_document,
)
from app.domain.enums import Channel
from app.estimate.service import build_summary
from app.queue.enqueue import current_traceparent
from app.storage import ObjectTooLarge, Storage, StorageFull
from app.tax_params import UnsupportedTaxYear, supported_years

log = structlog.stdlib.get_logger("app.bot")

BERLIN = ZoneInfo("Europe/Berlin")
FAILURE_WINDOW = timedelta(minutes=15)
CHAT_FAILURES = 5
GLOBAL_FAILURES = 30
GLOBAL_KEY = "all"
COMMANDS = frozenset({"start", "link", "summary", "missing", "undo", "year", "unlink"})


@dataclass(frozen=True)
class IncomingFile:
    ref: str  # adapter-specific handle for `open_file`
    size: int | None
    mime_type: str | None = None


@dataclass(frozen=True)
class IncomingMessage:
    channel: Channel
    external_id: str  # chat id
    update_id: int
    text: str | None
    files: Sequence[IncomingFile] = ()
    message_ref: str | None = None  # for `delete_message` (the /link message)


class ChannelAdapter(Protocol):
    max_file_bytes: int

    async def send_text(self, external_id: str, text: str) -> None: ...

    def open_file(self, ref: str) -> AsyncIterator[bytes]:
        """Stream the file; raises on a download error."""
        ...

    async def delete_message(self, external_id: str, message_ref: str) -> None: ...


@dataclass
class BotDeps:
    sessionmaker: async_sessionmaker[AsyncSession]
    storage: Storage
    settings: Settings
    clock: Clock
    chat_failures: RateLimiter = field(init=False)
    global_failures: RateLimiter = field(init=False)

    def __post_init__(self) -> None:
        # In memory like the IP limits in #5 (one api process in v1).
        self.chat_failures = RateLimiter(CHAT_FAILURES, FAILURE_WINDOW, self.clock)
        self.global_failures = RateLimiter(GLOBAL_FAILURES, FAILURE_WINDOW, self.clock)


# One update at a time per chat, in arrival order (albums arrive as several updates).
_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()


def _lock(channel: Channel, external_id: str) -> asyncio.Lock:
    key = external_hash(channel, external_id)
    lock = _locks.get(key)
    if lock is None:
        lock = _locks[key] = asyncio.Lock()
    return lock


def parse_command(text: str | None) -> tuple[str | None, list[str]]:
    """`("summary", ["2025"])` for `/summary@bot 2025`; `(None, [])` for anything else."""
    parts = (text or "").split()
    if not parts or not parts[0].startswith("/"):
        return None, []
    name = parts[0][1:].split("@", 1)[0].lower()
    return (name if name in COMMANDS else "unknown"), parts[1:]


def default_year(link_year: int | None, today: date) -> int:
    years = supported_years()
    if link_year in years:
        return link_year
    return today.year if today.year in years else max(years)


async def handle(msg: IncomingMessage, adapter: ChannelAdapter, deps: BotDeps) -> None:
    """Handle one update. Never raises (the webhook answers 200 whatever happens)."""
    async with _lock(msg.channel, msg.external_id):
        try:
            await _handle(msg, adapter, deps)
        except Exception as exc:
            log.warning("bot.handler_failed", error_kind=type(exc).__name__)


async def _handle(msg: IncomingMessage, adapter: ChannelAdapter, deps: BotDeps) -> None:
    command, args = parse_command(msg.text)
    async with deps.sessionmaker() as session:
        link = await find_link(session, msg.channel, msg.external_id)
        if link is None:
            # Unknown chat: only /link is processed; /start answers static text. No data.
            if command == "link":
                await _link(msg, adapter, deps, session, args)
            elif command == "start":
                await adapter.send_text(msg.external_id, msg_.HELP)
            return
        if link.disabled:
            return  # a disabled user gets nothing
        if not await claim_update(session, link.link_id, msg.update_id):
            log.info("bot.duplicate_update", user_id=str(link.user_id))
            return
        ctx = _Ctx(msg, adapter, deps, session, link, HouseholdScope(session, link.household_id))
        if msg.files:
            for file in msg.files:
                await ctx.ingest(file)
            return
        await ctx.command(command, args)


async def _link(
    msg: IncomingMessage,
    adapter: ChannelAdapter,
    deps: BotDeps,
    session: AsyncSession,
    args: list[str],
) -> None:
    chat_key = external_hash(msg.channel, msg.external_id)
    if (
        deps.chat_failures.retry_after(chat_key) is not None
        or deps.global_failures.retry_after(GLOBAL_KEY) is not None
    ):
        return  # over the limit: no reply, no check
    code = normalise_code(args[0]) if args else None
    consumed = None if code is None else await consume_code(session, code, deps.clock.now())
    if msg.message_ref is not None:  # the code must not stay in the chat (best effort)
        try:
            await adapter.delete_message(msg.external_id, msg.message_ref)
        except Exception as exc:
            log.info("bot.delete_message_failed", error_kind=type(exc).__name__)
    if consumed is None:
        deps.chat_failures.add(chat_key)
        deps.global_failures.add(GLOBAL_KEY)
        log.info("bot.link", outcome="invalid")
        await adapter.send_text(msg.external_id, msg_.LINK_FAILED)
        return
    scope = HouseholdScope(session, consumed.household_id)
    linked = await create_link(scope, consumed.user_id, msg.channel, msg.external_id)
    log.info(
        "bot.link", outcome="linked" if linked else "chat_taken", user_id=str(consumed.user_id)
    )
    if linked:
        found = await find_link(session, msg.channel, msg.external_id)
        if found is not None:
            await claim_update(session, found.link_id, msg.update_id)
    await adapter.send_text(msg.external_id, msg_.LINKED if linked else msg_.ALREADY_LINKED)


@dataclass
class _Ctx:
    msg: IncomingMessage
    adapter: ChannelAdapter
    deps: BotDeps
    session: AsyncSession
    link: LinkedUser
    scope: HouseholdScope

    async def say(self, text: str) -> None:
        await self.adapter.send_text(self.msg.external_id, text)

    def _outcome(self, command: str, outcome: str) -> None:
        log.info(
            "bot.command",
            command=command,
            outcome=outcome,
            user_id=str(self.link.user_id),
            household_id=str(self.link.household_id),
        )

    def _today(self) -> date:
        return self.deps.clock.now().astimezone(BERLIN).date()

    def _year(self, args: list[str]) -> int | None:
        """The year argument, or the default; `None` (and a reply is due) for a bad argument."""
        if not args:
            return default_year(self.link.year, self._today())
        arg = args[0]
        return int(arg) if arg.isascii() and arg.isdigit() and len(arg) == 4 else None

    async def command(self, command: str | None, args: list[str]) -> None:
        name = command or "text"
        handler = {
            "start": self._start,
            "link": self._already_linked,
            "summary": self._summary,
            "missing": self._missing,
            "year": self._year_cmd,
            "undo": self._undo,
            "unlink": self._unlink,
        }.get(name)
        if handler is None:  # unknown command or plain text
            await self.say(msg_.HELP)
            self._outcome("unknown" if command else "text", "help")
            return
        await handler(args)

    async def _start(self, _args: list[str]) -> None:
        await self.say(msg_.HELP)
        self._outcome("start", "ok")

    async def _already_linked(self, _args: list[str]) -> None:
        await self.say(msg_.ALREADY_LINKED)
        self._outcome("link", "already_linked")

    async def _result(self, name: str, args: list[str]) -> tuple[int, dict[str, Any]] | None:
        year = self._year(args)
        if year is None:
            await self.say(msg_.YEAR_BAD)
            self._outcome(name, "bad_year")
            return None
        try:
            return year, await build_summary(self.scope, year, self._today())
        except UnsupportedTaxYear:
            await self.say(msg_.unsupported_year(supported_years()))
            self._outcome(name, "unsupported_year")
            return None

    async def _summary(self, args: list[str]) -> None:
        found = await self._result("summary", args)
        if found is not None:
            await self.say(render_summary(*found))
            self._outcome("summary", "ok")

    async def _missing(self, args: list[str]) -> None:
        found = await self._result("missing", args)
        if found is None:
            return
        names = await self._person_names(found[1])
        await self.say(render_missing(found[1], names))
        self._outcome("missing", "ok")

    async def _person_names(self, result: dict[str, Any]) -> dict[str, str]:
        ids = {uuid.UUID(m["person_id"]) for m in result["what_missing"] if m["person_id"]}
        if not ids:
            return {}
        rows = await self.session.execute(
            self.scope.select(Person)
            .where(Person.id.in_(ids))
            .with_only_columns(Person.id, Person.first_name)
        )
        return {str(pid): name for pid, name in rows.all()}

    async def _year_cmd(self, args: list[str]) -> None:
        if not args:
            await self.say(msg_.year_set(default_year(self.link.year, self._today())))
            self._outcome("year", "show")
            return
        year = self._year(args)
        if year is None:
            await self.say(msg_.YEAR_BAD)
            self._outcome("year", "bad_year")
        elif year not in supported_years():
            await self.say(msg_.unsupported_year(supported_years()))
            self._outcome("year", "unsupported_year")
        else:
            await set_year(self.session, self.link.link_id, year)
            await self.say(msg_.year_set(year))
            self._outcome("year", "ok")

    async def _unlink(self, _args: list[str]) -> None:
        await unlink_user(self.scope, self.link.user_id, self.msg.channel)
        await self.say(msg_.UNLINKED)
        self._outcome("unlink", "ok")

    async def _undo(self, _args: list[str]) -> None:
        doc_id = await self.session.scalar(
            self.scope.select(Document)
            .where(
                Document.channel == self.msg.channel,
                Document.uploaded_by_user_id == self.link.user_id,
            )
            .with_only_columns(Document.id)
            .order_by(Document.created_at.desc(), Document.id.desc())
            .limit(1)
        )
        if doc_id is None:
            await self.say(msg_.UNDO_NOTHING)
            self._outcome("undo", "nothing")
            return
        overridden = await self.session.scalar(
            self.scope.select(TaxItem)
            .where(TaxItem.document_id == doc_id, TaxItem.overridden_by_user.is_(True))
            .with_only_columns(TaxItem.id)
            .limit(1)
        )
        if overridden is not None:
            await self.say(msg_.UNDO_OVERRIDDEN)
            self._outcome("undo", "overridden")
            return
        try:
            await delete_document(self.scope, doc_id, self.link.user_id, self.deps.storage)
        except DocumentBusy:
            await self.say(msg_.UNDO_BUSY)
            self._outcome("undo", "busy")
            return
        await self.say(msg_.UNDONE)
        self._outcome("undo", "ok")

    async def ingest(self, file: IncomingFile) -> None:
        settings = self.deps.settings
        limit = min(self.adapter.max_file_bytes, settings.max_upload_bytes)
        mime = (file.mime_type or "").lower()
        if mime and mime != "application/pdf" and not mime.startswith("image/"):
            await self.say(msg_.FILE_UNSUPPORTED)
            self._outcome("file", "unsupported_type")
            return
        if file.size is not None and file.size > limit:
            await self.say(msg_.FILE_TOO_BIG)
            self._outcome("file", "too_large")
            return
        try:
            result = await ingest_document(
                self.scope,
                self.link.user_id,
                self.adapter.open_file(file.ref),
                channel=self.msg.channel,
                storage=self.deps.storage,
                max_bytes=limit,
                original_filename=None,
                trace_context=current_traceparent(),
                max_attempts=settings.job_max_attempts,
            )
        except ObjectTooLarge:
            reply, outcome = msg_.FILE_TOO_BIG, "too_large"
        except UnsupportedType:
            reply, outcome = msg_.FILE_UNSUPPORTED, "unsupported_type"
        except EmptyFile:
            reply, outcome = msg_.FILE_EMPTY, "empty"
        except StorageFull:
            reply, outcome = msg_.STORAGE_FULL, "storage_full"
        except Exception as exc:  # download or storage failure: the class name only
            log.warning("bot.file_failed", error_kind=type(exc).__name__)
            reply, outcome = msg_.FILE_FETCH_FAILED, "fetch_failed"
        else:
            if result.created:
                reply, outcome = msg_.RECEIVED, "created"
            elif result.requeued:
                reply, outcome = msg_.DUPLICATE_REQUEUED, "duplicate_requeued"
            else:
                reply, outcome = msg_.DUPLICATE, "duplicate"
            log.info(
                "bot.document",
                document_id=str(result.document.id),
                duplicate=not result.created,
                requeued=result.requeued,
            )
        await self.say(reply)
        self._outcome("file", outcome)


__all__ = [
    "BotDeps",
    "ChannelAdapter",
    "IncomingFile",
    "IncomingMessage",
    "default_year",
    "handle",
    "parse_command",
]
