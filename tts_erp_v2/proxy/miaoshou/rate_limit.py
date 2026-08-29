"""Token-bucket rate limiter for the Miaoshou SDK.

The Miaoshou open platform enforces an account-level QPS limit
(``accountApiQpsRateLimit`` — observed in production during the
2026-08-29 ``search_move_collect_list`` probe). The limit is *silent*:
the upstream returns ``{"result":"fail","data":null}`` with the error
code embedded somewhere in ``reason`` or a similar field — NOT a
distinct HTTP status. This makes naïve pagination loops believe
they've reached the last page and silently truncate (237 records →
20 saved in the first sync attempt before we patched manually).

This module gives callers a thread-safe token bucket that simply
paces requests, and a :func:`is_rate_limited_response` classifier
the retry layer uses to recognise the empty-list-on-rate-limit pattern.

Design notes
------------
* The bucket is **per-instance**, not global — the sync-worker has one
  MiaoshouClient per process, so a per-instance bucket IS the global
  bucket for that process. Tests construct fresh instances.
* Default rate: **0.83 req/s** (1 request per 1.2s) — slightly under
  the observed limit so the bucket does not race the upstream.
* No async/threading — Miaoshou SDK is sync; one thread per worker.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass

# Default: 1 request every 1.2 seconds (≈ 0.83 req/s). Tunable via env.
DEFAULT_RATE_PER_SECOND = 0.8333333


@dataclass
class TokenBucket:
    """Thread-safe token bucket.

    ``rate_per_second`` is the steady-state refill rate. ``capacity``
    caps the burst — set equal to ``rate`` to fully smooth the flow.
    """

    rate_per_second: float = DEFAULT_RATE_PER_SECOND
    capacity: float | None = None  # defaults to rate_per_second (=no burst)

    def __post_init__(self) -> None:
        if self.capacity is None:
            self.capacity = max(self.rate_per_second, 1.0)
        self._tokens: float = self.capacity
        self._last: float = time.monotonic()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        """Refill tokens based on elapsed time. Caller holds the lock."""
        now = time.monotonic()
        elapsed = now - self._last
        if elapsed > 0:
            self._tokens = min(
                self.capacity,  # type: ignore[operator]
                self._tokens + elapsed * self.rate_per_second,
            )
            self._last = now

    def acquire(self) -> None:
        """Block until a token is available, then consume it."""
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                # Time until next token
                deficit = 1.0 - self._tokens
                wait = deficit / self.rate_per_second if self.rate_per_second > 0 else 0.1
            # Sleep outside the lock so multiple waiters don't serialise
            # on the lock itself.
            time.sleep(wait)

    def try_acquire(self) -> bool:
        """Non-blocking variant. Returns True if a token was taken."""
        with self._lock:
            self._refill()
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True
            return False


# ---- Upstream-response classifier ------------------------------------
#
# Patterns observed in production (2026-08-29 probe scripts):
#   * {"result":"fail","code":"accountApiQpsRateLimit","reason":"..."}
#   * {"result":"fail","data":null,"message":"rate limit"}
#   * On a "list" endpoint during rate limit: data is null and the
#     page-count logic sees no items → silent truncation.
#
# We classify by *shape*, not exact field name, because the SDK is
# shared across many Miaoshou vendors and field names drift.

_RATE_LIMIT_TOKENS: tuple[str, ...] = (
    "accountApiQpsRateLimit",
    "qpsRateLimit",
    "rateLimit",
    "rate limit",
    "rate-limit",
    "请求过于频繁",  # Chinese: "too many requests"
    "限流",          # "rate-limited"
)


def is_rate_limited_response(payload: dict | None) -> bool:
    """Return True if the parsed JSON payload looks like a Miaoshou
    account-level QPS rate-limit response.

    Does NOT inspect ``data`` directly — the empty-list pattern is
    handled in :func:`is_empty_due_to_rate_limit`, which is the *other*
    half of the silent-truncation bug fix.
    """
    if not isinstance(payload, dict):
        return False
    # Explicit error code wins.
    code = payload.get("code")
    if isinstance(code, str) and any(tok in code for tok in _RATE_LIMIT_TOKENS):
        return True
    if isinstance(code, int):
        # Miaoshou uses string codes for biz errors; an int code is not
        # rate-limit shaped.
        return False
    # Fall back to reason/message string match.
    for key in ("reason", "message", "msg"):
        val = payload.get(key)
        if isinstance(val, str) and any(tok in val for tok in _RATE_LIMIT_TOKENS):
            return True
    # Distinct "result" field used by ERP API (HMAC-SHA256 side).
    result = payload.get("result")
    return result == "fail" and payload.get("data") is None and any(
        isinstance(payload.get(k), str) and any(tok in payload[k] for tok in _RATE_LIMIT_TOKENS)
        for k in ("reason", "message")
    )


def is_empty_due_to_rate_limit(
    payload: dict | None,
    *,
    requested_page: int,
    expected_nonempty_pages: int | None = None,
) -> bool:
    """Heuristic: did this page come back empty *because* we got
    rate-limited, not because it really is the last page?

    Args:
        payload: Parsed response JSON.
        requested_page: 1-based page index the caller asked for.
        expected_nonempty_pages: If the caller knows the total page
            count (e.g. from a previous page's ``total``/``totalPage``
            field), and ``requested_page`` is below it, an empty list
            is *not* the last page — it's rate-limited truncation.

    The function is conservative: if we cannot make a positive case
    for "this is rate limit", we return False so the caller does
    NOT loop forever on a real empty result.
    """
    if not isinstance(payload, dict):
        return False
    if is_rate_limited_response(payload):
        return True
    # Empty-list shape: data is list with zero items AND the response
    # does NOT advertise an explicit total/page count of zero.
    data = payload.get("data")
    if isinstance(data, list) and len(data) == 0:
        # If total > 0 was previously seen, an empty page below total
        # is rate-limit truncation.
        return (
            expected_nonempty_pages is not None
            and requested_page <= expected_nonempty_pages
        )
    return False


__all__ = [
    "TokenBucket",
    "DEFAULT_RATE_PER_SECOND",
    "is_rate_limited_response",
    "is_empty_due_to_rate_limit",
]
