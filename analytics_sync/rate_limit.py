"""Per-api-key rate limiter for analytics_sync.

Sliding 60s window bucketed by `api_key_hash` (set by SyncAuthMiddleware).
Same semantics as tts-erp/tdd/rate_limit.py but standalone for analytics_sync.

Returned response includes Retry-After in seconds (integer ≥ 1).
"""
from __future__ import annotations

import os
import sys
import time
from collections import deque

DEFAULT_LIMIT = 100
WINDOW_S = 60.0


def _limit() -> int:
    try:
        return max(1, int(os.environ.get("ANALYTICS_SYNC_RATE_LIMIT_PER_MIN", DEFAULT_LIMIT)))
    except ValueError:
        return DEFAULT_LIMIT


_buckets: dict[str, deque[float]] = {}


def reset_buckets() -> None:
    """Test helper."""
    _buckets.clear()


def allow(key: str) -> tuple[bool, int]:
    now = time.monotonic()
    cutoff = now - WINDOW_S
    bucket = _buckets.setdefault(key, deque())
    while bucket and bucket[0] < cutoff:
        bucket.popleft()
    quota = _limit()
    if len(bucket) >= quota:
        retry_after = max(1, int(WINDOW_S - (now - bucket[0])) + 1)
        return False, retry_after
    bucket.append(now)
    return True, 0


def _deny_response(retry_after: int):
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


class SyncRateLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope["path"]
        method = scope["method"]
        if path in {"/healthz", "/endpoints", "/openapi.json", "/docs", "/redoc"}:
            await self.app(scope, receive, send)
            return

        bucket_key = scope.get("api_key_hash")
        if not bucket_key:
            client = scope.get("client") or ("?", 0)
            bucket_key = f"ip:{client[0]}"

        ok, retry_after = allow(bucket_key)
        if not ok:
            client = (scope.get("client") or ("?",))[0]
            sys.stderr.write(
                f"[sync-rl] 429 {method} {path} from {client} "
                f"bucket={bucket_key[:16]} retry_after={retry_after}s\n"
            )
            s, hdrs, body = _deny_response(retry_after)
            await send({"type": "http.response.start", "status": s, "headers": hdrs})
            await send({"type": "http.response.body", "body": body})
            return

        await self.app(scope, receive, send)
