"""Worker notifications (#11): `DocumentProcessed` -> the uploader's chat, Telegram only."""

from __future__ import annotations

from decimal import Decimal

from app.bot import messages as m
from app.bot.notify import make_subscriber
from app.domain.enums import (
    AttentionReason,
    Category,
    Channel,
    DocType,
    DocumentStatus,
)
from app.pipeline.events import DocumentProcessed
from tests.bot.conftest import Bot, Chat
from tests.domain.factories import make_document, make_tax_item


async def processed(
    bot: Bot,
    chat: Chat,
    *,
    channel: Channel = Channel.TELEGRAM,
    status: DocumentStatus = DocumentStatus.DONE,
    reason: AttentionReason | None = None,
    with_item: bool = True,
) -> list[str]:
    doc = await make_document(
        bot.session,
        chat.household,
        chat.user,
        channel=channel,
        status=status,
        attention_reason=reason,
    )
    if with_item:
        await make_tax_item(
            bot.session,
            chat.household,
            document_id=doc.id,
            category=Category.HANDWERKERLEISTUNG,
            gross_amount=Decimal("312.40"),
            deductible_amount=Decimal("180.00"),
            labour_share_35a=Decimal("180.00"),
            vendor="Muster Sanitaer",
        )
    await bot.session.commit()
    subscriber = make_subscriber(bot.adapter, bot.deps.sessionmaker, "https://app.test")
    before = len(bot.adapter.sent)
    await subscriber(
        DocumentProcessed(doc.id, chat.household.id, channel, status, reason, DocType.GENERIC_BILL)
    )
    return [t for _, t in bot.adapter.sent[before:]]


async def test_done_message_has_category_and_amounts(bot: Bot) -> None:
    chat = await bot.linked()
    [text] = await processed(bot, chat)
    assert "Handwerkerleistung" in text and "312,40 EUR" in text
    assert "§ 35a Lohnanteil 180,00 EUR" in text
    assert bot.adapter.sent[-1][0] == chat.chat_id


async def test_needs_attention_and_failed(bot: Bot) -> None:
    chat = await bot.linked()
    [text] = await processed(
        bot,
        chat,
        status=DocumentStatus.NEEDS_ATTENTION,
        reason=AttentionReason.LABOUR_SHARE_MISSING,
        with_item=False,
    )
    assert "Arbeitskostenanteil fehlt" in text and text.endswith("https://app.test/belege")
    assert await processed(bot, chat, status=DocumentStatus.FAILED, with_item=False) == [
        m.FAILED_DOC
    ]


async def test_nothing_for_web_uploads_unlinked_or_disabled(bot: Bot) -> None:
    chat = await bot.linked()
    assert await processed(bot, chat, channel=Channel.WEB) == []
    other = await bot.linked()
    other.user.disabled_at = bot.clock.now()
    await bot.session.flush()
    assert await processed(bot, other) == []
    await bot.send(chat.chat_id, "/unlink")
    assert await processed(bot, chat) == []


async def test_send_failure_never_raises(bot: Bot) -> None:
    chat = await bot.linked()

    async def boom(external_id: str, text: str) -> None:
        raise RuntimeError("telegram down")

    bot.adapter.send_text = boom  # type: ignore[method-assign]
    assert await processed(bot, chat) == []
