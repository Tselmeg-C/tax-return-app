"""Worker process (`python -m app.worker`), run next to the api by honcho.

Placeholder until the queue exists (#6): sets up observability, logs one line, then idles
until SIGTERM/SIGINT and exits 0. No DB access, never runs migrations.
"""

from __future__ import annotations

import signal
import threading
from types import FrameType

import structlog

from app.observability import WORKER_SERVICE_NAME, setup_observability


def main() -> int:
    observability = setup_observability(WORKER_SERVICE_NAME)
    log = structlog.stdlib.get_logger("app.worker")
    stop = threading.Event()

    def _handle(signum: int, _frame: FrameType | None) -> None:
        log.info("worker stopping", signal=signal.Signals(signum).name)
        stop.set()

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)

    log.info("worker started, no queue yet (#6)")
    while not stop.wait(timeout=1.0):
        pass
    observability.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
