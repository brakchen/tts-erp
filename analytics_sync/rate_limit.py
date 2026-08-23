"""Per-token rate limiter for analytics_sync.

Sliding-window counter, in-process. Bucketed by token prefix so a single
noisy token cannot starve other tokens. Resets are cooperative; the
window is bounded by ANALYTICS_SYNC_RATE_LIMIT_PER_MIN (default 100).

Returned response includes Retry-After in seconds (integer ≥ 1).

For multi-instance deployments, swap this for a Redis-backed counter;
the call signature (allow(key) → RetryAfter | None) is unchanged.
"""
from __future__ import annotations

import os
import sys
import time
from collections import deque

DEFAULT_PER_MIN = 100
WINDOW_SECONDS = 60.0


def _per_min() -> int:
    try:
        return max(1, int(os.environ.get("ANALYTICS_SYNC_RATE_LIMIT_PER_MIN", DEFAULT_PER_MIN)))
    except ValueError:
        return DEFAULT_PER_MIN


# Buckets keyed by token prefix. In production this is one process;
# a multi-worker uvicorn deployment would need a shared store.
_buckets: dict[str, deque[float]] = {}


def reset_buckets() -> None:
    """Test helper."""
    _buckets.clear()


def allow(key: str) -> tuple[bool, int]:
    """Return (is_allowed, retry_after_seconds).

    `retry_after_seconds` is meaningful only when is_allowed is False.
    """
    if key == "-" or not key:
        # Anonymous traffic: still rate-limit, but bucket by remote IP
        # rather than token prefix.
        pass
    now = time.monotonic()
    cutoff = now - WINDOW_SECONDS
    bucket = _buckets.setdefault(key, deque())
    # Drop expired entries.
    while bucket and bucket[0] < cutoff:
        bucket.popleft()
    limit = _per_min()
    if len(bucket) >= limit:
        # Retry-After = ceil(seconds until oldest entry falls out of window).
        retry_after = max(1, int(WINDOW_SECONDS - (now - bucket[0])) + 1)
        return False, retry_after
    bucket.append(now)
    return True, 0


def _deny(retry_after: int):
    """Build an ASGI 429 response with Retry-After header."""
    import json as _json

    body = _json.dumps(
        {
            "code": "RATE_LIMITED",
            "message": f"rate limit exceeded; retry after {retry_after}s",
            "requestId": None,
            "retryable": True,
        }
    ).encode()
    headers = [
        (b"content-type", b"application/json"),
        (b"retry-after", str(retry_after).encode()),
    ]
    return 429, headers, body


class RateLimitMiddleware:
    """Plain ASGI middleware. Wire after AuthMiddleware so we can bucket
    by token prefix (set on scope["sync_token_prefix"] by AuthMiddleware).
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope["path"]
        method = scope["method"]
        # Exempt healthz / endpoints / docs so health checks don't burn quota.
        if path in {"/healthz", "/endpoints", "/openapi.json", "/docs", "/redoc"}:
            await self.app(scope, receive, send)
            return

        bucket_key = scope.get("sync_token_prefix")
        if not bucket_key:
            # No auth context → use remote IP so unauthenticated traffic
            # is also rate-limited (defense against 401-flooding).
            client = scope.get("client") or ("?", 0)
            bucket_key = f"ip:{client[0]}"

        is_allowed, retry_after = allow(bucket_key)
        if not is_allowed:
            client = (scope.get("client") or ("?",))[0]
            sys.stderr.write(
                f"[analytics-rl] 429 {method} {path} from {client} "
                f"bucket={bucket_key[:16]} retry_after={retry_after}s\n"
            )
            s, hdrs, body = _deny(retry_after)
            await send({"type": "http.response.start", "status": s, "headers": hdrs})
            await send({"type": "http.response.body", "body": body})
            return

        await self.app(scope, receive, send)
