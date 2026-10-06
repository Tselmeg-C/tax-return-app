"""CSRF checks and `Cache-Control: no-store` for `/auth/*` (pure ASGI middleware).

CSRF (with `SameSite=Lax` cookies): every POST/PUT/PATCH/DELETE must carry
`X-Requested-With: belegbot` (a cross-site form cannot set it, and without CORS a cross-site
`fetch` cannot either), and an `Origin` header, if present, must equal `APP_BASE_URL`'s origin.
Otherwise `403 {"detail":"csrf"}`.
"""

from __future__ import annotations

import json

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.public import CSRF_EXEMPT_PATHS
from app.config import Settings

CSRF_HEADER = "x-requested-with"
CSRF_HEADER_VALUE = "belegbot"
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
NO_STORE_PREFIX = "/auth/"


def csrf_ok(method: str, path: str, headers: Headers, app_origin: str) -> bool:
    if method.upper() not in UNSAFE_METHODS or path in CSRF_EXEMPT_PATHS:
        return True
    if headers.get(CSRF_HEADER) != CSRF_HEADER_VALUE:
        return False
    origin = headers.get("origin")
    return origin is None or origin.lower() == app_origin


class CsrfMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        settings: Settings = scope["app"].state.settings
        if csrf_ok(scope["method"], scope["path"], Headers(scope=scope), settings.app_origin):
            await self.app(scope, receive, send)
            return
        body = json.dumps({"detail": "csrf"}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


class NoStoreMiddleware:
    """`Cache-Control: no-store` on every `/auth/*` response, errors included."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(NO_STORE_PREFIX):
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["cache-control"] = "no-store"
            await send(message)

        await self.app(scope, receive, send_wrapper)
