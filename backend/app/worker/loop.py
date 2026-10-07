"""The worker loop: claim, run with heartbeat and timeout, finish; never exits on errors.

Logs carry ids, kinds and class names only (never file names, bytes or `str(exc)`).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import secrets
import socket
import time
from collections.abc import Mapping

import structlog
from opentelemetry import trace
from opentelemetry.trace import Link, SpanKind, Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.domain.enums import JobKind
from app.queue import runner
from app.queue.errors import JobTimeout, PermanentJobError, error_kind_of
from app.queue.handlers import Handler, JobContext, default_handlers
from app.queue.metrics import QueueMetrics
from app.queue.runner import ClaimedJob
from app.storage.local import LocalVolume
from app.storage.sweep import sweep

log = structlog.stdlib.get_logger("app.worker")
SWEEP_INTERVAL_SECONDS = 6 * 3600
STATS_INTERVAL_SECONDS = 15.0


def new_worker_id() -> str:
    return f"{socket.gethostname()}-{os.getpid()}-{secrets.token_hex(2)}"[-100:]


def _link(traceparent: str | None) -> list[Link]:
    if not traceparent:
        return []
    ctx = trace.get_current_span(
        TraceContextTextMapPropagator().extract({"traceparent": traceparent})
    ).get_span_context()
    return [Link(ctx)] if ctx.is_valid else []


class Worker:
    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        storage: LocalVolume,
        settings: Settings,
        handlers: Mapping[JobKind, Handler] | None = None,
        metrics: QueueMetrics | None = None,
        worker_id: str | None = None,
        error_backoff: tuple[float, float] = (1.0, 30.0),
    ) -> None:
        self.sessionmaker = sessionmaker
        self.storage = storage
        self.settings = settings
        self.handlers = dict(handlers or default_handlers())
        self.metrics = metrics or QueueMetrics(free_bytes=storage.free_bytes)
        self.worker_id = worker_id or new_worker_id()
        self.error_backoff = error_backoff
        self.stopping = asyncio.Event()
        self.tasks: set[asyncio.Task[None]] = set()
        self._tracer = trace.get_tracer("app.worker")
        self._stats_at = 0.0

    def stop(self) -> None:
        self.stopping.set()

    # --- one job ---------------------------------------------------------------------

    async def _heartbeat(self, job: ClaimedJob) -> None:
        lease = self.settings.job_lease_seconds
        while True:
            await asyncio.sleep(lease / 3)
            try:
                if not await runner.heartbeat(self.sessionmaker, job, lease_seconds=lease):
                    return  # lease lost; the fenced completion discards the result
            except Exception as exc:
                log.warning("worker.loop_error", error_kind=type(exc).__name__, job_id=str(job.id))

    async def _run_handler(self, job: ClaimedJob) -> None:
        handler = self.handlers[job.kind]
        ctx = JobContext(job=job, sessionmaker=self.sessionmaker, storage=self.storage)
        cm = asyncio.timeout(self.settings.job_timeout_seconds)
        try:
            async with cm:
                await handler(ctx)
        except TimeoutError:
            if cm.expired():
                raise JobTimeout() from None
            raise

    async def run_job(self, job: ClaimedJob) -> None:
        log.info(
            "job.claimed",
            job_id=str(job.id),
            kind=job.kind.value,
            attempt=job.attempt,
            reclaimed=job.reclaimed,
        )
        if job.reclaimed:
            log.info("job.lease_expired", job_id=str(job.id), kind=job.kind.value, final=False)
        attributes: dict[str, str | int] = {
            "job.id": str(job.id),
            "job.kind": job.kind.value,
            "job.attempt": job.attempt,
        }
        if job.document_id is not None:
            attributes["document.id"] = str(job.document_id)
        with self._tracer.start_as_current_span(
            f"job {job.kind.value}",
            kind=SpanKind.CONSUMER,
            links=_link(job.trace_context),
            attributes=attributes,
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            started = time.monotonic()
            beat = asyncio.create_task(self._heartbeat(job))
            outcome: str
            ok = True
            try:
                try:
                    await self._run_handler(job)
                finally:
                    beat.cancel()
            except asyncio.CancelledError:
                # Shutdown: give the job back without using up an attempt.
                ok = await runner.release(self.sessionmaker, job)
                if ok:
                    log.info("job.released", job_id=str(job.id), kind=job.kind.value)
                    self.metrics.record_job(job.kind.value, "released")
                raise
            except Exception as exc:
                kind = error_kind_of(exc)
                span.set_attribute("error.type", kind)
                span.set_status(Status(StatusCode.ERROR))
                duration = time.monotonic() - started
                if isinstance(exc, PermanentJobError) or job.attempt >= job.max_attempts:
                    outcome = "failed"
                    ok = await runner.fail(self.sessionmaker, job, error_kind=kind)
                    if ok:
                        log.warning("job.failed", job_id=str(job.id), error_kind=kind)
                else:
                    outcome = "retried"
                    delay = runner.backoff_seconds(
                        job.attempt,
                        base=self.settings.job_backoff_base_seconds,
                        maximum=self.settings.job_backoff_max_seconds,
                    )
                    ok = await runner.retry(self.sessionmaker, job, error_kind=kind, delay_s=delay)
                    if ok:
                        log.info(
                            "job.retry",
                            job_id=str(job.id),
                            error_kind=kind,
                            next_run_in_s=round(delay, 1),
                        )
            else:
                outcome = "succeeded"
                duration = time.monotonic() - started
                ok = await runner.succeed(self.sessionmaker, job)
                if ok:
                    log.info(
                        "job.succeeded", job_id=str(job.id), duration_ms=round(duration * 1000)
                    )
            if not ok:
                outcome = "lease_lost"
                log.warning("job.lease_lost", job_id=str(job.id), kind=job.kind.value)
            span.set_attribute("job.outcome", outcome)
            self.metrics.record_job(job.kind.value, outcome, duration)

    async def _guarded(self, job: ClaimedJob) -> None:
        try:
            await self.run_job(job)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # e.g. DB down while finishing: the lease will expire
            log.warning("worker.loop_error", error_kind=type(exc).__name__, job_id=str(job.id))

    # --- loop ------------------------------------------------------------------------

    async def expire_leases(self) -> None:
        for expired in await runner.expire_poisoned(self.sessionmaker):
            log.warning(
                "job.lease_expired", job_id=str(expired.id), kind=expired.kind.value, final=True
            )
            self.metrics.record_job(expired.kind.value, "failed")

    async def claim(self) -> ClaimedJob | None:
        return await runner.claim(
            self.sessionmaker,
            worker_id=self.worker_id,
            lease_seconds=self.settings.job_lease_seconds,
        )

    async def run_once(self) -> bool:
        """Expire poisoned leases, claim one job and run it to the end. `True` if one ran."""
        await self.expire_leases()
        job = await self.claim()
        if job is None:
            return False
        await self._guarded(job)
        await self.refresh_stats(force=True)
        return True

    async def refresh_stats(self, *, force: bool = False) -> None:
        if not force and time.monotonic() - self._stats_at < STATS_INTERVAL_SECONDS:
            return
        async with self.sessionmaker() as session:
            depth, oldest = await runner.queue_stats(session)
        self.metrics.set_queue_stats(depth, oldest)
        self._stats_at = time.monotonic()

    async def sweep(self) -> None:
        await sweep(self.storage, self.sessionmaker)

    async def _fill_slots(self) -> None:
        await self.expire_leases()
        while len(self.tasks) < self.settings.worker_concurrency and not self.stopping.is_set():
            job = await self.claim()
            if job is None:
                return
            task = asyncio.create_task(self._guarded(job))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

    async def _idle(self, seconds: float) -> None:
        """Wait `seconds`, or less when stopping or when a running job finishes."""
        stop = asyncio.create_task(self.stopping.wait())
        try:
            await asyncio.wait({stop, *self.tasks}, timeout=seconds, return_when="FIRST_COMPLETED")
        finally:
            stop.cancel()

    async def run(self) -> None:
        log.info("worker started", worker_id=self.worker_id)
        backoff = self.error_backoff[0]
        next_sweep = time.monotonic() + SWEEP_INTERVAL_SECONDS
        while not self.stopping.is_set():
            try:
                await self._fill_slots()
                await self.refresh_stats(force=not self.tasks)
                if time.monotonic() >= next_sweep:
                    next_sweep = time.monotonic() + SWEEP_INTERVAL_SECONDS
                    await self.sweep()
                backoff = self.error_backoff[0]
            except Exception as exc:
                log.warning("worker.loop_error", error_kind=type(exc).__name__)
                await self._idle(backoff)
                backoff = min(backoff * 2, self.error_backoff[1])
                continue
            await self._idle(self.settings.worker_poll_interval_seconds)
        await self._shutdown()
        log.info("worker stopped", worker_id=self.worker_id)

    async def _shutdown(self) -> None:
        if not self.tasks:
            return
        pending = set(self.tasks)
        _, still = await asyncio.wait(pending, timeout=self.settings.worker_shutdown_grace_seconds)
        for task in still:
            task.cancel()
        for task in still:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
