"""Worker subscriber (#11): tell the uploader's chat what became of a Telegram upload.

Runs in the worker process on `DocumentProcessed` (non-durable, as accepted in #9). Only
`channel == telegram` documents are notified, to the link of `document.uploaded_by_user_id`;
web uploads and unlinked / disabled users get nothing. The text may hold amounts and vendors
(the user's own chat); logs carry ids and the class name only.
"""

from __future__ import annotations

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot import messages as m
from app.bot.core import ChannelAdapter
from app.bot.links import find_link_of_user
from app.db.models import Document, TaxItem
from app.db.scope import HouseholdScope
from app.domain.enums import LABELS_DE, Category, Channel, DocType, DocumentStatus
from app.pipeline.events import DocumentProcessed, Subscriber

log = structlog.stdlib.get_logger("app.bot")


def _item_text(item: TaxItem) -> str:
    parts = [LABELS_DE[Category][item.category], m.eur(item.gross_amount)]
    if item.vendor:
        parts.insert(0, item.vendor)
    if item.labour_share_35a is not None:
        parts.append(f"§ 35a Lohnanteil {m.eur(item.labour_share_35a)}")
    return ", ".join(parts)


async def _text(
    scope: HouseholdScope, doc: Document, event: DocumentProcessed, base_url: str
) -> str:
    if event.status is DocumentStatus.NEEDS_ATTENTION:
        return m.needs_attention(event.attention_reason, base_url)
    if event.status is not DocumentStatus.DONE:
        return m.FAILED_DOC
    items = (
        (await scope.session.execute(scope.select(TaxItem).where(TaxItem.document_id == doc.id)))
        .scalars()
        .all()
    )
    if not items:
        label = LABELS_DE[DocType][event.doc_type] if event.doc_type else ""
        return f"{m.DONE_NO_ITEM} ({label})" if label else m.DONE_NO_ITEM
    return "Beleg erkannt: " + "; ".join(_item_text(i) for i in items)


async def notify_processed(
    event: DocumentProcessed,
    adapter: ChannelAdapter,
    sessionmaker: async_sessionmaker[AsyncSession],
    base_url: str,
) -> None:
    if event.channel is not Channel.TELEGRAM:
        return
    try:
        async with sessionmaker() as session:
            scope = HouseholdScope(session, event.household_id)
            doc = await scope.get(Document, event.document_id)
            if doc is None:
                return
            target = await find_link_of_user(session, doc.uploaded_by_user_id, Channel.TELEGRAM)
            if target is None:
                return
            text = await _text(scope, doc, event, base_url)
        await adapter.send_text(target[1], text)
    except Exception as exc:  # never raise into the job
        log.warning(
            "bot.notify_failed", document_id=str(event.document_id), error_kind=type(exc).__name__
        )


def make_subscriber(
    adapter: ChannelAdapter, sessionmaker: async_sessionmaker[AsyncSession], base_url: str
) -> Subscriber:
    async def subscriber(event: DocumentProcessed) -> None:
        await notify_processed(event, adapter, sessionmaker, base_url)

    return subscriber
