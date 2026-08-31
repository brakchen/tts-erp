"""Per-API-key sliding-window rate limiting (ported from tdd/rate_limit.py).

Configuration: env ``TTS_ERP_RATE_LIMIT_PER_MIN`` (default 100).

Contract:
- ``AuthMiddleware`` MUST be installed outside this middleware. It writes
  ``scope['api_key_hash']``; this middleware reads it.
- ``scope['api_key_hash'] is None`` ⇒ pass through (anonymous/exempt).
- ``429`` response carries ``Retry-After`` + ``X-RateLimit-*`` headers.
- The shared bucket counter is reused by the auth denial path so that
  brute-force key attempts are throttled.

Hot reload (2026-08-31):
  The middleware reads ``TTS_ERP_RATE_LIMIT_PER_MIN`` only on first
  request, so an env-var edit alone won't take effect. Operators can
  hot-reload via ``POST /v2/admin/reset-rate-limit`` (admin-only) which
  calls ``reset_shared()`` internally.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections import deque
from typing import Any

DEFAULT_LIMIT = 100
ENV_VAR_NAME = "TTS_ERP_RATE_LIMIT_PER_MIN"  # exported for the admin reset endpoint + diagnostics
WINDOW_S = 60
EVICT_AFTER_S = 5 * WINDOW_S
EVICT_EVERY_N_CALLS = 256


class SlidingWindow:
    """Sliding-window request counter.

    ``hit(key_id)`` returns ``None`` when the request is within budget,
    else the ``Retry-After`` seconds to wait.
    """

    def __init__(self, limit: int, window_s: float = WINDOW_S):
        self.limit = limit
        self.window_s = window_s
        self._buckets: dict[str, deque[float]] = {}
        self._call_count = 0

    def hit(self, key_id: str) -> int | None:
        self._call_count += 1
        if self._call_count % EVICT_EVERY_N_CALLS == 0:
            self._evict_old_keys()
        now = time.monotonic()
        dq = self._buckets.setdefault(key_id, deque())
        cutoff = now - self.window_s
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) >= self.limit:
            return max(1, round(self.window_s - (now - dq[0])))
        dq.append(now)
        return None

    def _evict_old_keys(self) -> None:
        now = time.monotonic()
        stale = [
            k
            for k, dq in self._buckets.items()
            if not dq or dq[-1] < now - EVICT_AFTER_S
        ]
        for k in stale:
            self._buckets.pop(k, None)


def _env_limit() -> int:
    raw = os.environ.get("TTS_ERP_RATE_LIMIT_PER_MIN")
    if not raw:
        return DEFAULT_LIMIT
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_LIMIT


_SHARED: SlidingWindow | None = None


def shared_counter(limit: int | None = None) -> SlidingWindow:
    """Process-wide counter shared by middleware + auth-denial path.

    ``limit`` only applies on first creation; subsequent calls reuse it.
    """
    global _SHARED
    if _SHARED is None:
        _SHARED = SlidingWindow(limit or _env_limit())
    return _SHARED


def shared_hit(key_id: str) -> int | None:
    """Count one request against the shared per-key budget."""
    return shared_counter().hit(key_id)


def shared_config() -> dict[str, Any] | None:
    """Return the current shared counter's config, or ``None`` if the
    middleware hasn't yet created its singleton (e.g., no authenticated
    request has been served since worker boot).

    Used by the admin ``GET /v2/admin/rate-limit`` endpoint and by tests
    that need to assert post-reset state.
    """
    if _SHARED is None:
        return None
    return {
        "limit": _SHARED.limit,
        "window_s": _SHARED.window_s,
        "active_buckets": len(_SHARED._buckets),
    }


def reset_shared(
    limit: int | None = None,
    *,
    reset_buckets: bool = True,
) -> dict[str, Any]:
    """Apply a runtime config change to the in-process rate-limit
    singleton. Returns a dict describing the change so callers (admin
    endpoint, ops scripts, tests) can audit-log what happened.

    Behavior matrix:

    +---------------------+----------------+-----------------------------+
    | limit               | reset_buckets  | effect                      |
    +=====================+================+=============================+
    | None                | True (default) | Re-read env; clear buckets  |
    | int                 | True           | Set cap; clear buckets      |
    | None                | False          | Re-read env; KEEP buckets   |
    | int                 | False          | Set cap; KEEP buckets       |
    +---------------------+----------------+-----------------------------+

    When the singleton doesn't exist yet (no authenticated request has
    been served since worker boot), we lazily build a fresh one — no
    buckets to preserve, so ``reset_buckets`` is effectively a no-op
    in that case.

    The ``limit`` attribute is mutated **in place** when the singleton
    already exists, so per-key deques survive ``reset_buckets=False``.
    Doing a ``_SHARED = None`` + ``SlidingWindow(...)`` rebuild would
    discard every key's count and break the "raise the cap without
    invalidating in-flight state" use case.

    Returns a dict with:
      - ``old_limit``          int | None  (None if singleton didn't exist)
      - ``new_limit``          int         (post-reset value)
      - ``window_s``           float       (always WINDOW_S today; reserved
                                            for future per-tenant overrides)
      - ``buckets_cleared``    int         (count of per-key deques dropped
                                            when ``reset_buckets=True``)
      - ``active_buckets``     int         (per-key deques in the singleton
                                            after the call)
      - ``reset_buckets``      bool        (echoed from the input)
      - ``limit_source``       str         ``"override"`` if ``limit`` was
                                            given, else ``"env"``
    """
    global _SHARED
    old_config = shared_config()
    effective_limit = limit if limit is not None else _env_limit()
    limit_source = "override" if limit is not None else "env"
    buckets_cleared = 0
    if _SHARED is None:
        # Cold start: build the singleton with the target limit. Nothing
        # to preserve regardless of ``reset_buckets``.
        _SHARED = SlidingWindow(effective_limit)
    else:
        # Hot path: mutate in place so per-key deques survive.
        _SHARED.limit = effective_limit
        if reset_buckets:
            buckets_cleared = len(_SHARED._buckets)
            _SHARED._buckets.clear()
    return {
        "old_limit": old_config["limit"] if old_config else None,
        "new_limit": _SHARED.limit,
        "window_s": _SHARED.window_s,
        "buckets_cleared": buckets_cleared,
        "active_buckets": len(_SHARED._buckets),
        "reset_buckets": reset_buckets,
        "limit_source": limit_source,
    }


def too_many_response(
    limit: int, retry_after: int
) -> tuple[int, list[tuple[bytes, bytes]], bytes]:
    body = json.dumps(
        {
            "detail": f"rate limit exceeded: {limit} req/{WINDOW_S}s per api key",
            "retry_after_s": retry_after,
        }
    ).encode()
    headers: list[tuple[bytes, bytes]] = [
        (b"content-type", b"application/json"),
        (b"retry-after", str(retry_after).encode()),
        (b"x-ratelimit-limit", str(limit).encode()),
        (b"x-ratelimit-remaining", b"0"),
    ]
    return 429, headers, body


class RateLimitMiddleware:
    """ASGI middleware. Wire with ``app.add_middleware(RateLimitMiddleware)``.

    Order rule (FastAPI ``add_middleware`` wraps in reverse, therefore
    the LAST ``add_middleware`` call is the OUTERMOST layer):
    ``CORS → Auth → RateLimit`` ⇒ register as
    ``add_middleware(RateLimitMiddleware)`` first, then
    ``add_middleware(AuthMiddleware)``.

    The counter is looked up **per-request** via ``shared_counter()``
    rather than cached at construction. This honors ``TTS_ERP_RATE_LIMIT_PER_MIN``
    changes and lets tests reset the singleton between runs without
    rebuilding the middleware.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        key_id = scope.get("api_key_hash")
        if key_id is None:
            await self.app(scope, receive, send)
            return

        counter = shared_counter()
        retry_after = counter.hit(key_id)
        if retry_after is None:
            await self.app(scope, receive, send)
            return

        sys.stderr.write(
            f"[rate-limit] 429 key_id={key_id[:16]} path={scope.get('path')} "
            f"limit={counter.limit}/{WINDOW_S}s retry_after={retry_after}s\n"
        )
        s, hdrs, body = too_many_response(counter.limit, retry_after)
        await send({"type": "http.response.start", "status": s, "headers": hdrs})
        await send({"type": "http.response.body", "body": body})


def install(app) -> RateLimitMiddleware:
    return RateLimitMiddleware(app)
