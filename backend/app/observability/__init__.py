"""Observability: structlog JSON logs + OpenTelemetry (traces, metrics, logs) → OTLP.

`setup_observability(service_name)` is called once per process by the api and the worker.
Spans and log lines never carry bodies, query strings, cookies, auth headers, DB URLs,
the Steuer-ID or document contents.
"""

from app.observability.setup import (
    API_SERVICE_NAME,
    WORKER_SERVICE_NAME,
    Observability,
    build_resource,
    export_enabled,
    get_observability,
    setup_observability,
)

__all__ = [
    "API_SERVICE_NAME",
    "WORKER_SERVICE_NAME",
    "Observability",
    "build_resource",
    "export_enabled",
    "get_observability",
    "setup_observability",
]
