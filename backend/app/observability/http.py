"""FastAPI / SQLAlchemy instrumentation, request log line and the `X-Trace-Id` header."""

from __future__ import annotations

import time
from typing import Any

import structlog
from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.propagators import (
    ResponsePropagator,
    set_global_response_propagator,
)
from opentelemetry.instrumentation.sqlalchemy.engine import EngineTracer
from opentelemetry.metrics import get_meter
from opentelemetry.propagators import textmap
from opentelemetry.semconv.metrics import MetricInstruments
from opentelemetry.trace import get_tracer
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.observability.setup import Observability

TRACE_ID_HEADER = "X-Trace-Id"

request_logger = structlog.stdlib.get_logger("app.api.request")


class TraceIdResponsePropagator(ResponsePropagator):
    """Adds `X-Trace-Id: <32 hex>` to every response start (incl. error responses).

    Called by the OTel ASGI middleware, which is the outermost layer, for each ASGI send.
    """

    def inject(
        self,
        carrier: textmap.CarrierT,
        context: Context | None = None,
        setter: textmap.Setter[textmap.CarrierT] = textmap.default_setter,
    ) -> None:
        if not isinstance(carrier, dict) or carrier.get("type") != "http.response.start":
            return
        span_context = trace.get_current_span(context).get_span_context()
        if not span_context.is_valid:
            return
        setter.set(carrier, TRACE_ID_HEADER, trace.format_trace_id(span_context.trace_id))


class RequestLogMiddleware:
    """One log line per request: method, route template, status, duration, trace id.

    Never the raw path, query string, headers or body.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        status = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            route = scope.get("route")
            request_logger.info(
                "request",
                method=scope.get("method", ""),
                route=getattr(route, "path", None) or "<unmatched>",
                status=status,
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )


def instrument_app(app: FastAPI, observability: Observability) -> None:
    app.add_middleware(RequestLogMiddleware)
    set_global_response_propagator(TraceIdResponsePropagator())  # type: ignore[no-untyped-call]
    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=observability.tracer_provider,
        meter_provider=observability.meter_provider,
        exclude_spans=["receive", "send"],
        # No header capture, whatever OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_* says.
        http_capture_headers_server_request=[],
        http_capture_headers_server_response=[],
    )


def instrument_engine(engine: AsyncEngine, observability: Observability) -> Any:
    """Statement spans for the async engine (placeholders only, no bound params, no commenter)."""
    tracer = get_tracer(__name__, tracer_provider=observability.tracer_provider)
    meter = get_meter(__name__, meter_provider=observability.meter_provider)
    connections_usage = meter.create_up_down_counter(
        name=MetricInstruments.DB_CLIENT_CONNECTIONS_USAGE,
        unit="connections",
        description="Number of connections in the state given by the state attribute.",
    )
    return EngineTracer(  # type: ignore[no-untyped-call]
        tracer, engine.sync_engine, connections_usage, enable_commenter=False
    )
