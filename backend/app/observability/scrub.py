"""Span scrubbing: every exporter only ever sees sanitised copies of finished spans.

- Query strings are removed from URL attributes (`url.query` is dropped; the `?…` part of
  `http.url`, `url.full`, `http.target` is cut off).
- Captured header attributes (`http.request.header.*`, `http.response.header.*`) are dropped.
- Exception events keep only `exception.type`; other events are scrubbed like attributes.
- Status descriptions (often `str(exception)`, which may contain hosts or URLs) are dropped.
- Credentials in any URL-like string are masked.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from opentelemetry.context import Context
from opentelemetry.sdk.trace import Event, ReadableSpan, Span, SpanProcessor
from opentelemetry.trace import Status

from app.observability.logs import mask_url_credentials

_DROP_KEYS = frozenset({"url.query", "db.query.parameter", "db.statement.parameters"})
_STRIP_QUERY_KEYS = frozenset({"http.url", "url.full", "http.target", "url.path"})
_DROP_PREFIXES = ("http.request.header.", "http.response.header.", "db.query.parameter.")
_EXCEPTION_EVENT = "exception"


def _strip_query(value: str) -> str:
    return value.split("?", 1)[0].split("#", 1)[0]


def _clean_value(value: Any) -> Any:
    if isinstance(value, str):
        return mask_url_credentials(value)
    return value


def scrub_attributes(
    attributes: Mapping[str, Any] | None,
) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in (attributes or {}).items():
        if key in _DROP_KEYS or key.startswith(_DROP_PREFIXES):
            continue
        if key in _STRIP_QUERY_KEYS and isinstance(value, str):
            value = _strip_query(value)
        cleaned[key] = _clean_value(value)
    return cleaned


def _scrub_event(event: Event) -> Event:
    if event.name == _EXCEPTION_EVENT:
        attrs = event.attributes or {}
        kept: dict[str, Any] = {}
        if "exception.type" in attrs:
            kept["exception.type"] = attrs["exception.type"]
        return Event(_EXCEPTION_EVENT, kept, event.timestamp)
    return Event(event.name, scrub_attributes(event.attributes), event.timestamp)


def scrub_span(span: ReadableSpan) -> ReadableSpan:
    return ReadableSpan(
        name=_strip_query(mask_url_credentials(span.name)),
        context=span.context,
        parent=span.parent,
        resource=span.resource,
        attributes=scrub_attributes(span.attributes),
        events=[_scrub_event(e) for e in span.events],
        links=span.links,
        kind=span.kind,
        status=Status(span.status.status_code),
        start_time=span.start_time,
        end_time=span.end_time,
        instrumentation_scope=span.instrumentation_scope,
    )


class ScrubbingSpanProcessor(SpanProcessor):
    """Wraps a processor (batch/simple + exporter) and hands it scrubbed span copies."""

    def __init__(self, delegate: SpanProcessor) -> None:
        self._delegate = delegate

    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        self._delegate.on_start(span, parent_context=parent_context)

    def on_end(self, span: ReadableSpan) -> None:
        self._delegate.on_end(scrub_span(span))

    def shutdown(self) -> None:
        self._delegate.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._delegate.force_flush(timeout_millis)
