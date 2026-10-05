"""The placeholder worker logs one JSON line, idles, and exits 0 on SIGTERM."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent


def test_worker_starts_logs_and_stops_on_sigterm() -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("OTEL_")}
    env.pop("DATABASE_URL", None)  # the placeholder never touches the DB
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.worker"],
        cwd=BACKEND_DIR,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert proc.stdout is not None
    lines: list[str] = []
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if not line:
            break
        lines.append(line)
        if "no queue yet" in line:
            break
    proc.send_signal(signal.SIGTERM)
    rest, _ = proc.communicate(timeout=10)
    lines.extend(rest.splitlines())
    assert proc.returncode == 0

    records = [json.loads(line) for line in lines if line.strip()]
    for record in records:
        assert {"timestamp", "level", "event", "service"} <= set(record)
        assert record["service"] == "belegbot-worker"
    started = [r for r in records if "no queue yet" in str(r["event"])]
    assert len(started) == 1
    assert started[0]["event"] == "worker started, no queue yet (#6)"
