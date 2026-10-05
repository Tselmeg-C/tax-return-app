"""Observability: spans, resource, X-Trace-Id, request log, and no secrets in telemetry."""

from __future__ import annotations

import io
import json
import logging
import os
import re
import subprocess
import sys
import textwrap
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.api.main import create_app
from app.config import Settings
from app.observability import API_SERVICE_NAME, Observability, get_observability
from app.observability.logs import REDACTED, build_formatter, redact_sensitive

BACKEND_DIR = Path(__file__).resolve().parent.parent
TRACE_ID = re.compile(r"^[0-9a-f]{32}$")


@pytest.fixture(scope="session")
def observability() -> Observability:
    obs = get_observability()
    assert obs is not None, "app.api.main sets up observability on import"
    return obs


@pytest.fixture(scope="session")
def _session_exporter(observability: Observability) -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    observability.add_span_exporter(exporter)  # behind the same scrubbing processor as OTLP
    return exporter


@pytest.fixture
def spans(_session_exporter: InMemorySpanExporter) -> Iterator[InMemorySpanExporter]:
    _session_exporter.clear()
    yield _session_exporter
    _session_exporter.clear()


@pytest.fixture
def json_log() -> Iterator[io.StringIO]:
    """Captures the exact JSON lines the stdout handler would print."""
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(build_formatter(API_SERVICE_NAME))
    root = logging.getLogger()
    root.addHandler(handler)
    previous = root.level
    root.setLevel(logging.DEBUG)
    try:
        yield buffer
    finally:
        root.removeHandler(handler)
        root.setLevel(previous)


def _settings(database_url: str) -> Settings:
    return Settings(_env_file=None, database_url=database_url)  # type: ignore[call-arg]


@asynccontextmanager
async def client_for(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


def _dump(spans: list[ReadableSpan]) -> str:
    """Everything a span exporter would see, as one string."""
    return "\n".join(span.to_json() for span in spans)


def _log_lines(buffer: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in buffer.getvalue().splitlines() if line.strip()]


async def test_health_server_span_with_child_db_span(
    migrated_database: str, spans: InMemorySpanExporter
) -> None:
    async with client_for(_settings(migrated_database)) as client:
        response = await client.get("/health")
    assert response.status_code == 200

    finished = spans.get_finished_spans()
    servers = [s for s in finished if s.kind == SpanKind.SERVER]
    assert len(servers) == 1
    server = servers[0]
    assert server.name == "GET /health"
    assert server.attributes is not None
    assert server.attributes.get("http.route") == "/health"
    assert server.attributes.get("http.status_code") == 200

    db_spans = [
        s
        for s in finished
        if s.attributes is not None
        and "SELECT 1" in str(s.attributes.get("db.statement", s.attributes.get("db.query.text")))
    ]
    assert db_spans, [s.name for s in finished]
    assert db_spans[0].parent is not None
    assert db_spans[0].parent.span_id == server.context.span_id
    assert db_spans[0].context.trace_id == server.context.trace_id

    resource = server.resource.attributes
    assert resource["service.name"] == "belegbot-api"
    assert resource["service.version"] == "0.1.0"
    assert resource["deployment.environment.name"]


async def test_x_trace_id_matches_span_and_request_log(
    migrated_database: str, spans: InMemorySpanExporter, json_log: io.StringIO
) -> None:
    async with client_for(_settings(migrated_database)) as client:
        responses = [
            await client.get("/health"),
            await client.get("/version"),
            await client.get("/does-not-exist"),
        ]
    request_lines = [line for line in _log_lines(json_log) if line.get("event") == "request"]
    assert len(request_lines) == 3
    for response, line in zip(responses, request_lines, strict=True):
        trace_id = response.headers["x-trace-id"]
        assert TRACE_ID.match(trace_id)
        assert line["trace_id"] == trace_id
        assert line["status"] == response.status_code
        assert set(line) >= {"timestamp", "level", "event", "logger", "service", "duration_ms"}
        assert line["service"] == "belegbot-api"
    assert request_lines[0]["route"] == "/health"
    assert request_lines[2]["route"] == "<unmatched>"
    server_trace_ids = {
        format(s.context.trace_id, "032x")
        for s in spans.get_finished_spans()
        if s.kind == SpanKind.SERVER
    }
    assert {r.headers["x-trace-id"] for r in responses} == server_trace_ids


async def test_no_sentinels_in_spans_or_logs(
    migrated_database: str,
    spans: InMemorySpanExporter,
    json_log: io.StringIO,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    app_settings = _settings(migrated_database)
    async with client_for(app_settings) as client:
        response = await client.get(
            "/health?token=sentinel-q-123",
            headers={
                "Authorization": "Bearer sentinel-auth-123",
                "Cookie": "s=sentinel-cookie-123",
            },
        )
        assert response.status_code == 200
        # A query with a bound parameter, inside a request span context of its own.
        transport_app = client._transport.app  # type: ignore[attr-defined]
        engine = transport_app.state.engine
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT :p AS v"), {"p": "sentinel-param-123"})
            assert result.scalar_one() == "sentinel-param-123"

    finished = spans.get_finished_spans()
    assert any(
        "SELECT" in str((s.attributes or {}).get("db.statement", "")) and ":p" not in s.name
        for s in finished
    )
    exported = _dump(list(finished))
    logged = json_log.getvalue() + caplog.text
    for sentinel in (
        "sentinel-q-123",
        "sentinel-auth-123",
        "sentinel-cookie-123",
        "sentinel-param-123",
    ):
        assert sentinel not in exported, sentinel
        assert sentinel not in logged, sentinel
    assert "token=" not in exported


async def test_wrong_db_password_leaks_nothing(
    migrated_database: str,
    spans: InMemorySpanExporter,
    json_log: io.StringIO,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    bad_url = make_url(migrated_database).set(password="wrong-pw")
    bad_url_str = bad_url.render_as_string(hide_password=False)
    async with client_for(_settings(bad_url_str)) as client:
        response = await client.get("/health")
    assert response.status_code == 503

    exported = _dump(list(spans.get_finished_spans()))
    logged = json_log.getvalue() + caplog.text
    assert "health: db check failed (OperationalError)" in logged
    for needle in ("wrong-pw", bad_url_str, bad_url.render_as_string(hide_password=True)):
        assert needle not in exported
        assert needle not in logged
    for span in spans.get_finished_spans():
        assert not span.status.description
        for event in span.events:
            assert set((event.attributes or {}).keys()) <= {"exception.type"}


def test_redaction_processor() -> None:
    event = {
        "event": "login",
        "password": "p",
        "token": "t",
        "steuer_id": "12345678901",
        "authorization": "Bearer x",
        "cookie": "s=1",
        "database_url": "postgresql://u:p@h/db",
        "Session_Token": "x",
        "document_content": "x",
        "ocr_text": "x",
        "nested": {"api_secret": "s", "ok": 1},
        "user": "anna",
        "url": "postgresql://belegbot:hunter2@db:5432/belegbot",
    }
    out = redact_sensitive(None, "info", dict(event))
    for key in (
        "password",
        "token",
        "steuer_id",
        "authorization",
        "cookie",
        "database_url",
        "Session_Token",
        "document_content",
        "ocr_text",
    ):
        assert out[key] == REDACTED, key
    assert out["nested"] == {"api_secret": REDACTED, "ok": 1}
    assert out["user"] == "anna"
    assert out["event"] == "login"
    assert "hunter2" not in out["url"]


def test_no_exporter_without_endpoint(observability: Observability) -> None:
    assert os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") is None
    assert observability.exporters == []
    assert observability.logger_provider is None


def _run_python(code: str, env_overrides: dict[str, str], timeout: float = 60) -> str:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("OTEL_") and k not in ("RAILWAY_ENVIRONMENT_NAME",)
    }
    env.update(env_overrides)
    proc = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    return proc.stdout + proc.stderr


@pytest.mark.parametrize(
    ("service", "module"),
    [("belegbot-api", "API_SERVICE_NAME"), ("belegbot-worker", "WORKER_SERVICE_NAME")],
)
def test_service_name_from_code_wins_over_env(service: str, module: str) -> None:
    out = _run_python(
        f"""
        from app.observability import setup_observability, {module}
        obs = setup_observability({module})
        print("NAME=" + obs.tracer_provider.resource.attributes["service.name"])
        """,
        {"OTEL_SERVICE_NAME": "something-else", "RAILWAY_ENVIRONMENT_NAME": "pr-42"},
    )
    assert f"NAME={service}" in out
    assert "something-else" not in out


def test_environment_and_commit_on_resource() -> None:
    from app.observability import build_resource

    attrs = build_resource(
        "belegbot-api",
        {
            "RAILWAY_ENVIRONMENT_NAME": "tax-return-app-pr-42",
            "APP_ENV": "production",
            "GIT_SHA": "",
            "RAILWAY_GIT_COMMIT_SHA": "abc123",
        },
    ).attributes
    assert attrs["deployment.environment.name"] == "tax-return-app-pr-42"
    assert attrs["vcs.ref.head.revision"] == "abc123"
    assert (
        build_resource("x", {"APP_ENV": "production"}).attributes["deployment.environment.name"]
        == "production"
    )


def test_disabled_export_logs_once_and_has_no_exporters() -> None:
    out = _run_python(
        """
        from app.observability import setup_observability
        obs = setup_observability("belegbot-api")
        setup_observability("belegbot-api")
        print("EXPORTERS=" + ",".join(obs.exporters))
        """,
        {},
    )
    assert out.count("otel export disabled") == 1
    assert "EXPORTERS=\n" in out or out.rstrip().endswith("EXPORTERS=")


def test_unreachable_endpoint_never_errors_or_leaks_headers() -> None:
    out = _run_python(
        """
        import logging, time
        from opentelemetry import trace
        from app.observability import setup_observability
        obs = setup_observability("belegbot-api")
        assert "otlp-traces" in obs.exporters, obs.exporters
        tracer = trace.get_tracer("t")
        started = time.monotonic()
        with tracer.start_as_current_span("probe"):
            logging.getLogger("app.test").info("hello")
        assert time.monotonic() - started < 1
        obs.force_flush(timeout=3)
        obs.shutdown(timeout=3)
        print("DONE")
        """,
        {
            "OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:1",
            "OTEL_EXPORTER_OTLP_HEADERS": "Authorization=Basic%20sentinel-token-123",
            "OTEL_EXPORTER_OTLP_TIMEOUT": "2",
        },
    )
    assert "DONE" in out
    assert out.count("sentinel-token-123") == 0
    for line in out.splitlines():
        if line.startswith("{"):
            assert json.loads(line)["level"] in ("debug", "info", "warning"), line


def test_console_exporter_prints_span_json_lines() -> None:
    out = _run_python(
        """
        from opentelemetry import trace
        from app.observability import setup_observability
        setup_observability("belegbot-api")
        with trace.get_tracer("t").start_as_current_span("GET /probe?secret=1"):
            pass
        """,
        {"OTEL_TRACES_EXPORTER": "console"},
    )
    span_lines = [json.loads(line) for line in out.splitlines() if '"context"' in line]
    assert span_lines and span_lines[0]["name"] == "GET /probe"
