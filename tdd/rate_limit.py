"""Per-API-key rate limiting middleware (in-process sliding window).

Configurable via env TTS_ERP_RATE_LIMIT_PER_MIN (default 100).

Implementation notes:
- Custom ASGI middleware (NOT BaseHTTPMiddleware) — BaseHTTPMiddleware
  in starlette <0.31 wraps requests in a new scope which isolates
  request.state across middleware layers. Sharing state via scope directly
  is the standard workaround.
- Auth middleware writes `scope["api_key_hash"]` (sha256 prefix). Rate
  limit reads the same key. If absent (anonymous / exempt paths), we
  skip per-key bucketing.
- Sliding window: deque of timestamps per key; entries older than
  WINDOW_S are popleft'd on each call. Lazy cleanup of stale buckets
  every 256 calls.
- The bucket store is a module-level shared counter (`shared_counter()`)
  so the auth middleware can count DENIED requests (401/403 short-circuits
  never reach this middleware) against the same per-key budget — otherwise
  brute-forcing random keys is unthrottled and each attempt costs a PG
  connection.

Contract:
- Auth middleware MUST run first (it populates api_key_hash).
- 429 response includes Retry-After.
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
    """Sliding-window request counter. `hit()` returns None when the
    request is within budget, else the Retry-After seconds."""

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
    """Process-wide counter shared by the middleware and the auth
    denial path. `limit` only applies on first creation."""
    global _SHARED
    if _SHARED is None:
        _SHARED = SlidingWindow(limit or _env_limit())
    return _SHARED


def shared_hit(key_id: str) -> int | None:
    """Count one request against the shared per-key budget."""
    return shared_counter().hit(key_id)


def reset_shared() -> None:
    """Test helper: drop all shared buckets."""
    if _SHARED is not None:
        _SHARED._buckets.clear()


def too_many_response(limit: int, retry_after: int) -> tuple[int, list, bytes]:
    body = json.dumps(
        {
            "detail": f"rate limit exceeded: {limit} req/{WINDOW_S}s per api key",
            "retry_after_s": retry_after,
        }
    ).encode()
    headers = [
        (b"content-type", b"application/json"),
        (b"retry-after", str(retry_after).encode()),
        (b"x-ratelimit-limit", str(limit).encode()),
        (b"x-ratelimit-remaining", b"0"),
    ]
    return 429, headers, body


class RateLimitMiddleware:
    """ASGI middleware. Wire with `app.add_middleware(RateLimitMiddleware)`."""

    def __init__(self, app, limit: int | None = None):
        self.app = app
        self._counter = shared_counter(limit)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        key_id = scope.get("api_key_hash")
        if key_id is None:
            await self.app(scope, receive, send)
            return

        retry_after = self._counter.hit(key_id)
        if retry_after is None:
            await self.app(scope, receive, send)
            return

        sys.stderr.write(
            f"[rate-limit] 429 key_id={key_id[:16]} path={scope.get('path')} "
            f"limit={self._counter.limit}/{WINDOW_S}s retry_after={retry_after}s\n"
        )
        s, hdrs, body = too_many_response(self._counter.limit, retry_after)
        await send({"type": "http.response.start", "status": s, "headers": hdrs})
        await send({"type": "http.response.body", "body": body})


def install(app, limit: int | None = None) -> RateLimitMiddleware:
    return RateLimitMiddleware(app, limit=limit)
