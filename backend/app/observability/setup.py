"""OpenTelemetry + logging setup shared by the api and the worker.

Configuration comes only from the standard env vars:

- `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_HEADERS` (+ `OTEL_EXPORTER_OTLP_PROTOCOL`,
  only `http/protobuf` is supported): OTLP export of traces, metrics and logs.
- Endpoint unset/empty or `OTEL_SDK_DISABLED=true`: nothing is exported and nothing connects
  anywhere (`otel export disabled` is logged once). A tracer provider is installed either way,
  so trace ids exist for log correlation and the `X-Trace-Id` header.
- `OTEL_TRACES_EXPORTER=console`: spans are printed to stdout as JSON lines (local debugging).
"""

from __future__ import annotations

import atexit
import logging
import os
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from importlib.metadata import version as package_version

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExporter,
)

from app.observability.logs import configure_logging
from app.observability.scrub import ScrubbingSpanProcessor

API_SERVICE_NAME = "belegbot-api"
WORKER_SERVICE_NAME = "belegbot-worker"
SHUTDOWN_TIMEOUT_SECONDS = 5.0

logger = logging.getLogger("app.observability")


def _env(env: Mapping[str, str], name: str) -> str:
    return env.get(name, "").strip()


def export_enabled(env: Mapping[str, str]) -> bool:
    if _env(env, "OTEL_SDK_DISABLED").lower() == "true":
        return False
    return bool(_env(env, "OTEL_EXPORTER_OTLP_ENDPOINT"))


def build_resource(service_name: str, env: Mapping[str, str]) -> Resource:
    """Resource for one process. `service.name` comes from code and wins over the env."""
    environment = _env(env, "RAILWAY_ENVIRONMENT_NAME") or _env(env, "APP_ENV") or "development"
    attributes: dict[str, str] = {
        "service.version": package_version("belegbot"),
        "deployment.environment.name": environment,
    }
    commit = _env(env, "GIT_SHA") or _env(env, "RAILWAY_GIT_COMMIT_SHA")
    if commit:
        attributes["vcs.ref.head.revision"] = commit
    # Resource.create also reads OTEL_RESOURCE_ATTRIBUTES / OTEL_SERVICE_NAME; the final merge
    # makes the name from code win, so api and worker in one container never share a name.
    return Resource.create(attributes).merge(Resource({"service.name": service_name}))


def _console_span_exporter() -> SpanExporter:
    # One JSON object per line, so stdout stays line-parseable.
    return ConsoleSpanExporter(formatter=lambda span: span.to_json(indent=None) + os.linesep)


@dataclass
class Observability:
    service_name: str
    tracer_provider: TracerProvider
    meter_provider: MeterProvider
    logger_provider: LoggerProvider | None
    exporters: list[str] = field(default_factory=list)
    _shutdown_done: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def add_span_exporter(self, exporter: SpanExporter, *, batch: bool = False) -> None:
        """Attach an exporter behind the scrubbing processor (also used by tests)."""
        processor: SpanProcessor = (
            BatchSpanProcessor(exporter) if batch else SimpleSpanProcessor(exporter)
        )
        self.tracer_provider.add_span_processor(ScrubbingSpanProcessor(processor))

    def force_flush(self, timeout: float = SHUTDOWN_TIMEOUT_SECONDS) -> None:
        self._bounded(self._flush, timeout)

    def shutdown(self, timeout: float = SHUTDOWN_TIMEOUT_SECONDS) -> None:
        with self._lock:
            if self._shutdown_done:
                return
            self._shutdown_done = True
        self._bounded(self._shutdown, timeout)

    def _flush(self) -> None:
        millis = int(SHUTDOWN_TIMEOUT_SECONDS * 1000)
        self.tracer_provider.force_flush(millis)
        self.meter_provider.force_flush(millis)
        if self.logger_provider is not None:
            self.logger_provider.force_flush(millis)

    def _shutdown(self) -> None:
        self.tracer_provider.shutdown()
        self.meter_provider.shutdown()
        if self.logger_provider is not None:
            self.logger_provider.shutdown()

    @staticmethod
    def _bounded(fn: Callable[[], None], timeout: float) -> None:
        # Exporters retry with backoff; never let that block process shutdown for long.
        thread = threading.Thread(target=fn, name="otel-shutdown", daemon=True)
        thread.start()
        thread.join(timeout)


_current: Observability | None = None
_setup_lock = threading.Lock()


def get_observability() -> Observability | None:
    return _current


def setup_observability(service_name: str, env: Mapping[str, str] | None = None) -> Observability:
    """Configure logging and OTel for this process (idempotent per process)."""
    global _current
    with _setup_lock:
        if _current is not None:
            return _current
        environ: Mapping[str, str] = os.environ if env is None else env
        resource = build_resource(service_name, environ)
        exporting = export_enabled(environ)
        exporters: list[str] = []

        tracer_provider = TracerProvider(resource=resource, shutdown_on_exit=False)
        traces_exporter = _env(environ, "OTEL_TRACES_EXPORTER").lower() or "otlp"
        if exporting and "otlp" in traces_exporter:
            tracer_provider.add_span_processor(
                ScrubbingSpanProcessor(BatchSpanProcessor(OTLPSpanExporter()))
            )
            exporters.append("otlp-traces")
        if "console" in traces_exporter:
            tracer_provider.add_span_processor(
                ScrubbingSpanProcessor(SimpleSpanProcessor(_console_span_exporter()))
            )
            exporters.append("console-traces")

        readers = [PeriodicExportingMetricReader(OTLPMetricExporter())] if exporting else []
        if exporting:
            exporters.append("otlp-metrics")
        meter_provider = MeterProvider(
            resource=resource, metric_readers=readers, shutdown_on_exit=False
        )

        logger_provider: LoggerProvider | None = None
        if exporting:
            logger_provider = LoggerProvider(resource=resource, shutdown_on_exit=False)
            logger_provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
            exporters.append("otlp-logs")

        configure_logging(
            service_name,
            level=_env(environ, "LOG_LEVEL") or "INFO",
            otel_provider=logger_provider,
        )
        trace.set_tracer_provider(tracer_provider)
        metrics.set_meter_provider(meter_provider)

        _current = Observability(
            service_name=service_name,
            tracer_provider=tracer_provider,
            meter_provider=meter_provider,
            logger_provider=logger_provider,
            exporters=exporters,
        )
        atexit.register(_current.shutdown)

        if not exporting:
            logger.info("otel export disabled")
        protocol = _env(environ, "OTEL_EXPORTER_OTLP_PROTOCOL")
        if exporting and protocol and protocol != "http/protobuf":
            logger.warning("OTEL_EXPORTER_OTLP_PROTOCOL is not http/protobuf; using http/protobuf")
        return _current
