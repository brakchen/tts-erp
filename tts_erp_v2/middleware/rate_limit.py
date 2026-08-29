"""Per-API-key sliding-window rate limiting (ported from tdd/rate_limit.py).

Configuration: env ``TTS_ERP_RATE_LIMIT_PER_MIN`` (default 100).

Contract:
- ``AuthMiddleware`` MUST be installed outside this middleware. It writes
  ``scope['api_key_hash']``; this middleware reads it.
- ``scope['api_key_hash'] is None`` ⇒ pass through (anonymous/exempt).
- ``429`` response carries ``Retry-After`` + ``X-RateLimit-*`` headers.
- The shared bucket counter is reused by the auth denial path so that
  brute-force key attempts are throttled.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections import deque

DEFAULT_LIMIT = 100
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
        stale = [k for k, dq in self._buckets.items() if not dq or dq[-1] < now - EVICT_AFTER_S]
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


def reset_shared(limit: int | None = None) -> None:
    """Test helper: drop the shared singleton so the next ``shared_counter()``
    call rebuilds it with the current env var (or the supplied ``limit``).

    Use ``reset_shared()`` between tests to honor monkeypatched env vars.
    Use ``reset_shared(new_limit)`` to also force a new limit value.
    """
    global _SHARED
    _SHARED = None
    if limit is not None:
        shared_counter(limit)


def too_many_response(limit: int, retry_after: int) -> tuple[int, list[tuple[bytes, bytes]], bytes]:
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
