"""LLM tests: no DB, no network, no key from the developer's environment.

- Outgoing sockets are blocked (`socket.socket.connect` raises), except in `live` tests.
- `OPENAI_API_KEY`, `OPENAI_BASE_URL` and `LLM_*` are removed for non-live tests.
- `otel` gives an in-memory span exporter (behind the production scrubber) and metric
  reader; `json_log` captures the exact JSON lines the stdout handler prints.
"""

from __future__ import annotations

import io
import logging
import os
import socket
from collections.abc import Iterator

import pytest

from app.observability import API_SERVICE_NAME, setup_observability
from app.observability.logs import build_formatter

from .helpers import Otel, make_otel

setup_observability(API_SERVICE_NAME)  # idempotent; configures structlog → stdlib JSON


@pytest.fixture(autouse=True)
def _offline(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.node.get_closest_marker("live"):
        return
    for name in list(os.environ):
        if name.startswith("LLM_") or name in ("OPENAI_API_KEY", "OPENAI_BASE_URL"):
            monkeypatch.delenv(name, raising=False)

    def _blocked(self: socket.socket, *args: object, **kwargs: object) -> None:
        raise RuntimeError("network access is blocked in LLM tests")

    monkeypatch.setattr(socket.socket, "connect", _blocked)


@pytest.fixture
def otel() -> Otel:
    return make_otel()


@pytest.fixture
def json_log() -> Iterator[io.StringIO]:
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
