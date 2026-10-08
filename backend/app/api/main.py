"""FastAPI application (`uvicorn app.api.main:app`)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from importlib.metadata import version as package_version

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse
from opentelemetry.metrics import MeterProvider
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api import auth, documents, meta, tax_items
from app.api.deps import require_session
from app.api.security import CsrfMiddleware, NoStoreMiddleware
from app.auth.clock import Clock, SystemClock
from app.auth.mail import MailBackend, MailDispatcher, backend_from_settings
from app.auth.ratelimit import RateLimiter
from app.config import Settings, check_api_settings, get_settings
from app.db.session import create_engine, create_sessionmaker
from app.observability import API_SERVICE_NAME, setup_observability
from app.observability.http import instrument_app, instrument_engine
from app.observability.logs import set_level
from app.queue.metrics import UploadMetrics
from app.storage import storage_from_settings
from app.tax_items import TaxItemMetrics

logger = logging.getLogger("app.api")

HEALTH_DB_TIMEOUT_SECONDS = 2.0
MAIL_DRAIN_TIMEOUT_SECONDS = 5.0
RATE_WINDOW = timedelta(minutes=15)
MAGIC_LINK_REQUESTS_PER_IP = 10
VERIFY_FAILURES_PER_IP = 20


def create_app(
    settings: Settings | None = None,
    *,
    clock: Clock | None = None,
    mail_backend: MailBackend | None = None,
    meter_provider: MeterProvider | None = None,
) -> FastAPI:
    """Build the app. Settings are resolved at startup (not import); no DB connection is opened.

    Observability (JSON logging, OTel) is set up here, at import time of `app.api.main`, so
    uvicorn's own startup lines are already JSON. It never connects anywhere by itself.
    `clock`, `mail_backend` and `meter_provider` are injectable for tests.
    """
    observability = setup_observability(API_SERVICE_NAME)
    the_clock: Clock = clock or SystemClock()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        resolved = settings if settings is not None else get_settings()
        check_api_settings(resolved)  # production rules; names variables, never values
        set_level(resolved.log_level)
        storage = storage_from_settings(resolved)
        # Fails startup naming STORAGE_PATH (class name only, never the OS message).
        await asyncio.to_thread(storage.probe)
        engine = create_engine(resolved)
        instrument_engine(engine, observability)
        app.state.settings = resolved
        app.state.engine = engine
        app.state.sessionmaker = create_sessionmaker(engine)
        app.state.clock = the_clock
        app.state.storage = storage
        app.state.upload_metrics = UploadMetrics(meter_provider)
        app.state.tax_item_metrics = TaxItemMetrics(meter_provider)
        app.state.mailer = MailDispatcher(mail_backend or backend_from_settings(resolved))
        app.state.magic_link_limiter = RateLimiter(
            MAGIC_LINK_REQUESTS_PER_IP, RATE_WINDOW, the_clock
        )
        app.state.verify_failure_limiter = RateLimiter(
            VERIFY_FAILURES_PER_IP, RATE_WINDOW, the_clock
        )
        try:
            yield
        finally:
            await app.state.mailer.drain(timeout=MAIL_DRAIN_TIMEOUT_SECONDS)
            await engine.dispose()
            # Bounded flush; the providers are shut down (also bounded) at process exit.
            await asyncio.to_thread(observability.force_flush)

    app = FastAPI(
        title="belegbot",
        version=package_version("belegbot"),
        lifespan=lifespan,
        # Every route needs a session unless listed in app/api/public.py.
        dependencies=[Depends(require_session)],
        # Re-added below behind the session; 404 in production.
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    # Last added = outermost: no-store also covers CSRF rejections.
    app.add_middleware(CsrfMiddleware)
    app.add_middleware(NoStoreMiddleware)
    instrument_app(app, observability)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI's default body echoes the input (e-mail addresses, tokens); never do that.
        if request.url.path == "/auth/magic-link":
            return JSONResponse({"detail": auth.INVALID_EMAIL}, status_code=422)
        if request.url.path == "/auth/verify":
            return JSONResponse({"detail": auth.INVALID_OR_EXPIRED}, status_code=400)
        return JSONResponse({"detail": "invalid_request"}, status_code=422)

    @app.get("/health")
    async def health(request: Request) -> JSONResponse:
        engine: AsyncEngine = request.app.state.engine
        try:
            async with asyncio.timeout(HEALTH_DB_TIMEOUT_SECONDS):
                async with engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
        except Exception as exc:  # any DB failure (refused, auth, timeout) means "not healthy"
            # Class name only: messages can carry host, user or URL details.
            logger.warning("health: db check failed (%s)", type(exc).__name__)
            return JSONResponse({"status": "error", "db": "error"}, status_code=503)
        return JSONResponse({"status": "ok", "db": "ok"})

    @app.get("/version")
    async def version(request: Request) -> dict[str, str]:
        resolved: Settings = request.app.state.settings
        return {
            "version": package_version("belegbot"),
            "commit": resolved.git_sha,
            "env": resolved.app_env,
        }

    # Dev-only API docs (relative URLs, so they also work behind the /api proxy).
    @app.get("/openapi.json", include_in_schema=False)
    async def openapi_json() -> JSONResponse:
        return JSONResponse(app.openapi())

    @app.get("/docs", include_in_schema=False)
    async def swagger_docs() -> HTMLResponse:
        return get_swagger_ui_html(openapi_url="openapi.json", title="belegbot api")

    @app.get("/redoc", include_in_schema=False)
    async def redoc_docs() -> HTMLResponse:
        return get_redoc_html(openapi_url="openapi.json", title="belegbot api")

    app.include_router(auth.router)
    app.include_router(documents.router)
    app.include_router(tax_items.router)
    app.include_router(meta.router)
    return app


app = create_app()
