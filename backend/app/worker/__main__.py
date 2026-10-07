"""Worker process (`python -m app.worker`), run next to the api by honcho.

Startup: observability (`belegbot-worker`), settings check, storage probe, sweep; then the
queue loop until SIGTERM/SIGINT (graceful shutdown, exit 0). Never runs migrations.

Test-only switch: `WORKER_TEST_SLEEP_SECONDS=<n>` makes `process_document` sleep n seconds
before the pipeline runs (ignored with APP_ENV=production).
"""

from __future__ import annotations

import asyncio
import os
import signal

import structlog

from app.config import SettingsError, check_worker_settings, load_llm_settings, load_settings
from app.db.session import create_engine, create_sessionmaker
from app.domain.enums import JobKind
from app.observability import WORKER_SERVICE_NAME, setup_observability
from app.observability.logs import set_level
from app.queue.handlers import Handler, JobContext, default_handlers
from app.storage import StorageUnavailable, storage_from_settings
from app.worker.loop import Worker

log = structlog.stdlib.get_logger("app.worker")


def _test_handlers(app_env: str) -> dict[JobKind, Handler]:
    handlers = default_handlers()
    handlers_default = handlers[JobKind.PROCESS_DOCUMENT]
    raw = os.environ.get("WORKER_TEST_SLEEP_SECONDS", "").strip()
    if raw and app_env != "production":
        seconds = float(raw)

        async def sleepy(ctx: JobContext) -> None:
            await asyncio.sleep(seconds)
            await handlers_default(ctx)

        handlers[JobKind.PROCESS_DOCUMENT] = sleepy
    return handlers


async def amain() -> int:
    try:
        settings = load_settings()
        check_worker_settings(settings)
        from app.pipeline.handler import check_pipeline_settings

        check_pipeline_settings(settings, load_llm_settings())
    except SettingsError as exc:
        log.error("worker startup failed", reason=str(exc))  # names variables, never values
        return 1
    set_level(settings.log_level)
    storage = storage_from_settings(settings)
    try:
        await asyncio.to_thread(storage.probe)
    except StorageUnavailable as exc:
        log.error("worker startup failed", reason=str(exc))
        return 1

    engine = create_engine(settings)
    worker = Worker(
        sessionmaker=create_sessionmaker(engine),
        storage=storage,
        settings=settings,
        handlers=_test_handlers(settings.app_env),
    )
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, worker.stop)
    try:
        try:
            await worker.sweep()
        except Exception as exc:
            log.warning("worker.loop_error", error_kind=type(exc).__name__)
        await worker.run()
    finally:
        await engine.dispose()
    return 0


def main() -> int:
    observability = setup_observability(WORKER_SERVICE_NAME)
    try:
        return asyncio.run(amain())
    finally:
        observability.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
