"""The only routes reachable without a session. Every other route requires one.

Adding a path here makes it public: think twice, and keep the meta-test in
`tests/auth/test_protection.py` green. CSRF-exempt paths (none yet; #11's Telegram webhook
will be one) live next to it.
"""

from __future__ import annotations

PUBLIC_PATHS: frozenset[str] = frozenset(
    {
        "/health",
        "/version",
        "/auth/magic-link",
        "/auth/verify",
        "/auth/logout",
    }
)

# Not public: in development they need a session like any route; in production they are 404.
DEV_ONLY_PATHS: frozenset[str] = frozenset({"/docs", "/redoc", "/openapi.json"})

# State-changing requests to these paths skip the CSRF checks (they authenticate otherwise).
CSRF_EXEMPT_PATHS: frozenset[str] = frozenset()
