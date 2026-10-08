"""`DocumentProcessed` and its in-process subscribers (#9; #11 subscribes the Telegram notice).

Published after the final commit. Ids and codes only. A failing subscriber is logged by
class name and never changes the job outcome.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import structlog

from app.domain.enums import AttentionReason, Channel, DocType, DocumentStatus

log = structlog.stdlib.get_logger("app.pipeline")


@dataclass(frozen=True)
class DocumentProcessed:
    document_id: uuid.UUID
    household_id: uuid.UUID
    channel: Channel
    status: DocumentStatus
    attention_reason: AttentionReason | None
    doc_type: DocType | None


Subscriber = Callable[[DocumentProcessed], Awaitable[None]]
_subscribers: list[Subscriber] = []


def subscribe(fn: Subscriber) -> None:
    _subscribers.append(fn)


def unsubscribe(fn: Subscriber) -> None:
    if fn in _subscribers:
        _subscribers.remove(fn)


async def publish(event: DocumentProcessed) -> None:
    for fn in list(_subscribers):
        try:
            await fn(event)
        except Exception as exc:
            log.warning(
                "pipeline.subscriber_failed",
                document_id=str(event.document_id),
                error_kind=type(exc).__name__,
            )
