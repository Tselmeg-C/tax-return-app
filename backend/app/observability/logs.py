"""structlog JSON logging: one JSON object per stdout line, for structlog and stdlib loggers.

Every line has `timestamp` (ISO 8601 UTC), `level`, `event`, `logger`, `service`, and
`trace_id` / `span_id` when inside a span. Values of sensitive keys are redacted and
credentials in URLs are masked as a safety net; code must still never log secrets,
document contents or the Steuer-ID.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from collections.abc import Mapping
from typing import Any

import structlog
from opentelemetry import trace
from opentelemetry._logs import LoggerProvider, LogRecord, SeverityNumber
from structlog.typing import EventDict, Processor, WrappedLogger

REDACTED = "[redacted]"
SENSITIVE_KEY = re.compile(
    r"password|secret|token|authorization|cookie|steuer_id|tax_id|database_url|content|text",
    re.IGNORECASE,
)
# `scheme://user:password@host` → `scheme://user:[redacted]@host`
_URL_CREDENTIALS = re.compile(r"(?P<prefix>[a-zA-Z][a-zA-Z0-9+.-]*://[^/\s:@]*):[^@\s/]*@")

# Fields that are part of the line format itself and never redacted.
_CORE_FIELDS = frozenset(
    {"timestamp", "level", "event", "logger", "service", "trace_id", "span_id"}
)

# Third-party loggers whose own handlers would print non-JSON lines.
_RELAYED_LOGGERS = ("uvicorn", "uvicorn.error", "alembic", "sqlalchemy")


def mask_url_credentials(value: str) -> str:
    return _URL_CREDENTIALS.sub(rf"\g<prefix>:{REDACTED}@", value)


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return mask_url_credentials(value)
    if isinstance(value, Mapping):
        return _redact_mapping(value)
    if isinstance(value, list | tuple):
        return [_redact_value(v) for v in value]
    return value


def _redact_mapping(mapping: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: REDACTED if SENSITIVE_KEY.search(str(key)) else _redact_value(value)
        for key, value in mapping.items()
    }


def redact_sensitive(_logger: WrappedLogger, _method: str, event_dict: EventDict) -> EventDict:
    """Replace values of sensitive keys (any depth) with `[redacted]`; mask URL credentials."""
    for key in list(event_dict):
        value = event_dict[key]
        if key not in _CORE_FIELDS and SENSITIVE_KEY.search(key):
            event_dict[key] = REDACTED
        elif key == "event" and isinstance(value, str):
            event_dict[key] = mask_url_credentials(value)
        elif key not in _CORE_FIELDS:
            event_dict[key] = _redact_value(value)
    return event_dict


def add_trace_context(_logger: WrappedLogger, _method: str, event_dict: EventDict) -> EventDict:
    ctx = trace.get_current_span().get_span_context()
    if ctx.is_valid:
        event_dict["trace_id"] = format(ctx.trace_id, "032x")
        event_dict["span_id"] = format(ctx.span_id, "016x")
    return event_dict


def _add_service(service: str) -> Processor:
    def processor(_logger: WrappedLogger, _method: str, event_dict: EventDict) -> EventDict:
        event_dict.setdefault("service", service)
        return event_dict

    return processor


def _logger_name(_logger: WrappedLogger, _method: str, event_dict: EventDict) -> EventDict:
    record = event_dict.get("_record")
    if "logger" not in event_dict:
        if isinstance(record, logging.LogRecord):
            event_dict["logger"] = record.name
        else:
            name = getattr(_logger, "name", None)
            event_dict["logger"] = name if isinstance(name, str) else "app"
    return event_dict


def _downgrade_otel_errors(
    _logger: WrappedLogger, _method: str, event_dict: EventDict
) -> EventDict:  # noqa: E501
    """Exporter failures (unreachable endpoint, 401) are warnings, without tracebacks."""
    if str(event_dict.get("logger", "")).startswith("opentelemetry") and event_dict.get(
        "level"
    ) in ("error", "critical"):
        event_dict["level"] = "warning"
        event_dict.pop("exc_info", None)
        event_dict.pop("exception", None)
    return event_dict


def build_formatter(service: str) -> structlog.stdlib.ProcessorFormatter:
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    pre_chain: list[Processor] = [
        structlog.stdlib.add_log_level,
        timestamper,
    ]
    return structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=pre_chain,
        processors=[
            _logger_name,
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            _add_service(service),
            add_trace_context,
            _downgrade_otel_errors,
            structlog.processors.format_exc_info,
            redact_sensitive,
            structlog.processors.JSONRenderer(),
        ],
        pass_foreign_args=False,
    )


class _NotFromOtel(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not record.name.startswith("opentelemetry")


_SEVERITY = {
    "debug": SeverityNumber.DEBUG,
    "info": SeverityNumber.INFO,
    "warning": SeverityNumber.WARN,
    "error": SeverityNumber.ERROR,
    "critical": SeverityNumber.FATAL,
}


class OtelLogHandler(logging.Handler):
    """Stdlib → OTel logs bridge: sends the same redacted JSON fields to the OTLP exporter.

    The record body is the `event`; the other fields become attributes. The current span
    context is attached, so Loki lines link to the trace. The SDK's own (`opentelemetry.*`)
    records are not exported, to avoid feedback loops.
    """

    def __init__(self, provider: LoggerProvider, formatter: logging.Formatter) -> None:
        super().__init__()
        self._provider = provider
        self.setFormatter(formatter)
        self.addFilter(_NotFromOtel())

    def emit(self, record: logging.LogRecord) -> None:
        try:
            fields: dict[str, Any] = json.loads(self.format(record))
            level = str(fields.pop("level", "info"))
            event = fields.pop("event", "")
            for key in ("timestamp", "trace_id", "span_id"):
                fields.pop(key, None)
            attributes = {
                k: v if isinstance(v, str | bool | int | float) else json.dumps(v)
                for k, v in fields.items()
            }
            self._provider.get_logger(record.name).emit(
                LogRecord(
                    timestamp=int(record.created * 1e9),
                    severity_text=level.upper(),
                    severity_number=_SEVERITY.get(level, SeverityNumber.INFO),
                    body=event if isinstance(event, str) else json.dumps(event),
                    attributes=attributes,
                )
            )
        except Exception:  # never let telemetry break logging
            self.handleError(record)


def _structlog_processors() -> list[Processor]:
    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
    ]


def configure_logging(
    service: str,
    level: str = "INFO",
    otel_provider: LoggerProvider | None = None,
) -> structlog.stdlib.ProcessorFormatter:
    """Route structlog and stdlib logging to JSON lines on stdout (and OTLP if given)."""
    formatter = build_formatter(service)
    stdout = logging.StreamHandler(sys.stdout)
    stdout.setFormatter(formatter)

    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_belegbot", False):
            root.removeHandler(handler)
    stdout._belegbot = True  # type: ignore[attr-defined]
    root.addHandler(stdout)
    if otel_provider is not None:
        otel = OtelLogHandler(otel_provider, build_formatter(service))
        otel._belegbot = True  # type: ignore[attr-defined]
        root.addHandler(otel)
    root.setLevel(level.upper())

    # uvicorn/alembic install their own (non-JSON) handlers; send everything through ours.
    for name in _RELAYED_LOGGERS:
        lib_logger = logging.getLogger(name)
        lib_logger.handlers.clear()
        lib_logger.propagate = True
    # HTTP client libraries log full request URLs (query strings) at INFO.
    for name in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(name).setLevel(logging.WARNING)
    # The access log prints query strings; the request log middleware replaces it.
    access = logging.getLogger("uvicorn.access")
    access.handlers.clear()
    access.propagate = False
    access.disabled = True

    structlog.configure(
        processors=_structlog_processors(),
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    return formatter


def set_level(level: str) -> None:
    logging.getLogger().setLevel(level.upper())
