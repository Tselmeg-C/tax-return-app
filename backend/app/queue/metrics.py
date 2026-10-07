"""Queue, upload and storage metrics (#6). Attributes are kinds, outcomes and types only."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from opentelemetry import metrics
from opentelemetry.metrics import CallbackOptions, MeterProvider, Observation

from app.domain.enums import JobStatus

METER_NAME = "belegbot"


def _meter(provider: MeterProvider | None) -> metrics.Meter:
    return (provider or metrics.get_meter_provider()).get_meter(METER_NAME)


class UploadMetrics:
    """`belegbot.uploads{outcome, mime_type}` (api)."""

    def __init__(self, provider: MeterProvider | None = None) -> None:
        self.uploads = _meter(provider).create_counter(
            "belegbot.uploads", unit="{upload}", description="Upload requests by outcome"
        )

    def record(self, outcome: str, mime_type: str | None = None) -> None:
        self.uploads.add(1, {"outcome": outcome, "mime_type": mime_type or "unknown"})


class QueueMetrics:
    """Job counters/histogram plus gauges observed by the worker.

    The gauges report values cached by `set_queue_stats` (refreshed by the worker loop), so
    the exporter thread never touches the DB.
    """

    def __init__(
        self,
        provider: MeterProvider | None = None,
        free_bytes: Callable[[], int] | None = None,
    ) -> None:
        meter = _meter(provider)
        self.jobs = meter.create_counter(
            "belegbot.jobs", unit="{job}", description="Finished job attempts by outcome"
        )
        self.duration = meter.create_histogram(
            "belegbot.job.duration", unit="s", description="Job handler duration"
        )
        self._depth: dict[str, int] = {s.value: 0 for s in JobStatus}
        self._oldest_age = 0.0
        self._free_bytes = free_bytes  # from the Storage (no file-system access here)
        meter.create_observable_gauge(
            "belegbot.queue.depth",
            callbacks=[self._observe_depth],
            unit="{job}",
            description="Jobs per status",
        )
        meter.create_observable_gauge(
            "belegbot.queue.oldest_age",
            callbacks=[self._observe_oldest],
            unit="s",
            description="Age of the oldest claimable queued job (0 if none)",
        )
        meter.create_observable_gauge(
            "belegbot.storage.free_bytes",
            callbacks=[self._observe_free],
            unit="By",
            description="Free bytes on the STORAGE_PATH file system",
        )

    def record_job(self, kind: str, outcome: str, duration_s: float | None = None) -> None:
        attrs = {"kind": kind, "outcome": outcome}
        self.jobs.add(1, attrs)
        if duration_s is not None:
            self.duration.record(duration_s, attrs)

    def set_queue_stats(self, depth: dict[str, int], oldest_age_s: float) -> None:
        self._depth = {s.value: depth.get(s.value, 0) for s in JobStatus}
        self._oldest_age = max(0.0, oldest_age_s)

    def _observe_depth(self, _options: CallbackOptions) -> Iterable[Observation]:
        return [Observation(count, {"status": status}) for status, count in self._depth.items()]

    def _observe_oldest(self, _options: CallbackOptions) -> Iterable[Observation]:
        return [Observation(self._oldest_age)]

    def _observe_free(self, _options: CallbackOptions) -> Iterable[Observation]:
        if self._free_bytes is None:
            return []
        try:
            return [Observation(self._free_bytes())]
        except OSError:
            return []
