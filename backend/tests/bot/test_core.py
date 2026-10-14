"""Core behaviour (#11) with a `FakeAdapter`: linking, silence, commands, files, undo."""

from __future__ import annotations

import ast
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select

from app.bot import messages as m
from app.bot.links import CODE_TTL, external_hash
from app.db.models import AppUser, ChannelLink, ChannelLinkCode, Document, Job
from app.domain.enums import (
    AuditAction,
    Channel,
    DocumentStatus,
    JobStatus,
)
from tests.bot.conftest import Bot, new_chat_id
from tests.documents.files import jpeg, pdf
from tests.domain.factories import make_document, make_tax_item

# --- silence -----------------------------------------------------------------------


async def test_unlinked_chat_gets_no_reply_and_no_document(bot: Bot) -> None:
    chat = new_chat_id()
    assert await bot.send(chat, "/summary") == []
    assert await bot.send(chat, "hallo") == []
    assert await bot.send(chat, "/undo") == []
    assert await bot.send_file(chat, pdf()) == []
    assert await bot.count(Document) == 0


async def test_start_answers_static_text_only(bot: Bot) -> None:
    assert await bot.send(new_chat_id(), "/start") == [m.HELP]


async def test_disabled_user_gets_nothing(bot: Bot) -> None:
    chat = await bot.linked()
    chat.user.disabled_at = bot.clock.now()
    await bot.session.flush()
    assert await bot.send(chat.chat_id, "/summary 2025") == []
    assert await bot.send(chat.chat_id, "/start") == []
    assert await bot.send_file(chat.chat_id, pdf()) == []
    assert await bot.count(Document) == 0


async def test_unlink_on_web_makes_the_bot_silent(bot: Bot) -> None:
    chat = await bot.linked()
    assert await bot.send(chat.chat_id, "/year") != []
    assert await bot.send(chat.chat_id, "/unlink") == [m.UNLINKED]
    assert await bot.send(chat.chat_id, "/year") == []
    assert [a.action for a in await bot.audit("channel_link")] == [
        AuditAction.CREATE,
        AuditAction.DELETE,
    ]


# --- linking -----------------------------------------------------------------------


async def test_link_with_valid_code(bot: Bot) -> None:
    chat = await bot.linked()  # to get a household and a user
    other = new_chat_id()
    await bot.send(chat.chat_id, "/unlink")
    code = await bot.code_for(chat.household, chat.user)
    replies = await bot.send(other, f"/link {code}", message_ref="77")
    assert replies == [m.LINKED]
    assert bot.adapter.deleted == [(other, "77")]  # the code does not stay in the chat
    link = (await bot.session.execute(select(ChannelLink))).scalar_one()
    assert link.external_id == other and link.external_id_hash == external_hash(
        Channel.TELEGRAM, other
    )
    assert link.user_id == chat.user.id
    # linked now: the chat can talk
    assert await bot.send(other, "/year") != []


async def test_code_is_single_use_and_failures_are_generic(bot: Bot) -> None:
    chat = await bot.linked()
    await bot.send(chat.chat_id, "/unlink")
    code = await bot.code_for(chat.household, chat.user)
    first, second = new_chat_id(), new_chat_id()
    assert await bot.send(first, f"/link {code}") == [m.LINKED]
    assert await bot.send(second, f"/link {code}") == [m.LINK_FAILED]  # used
    assert await bot.send(second, "/link 00000000") == [m.LINK_FAILED]  # unknown
    assert await bot.send(second, "/link abc") == [m.LINK_FAILED]  # malformed
    assert await bot.send(second, "/link") == [m.LINK_FAILED]  # missing


async def test_code_expires_after_ten_minutes(bot: Bot) -> None:
    chat = await bot.linked()
    await bot.send(chat.chat_id, "/unlink")
    code = await bot.code_for(chat.household, chat.user)
    bot.clock.advance(CODE_TTL)
    assert await bot.send(new_chat_id(), f"/link {code}") == [m.LINK_FAILED]


async def test_a_new_code_invalidates_the_old_one(bot: Bot) -> None:
    chat = await bot.linked()
    await bot.send(chat.chat_id, "/unlink")
    old = await bot.code_for(chat.household, chat.user)
    new = await bot.code_for(chat.household, chat.user)
    assert await bot.send(new_chat_id(), f"/link {old}") == [m.LINK_FAILED]
    assert await bot.send(new_chat_id(), f"/link {new}") == [m.LINKED]


async def test_code_of_a_disabled_user_does_not_link(bot: Bot) -> None:
    chat = await bot.linked()
    await bot.send(chat.chat_id, "/unlink")
    code = await bot.code_for(chat.household, chat.user)
    chat.user.disabled_at = bot.clock.now()
    await bot.session.flush()
    assert await bot.send(new_chat_id(), f"/link {code}") == [m.LINK_FAILED]
    assert await bot.count(ChannelLink) == 0


async def test_relinking_replaces_the_old_row_and_is_audited(bot: Bot) -> None:
    chat = await bot.linked()
    code = await bot.code_for(chat.household, chat.user)
    new_chat = new_chat_id()
    assert await bot.send(new_chat, f"/link {code}") == [m.LINKED]
    rows = (await bot.session.execute(select(ChannelLink))).scalars().all()
    assert [r.external_id for r in rows] == [new_chat]
    assert [a.action for a in await bot.audit("channel_link")] == [
        AuditAction.CREATE,
        AuditAction.DELETE,
        AuditAction.CREATE,
    ]
    # the old chat is silent now
    assert await bot.send(chat.chat_id, "/year") == []


async def test_limit_five_failures_per_chat(bot: Bot) -> None:
    chat = await bot.linked()
    await bot.send(chat.chat_id, "/unlink")
    code = await bot.code_for(chat.household, chat.user)
    attacker = new_chat_id()
    for _ in range(5):
        assert await bot.send(attacker, "/link 12345678") == [m.LINK_FAILED]
    # over the limit: no reply and no check, even with a valid code
    assert await bot.send(attacker, f"/link {code}") == []
    row = (await bot.session.execute(select(ChannelLinkCode))).scalar_one()
    assert row.used_at is None
    # another chat is not affected, and after the window the attacker may try again
    assert await bot.send(new_chat_id(), "/link 12345678") == [m.LINK_FAILED]
    bot.clock.advance(timedelta(minutes=16))
    fresh = await bot.code_for(chat.household, chat.user)
    assert await bot.send(attacker, f"/link {fresh}") == [m.LINKED]


async def test_global_limit_of_thirty_failures(bot: Bot) -> None:
    for _ in range(30):
        assert await bot.send(new_chat_id(), "/link 12345678") == [m.LINK_FAILED]
    assert await bot.send(new_chat_id(), "/link 12345678") == []


async def test_chat_linked_to_someone_else_is_refused(bot: Bot) -> None:
    chat = await bot.linked()
    other = await bot.linked()
    code = await bot.code_for(other.household, other.user)
    assert await bot.send(chat.chat_id, f"/link {code}") == [m.ALREADY_LINKED]
    link = (
        await bot.session.execute(select(ChannelLink).where(ChannelLink.user_id == chat.user.id))
    ).scalar_one()
    assert link.external_id == chat.chat_id


# --- update ids --------------------------------------------------------------------


async def test_repeated_update_id_is_ignored(bot: Bot) -> None:
    chat = await bot.linked()
    doc = await make_document(
        bot.session, chat.household, chat.user, channel=Channel.TELEGRAM, status=DocumentStatus.DONE
    )
    other = await make_document(
        bot.session, chat.household, chat.user, channel=Channel.TELEGRAM, status=DocumentStatus.DONE
    )
    assert await bot.send(chat.chat_id, "/undo", update_id=500) == [m.UNDONE]
    assert await bot.send(chat.chat_id, "/undo", update_id=500) == []  # Telegram retry
    assert await bot.send(chat.chat_id, "/undo", update_id=499) == []  # older
    remaining = {d.id for d in await bot.documents()}
    assert len(remaining) == 1 and remaining <= {doc.id, other.id}
    assert await bot.send(chat.chat_id, "/undo", update_id=501) == [m.UNDONE]


# --- files -------------------------------------------------------------------------


async def test_file_creates_a_telegram_document(bot: Bot) -> None:
    chat = await bot.linked()
    assert await bot.send_file(chat.chat_id, pdf(), mime="application/pdf") == [m.RECEIVED]
    doc = (await bot.documents())[0]
    assert doc.channel is Channel.TELEGRAM and doc.uploaded_by_user_id == chat.user.id
    assert doc.original_filename is None and doc.status is DocumentStatus.QUEUED
    assert await bot.count(Job) == 1


async def test_photo_and_duplicate_and_requeue(bot: Bot) -> None:
    chat = await bot.linked()
    data = jpeg()
    assert await bot.send_file(chat.chat_id, data, mime="image/jpeg") == [m.RECEIVED]
    assert await bot.send_file(chat.chat_id, data) == [m.DUPLICATE]
    assert await bot.count(Document) == 1
    doc = (await bot.documents())[0]
    doc.status = DocumentStatus.FAILED
    await bot.session.commit()
    assert await bot.send_file(chat.chat_id, data) == [m.DUPLICATE_REQUEUED]
    await bot.session.refresh(doc)
    assert await bot.count(Document) == 1 and doc.status is DocumentStatus.QUEUED


async def test_file_error_mapping(bot: Bot) -> None:
    chat = await bot.linked()
    assert await bot.send_file(chat.chat_id, b"x" * 50, mime="application/zip") == [
        m.FILE_UNSUPPORTED
    ]
    assert await bot.send_file(chat.chat_id, b"not a known type at all") == [m.FILE_UNSUPPORTED]
    assert await bot.send_file(chat.chat_id, b"") == [m.FILE_EMPTY]
    big = pdf(junk=0)
    assert await bot.send_file(chat.chat_id, big, size=21 * 1024 * 1024) == [m.FILE_TOO_BIG]
    bot.deps.settings.max_upload_mb = 1  # the stream is cut at MAX_UPLOAD_MB too
    assert await bot.send_file(chat.chat_id, pdf() + b"0" * 1_100_000, size=None) == [
        m.FILE_TOO_BIG
    ]
    assert await bot.send_file(chat.chat_id, RuntimeError("secret detail")) == [m.FILE_FETCH_FAILED]
    assert await bot.count(Document) == 0


async def test_each_file_of_an_album_is_one_document(bot: Bot) -> None:
    chat = await bot.linked()
    for _ in range(3):
        assert await bot.send_file(chat.chat_id, jpeg()) == [m.RECEIVED]
    assert await bot.count(Document) == 3


# --- undo --------------------------------------------------------------------------


async def test_undo_deletes_the_last_telegram_document_of_the_user(bot: Bot) -> None:
    chat = await bot.linked()
    web = await make_document(bot.session, chat.household, chat.user, channel=Channel.WEB)
    assert await bot.send(chat.chat_id, "/undo") == [m.UNDO_NOTHING]
    await bot.send_file(chat.chat_id, pdf())
    await bot.send_file(chat.chat_id, pdf())
    newest = max(await bot.documents(), key=lambda d: (d.created_at, d.id))
    assert await bot.send(chat.chat_id, "/undo") == [m.UNDONE]  # also while queued
    ids = {d.id for d in await bot.documents()}
    assert web.id in ids and len(ids) == 2
    assert newest.id not in ids or newest.channel is Channel.WEB


async def test_undo_never_touches_another_household(bot: Bot) -> None:
    """#107: the Document select is scoped; a newer foreign Telegram document of the same
    uploader id (impossible via the app, possible in the DB) must survive."""
    chat = await bot.linked()
    other = await bot.linked()
    foreign = await make_document(
        bot.session,
        other.household,
        chat.user,
        channel=Channel.TELEGRAM,
        created_at=bot.clock.now() + timedelta(days=1),
    )
    await bot.session.commit()
    assert await bot.send(chat.chat_id, "/undo") == [m.UNDO_NOTHING]
    assert {d.id for d in await bot.documents()} == {foreign.id}


async def test_undo_busy_and_overridden(bot: Bot) -> None:
    chat = await bot.linked()
    await bot.send_file(chat.chat_id, pdf())
    doc = (await bot.documents())[0]
    job = (await bot.session.execute(select(Job))).scalar_one()
    job.status = JobStatus.RUNNING
    job.locked_until = bot.clock.now() + timedelta(minutes=1)
    await bot.session.commit()
    assert await bot.send(chat.chat_id, "/undo") == [m.UNDO_BUSY]
    job.status = JobStatus.QUEUED
    job.locked_until = None
    await make_tax_item(bot.session, chat.household, document_id=doc.id, overridden_by_user=True)
    await bot.session.commit()
    assert await bot.send(chat.chat_id, "/undo") == [m.UNDO_OVERRIDDEN]
    assert await bot.count(Document) == 1


# --- year, help --------------------------------------------------------------------


async def test_year_and_help(bot: Bot) -> None:
    chat = await bot.linked()
    assert await bot.send(chat.chat_id, "/year 2025") == [m.year_set(2025)]
    link = (await bot.session.execute(select(ChannelLink))).scalar_one()
    await bot.session.refresh(link)
    assert link.year == 2025
    assert await bot.send(chat.chat_id, "/year") == [m.year_set(2025)]
    assert (await bot.send(chat.chat_id, "/year 1999"))[0].startswith("Jahr nicht unterstützt")
    assert await bot.send(chat.chat_id, "/year abc") == [m.YEAR_BAD]
    assert await bot.send(chat.chat_id, "/gibtsnicht") == [m.HELP]
    assert await bot.send(chat.chat_id, "ein Satz") == [m.HELP]
    assert await bot.send(chat.chat_id, "/start@belegbot_test_bot") == [m.HELP]
    assert await bot.send(chat.chat_id, f"/link {'1' * 8}") == [m.ALREADY_LINKED]


async def test_default_year_without_link_year(bot: Bot) -> None:
    chat = await bot.linked()
    # FakeClock is in 2026 (supported): the default is the current year
    assert await bot.send(chat.chat_id, "/year") == [m.year_set(2026)]


# --- structure ---------------------------------------------------------------------


def test_core_imports_nothing_telegram_specific() -> None:
    source = (Path(__file__).parents[2] / "app" / "bot" / "core.py").read_text()
    for node in ast.walk(ast.parse(source)):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        assert not any("telegram" in n.lower() for n in names), names


async def test_users_table_untouched(bot: Bot) -> None:
    chat = await bot.linked()
    await bot.send(chat.chat_id, "/year 2025")
    user = (await bot.session.execute(select(AppUser))).scalar_one()
    assert user.disabled_at is None
