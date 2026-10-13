"""The only routes reachable without a session. Every other route requires one.

Adding a path here makes it public: think twice, and keep the meta-test in
`tests/auth/test_protection.py` green. CSRF-exempt paths live next to it.
Both hold route templates (`{param}` matches one path segment).
"""

from __future__ import annotations

import re

PUBLIC_PATHS: frozenset[str] = frozenset(
    {
        "/health",
        "/version",
        "/auth/magic-link",
        "/auth/verify",
        "/auth/logout",
        "/bot/telegram/{secret}",  # #11: the secret path + header are its authentication
        "/auth/passkey/login/options",
        "/auth/passkey/login/verify",
    }
)

# Not public: in development they need a session like any route; in production they are 404.
DEV_ONLY_PATHS: frozenset[str] = frozenset({"/docs", "/redoc", "/openapi.json"})

# State-changing requests to these paths skip the CSRF checks (they authenticate otherwise).
CSRF_EXEMPT_PATHS: frozenset[str] = frozenset({"/bot/telegram/{secret}"})

_EXEMPT_PATTERNS = tuple(
    re.compile("/".join("[^/]+" if seg.startswith("{") else re.escape(seg) for seg in p.split("/")))
    for p in CSRF_EXEMPT_PATHS
)


def is_csrf_exempt(path: str) -> bool:
    """Whether a concrete request path matches a template in `CSRF_EXEMPT_PATHS`."""
    return any(p.fullmatch(path) for p in _EXEMPT_PATTERNS)
