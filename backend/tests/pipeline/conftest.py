"""Pipeline tests (#9): no network, no key from the developer's environment.

Outgoing sockets raise (the DB is reached through already-open pool connections or
`localhost`-free unix paths only when the test uses the `committed` fixture, see below);
`OPENAI_API_KEY`, `OPENAI_BASE_URL`, `LLM_*` and `PIPELINE_*` are removed.
"""

from __future__ import annotations

import os
import socket
from collections.abc import Iterator
from typing import Any

import pytest

from app.config import get_llm_settings
from app.llm import get_router


class NetworkBlocked(RuntimeError):
    pass


_real_connect = socket.socket.connect


def _db_ports() -> set[int]:
    from sqlalchemy.engine import make_url

    ports = set()
    for name in ("TEST_DATABASE_URL", "DATABASE_URL"):
        raw = os.environ.get(name)
        if raw:
            ports.add(make_url(raw).port or 5432)
    return ports


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in list(os.environ):
        if name.startswith(("LLM_", "PIPELINE_")) or name in ("OPENAI_API_KEY", "OPENAI_BASE_URL"):
            monkeypatch.delenv(name, raising=False)
    allowed = _db_ports()

    def guard(self: socket.socket, address: Any, *args: Any, **kwargs: Any) -> Any:
        # Postgres only (the test DB); every other destination is blocked.
        if isinstance(address, tuple) and len(address) >= 2 and address[1] in allowed:
            return _real_connect(self, address, *args, **kwargs)
        if isinstance(address, str):  # unix socket (local Postgres)
            return _real_connect(self, address, *args, **kwargs)
        raise NetworkBlocked("network access is blocked in pipeline tests")

    monkeypatch.setattr(socket.socket, "connect", guard)
    get_llm_settings.cache_clear()
    get_router.cache_clear()
    yield
    get_llm_settings.cache_clear()
    get_router.cache_clear()
