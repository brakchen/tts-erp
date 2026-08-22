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

Contract:
- Auth middleware MUST run first (it populates api_key_hash).
- 429 response includes Retry-After.
"""

from __future__ import annotations

import os
import sys
import time
from collections import deque

DEFAULT_LIMIT = 100
WINDOW_S = 60.0
EVICT_AFTER_S = 5 * WINDOW_S
EVICT_EVERY_N_CALLS = 256


class RateLimitMiddleware:
    """ASGI middleware. Wire with `app.add_middleware(RateLimitMiddleware)`."""

    def __init__(self, app, limit: int | None = None):
        self.app = app
        self.limit = int(
            os.environ.get("TTS_ERP_RATE_LIMIT_PER_MIN") or limit or DEFAULT_LIMIT
        )
        self._buckets: dict[str, deque[float]] = {}
        self._call_count = 0

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Count every request (used for lazy eviction only)
        self._call_count += 1
        if self._call_count % EVICT_EVERY_N_CALLS == 0:
            self._evict_old_keys()

        key_id = scope.get("api_key_hash")
        if key_id is None:
            await self.app(scope, receive, send)
            return

        now = time.monotonic()
        dq = self._buckets.setdefault(key_id, deque())
        cutoff = now - WINDOW_S
        while dq and dq[0] < cutoff:
            dq.popleft()

        if len(dq) >= self.limit:
            retry_after = max(1, int(round(WINDOW_S - (now - dq[0]))))
            sys.stderr.write(
                f"[rate-limit] 429 key_id={key_id[:16]} path={scope.get('path')} "
                f"limit={self.limit}/{int(WINDOW_S)}s retry_after={retry_after}s\n"
            )
            body = (
                f'{{"detail":"rate limit exceeded: {self.limit} req/{int(WINDOW_S)}s per api key",'
                f'"retry_after_s":{retry_after}}}'
            ).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 429,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"retry-after", str(retry_after).encode()),
                        (b"x-ratelimit-limit", str(self.limit).encode()),
                        (b"x-ratelimit-remaining", b"0"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return

        dq.append(now)
        await self.app(scope, receive, send)

    def _evict_old_keys(self) -> None:
        now = time.monotonic()
        stale = [
            k for k, dq in self._buckets.items() if dq and dq[-1] < now - EVICT_AFTER_S
        ]
        for k in stale:
            self._buckets.pop(k, None)


def install(app, limit: int | None = None) -> RateLimitMiddleware:
    return RateLimitMiddleware(app, limit=limit)


def reset_for_key(key_id: str) -> None:
    """Reset bucket for a key (admin helper). Module-level bucket map not used in prod."""


_MODULE_BUCKETS: dict[str, deque[float]] = {}
