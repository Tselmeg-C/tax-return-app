"""FastAPI application (`uvicorn app.api.main:app`)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import version as package_version

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import Settings, get_settings
from app.db.session import create_engine, create_sessionmaker
from app.observability import API_SERVICE_NAME, setup_observability
from app.observability.http import instrument_app, instrument_engine
from app.observability.logs import set_level

logger = logging.getLogger("app.api")

HEALTH_DB_TIMEOUT_SECONDS = 2.0


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the app. Settings are resolved at startup (not import); no DB connection is opened.

    Observability (JSON logging, OTel) is set up here, at import time of `app.api.main`, so
    uvicorn's own startup lines are already JSON. It never connects anywhere by itself.
    """
    observability = setup_observability(API_SERVICE_NAME)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        resolved = settings if settings is not None else get_settings()
        set_level(resolved.log_level)
        engine = create_engine(resolved)
        instrument_engine(engine, observability)
        app.state.settings = resolved
        app.state.engine = engine
        app.state.sessionmaker = create_sessionmaker(engine)
        try:
            yield
        finally:
            await engine.dispose()
            # Bounded flush; the providers are shut down (also bounded) at process exit.
            await asyncio.to_thread(observability.force_flush)

    app = FastAPI(title="belegbot", version=package_version("belegbot"), lifespan=lifespan)
    instrument_app(app, observability)

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

    return app


app = create_app()
