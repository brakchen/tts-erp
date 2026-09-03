"""TDD tests for :mod:`tts_erp_v2.proxy.miaoshou.rate_limit`.

Covers the :class:`TokenBucket` state machine and the upstream-response
classifiers (:func:`is_rate_limited_response`,
:func:`is_empty_due_to_rate_limit`). The classifier functions carry the
silent-truncation bug fix; the bucket is the runtime QPS-pacing primitive
behind every Miaoshou SDK call.

Tests:

* :class:`TokenBucket.__post_init__` default capacity
* :func:`TokenBucket.try_acquire` happy + exhausted
* :func:`TokenBucket.acquire` blocks until a token is available
  (we monkeypatch ``time.sleep`` so the test stays fast)
* :func:`is_rate_limited_response` — string code token match
* :func:`is_rate_limited_response` — int code (always False)
* :func:`is_rate_limited_response` — reason/message token match
* :func:`is_rate_limited_response` — ERP result="fail" + data is None shape
* :func:`is_rate_limited_response` — non-dict returns False
* :func:`is_empty_due_to_rate_limit` — passes when rate-limit-shaped
* :func:`is_empty_due_to_rate_limit` — empty list with expected pages
* :func:`is_empty_due_to_rate_limit` — empty list without expected pages
* :func:`is_empty_due_to_rate_limit` — non-dict returns False
* default rate constant sanity
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tts_erp_v2.proxy.miaoshou.rate_limit import (
    DEFAULT_RATE_PER_SECOND,
    TokenBucket,
    is_empty_due_to_rate_limit,
    is_rate_limited_response,
)

pytestmark = [pytest.mark.domain_miaoshou, pytest.mark.layer_integration]


# ─── TokenBucket: capacity / acquire / try_acquire ──────────────────


def test_token_bucket_default_capacity() -> None:
    """With no explicit ``capacity`` the bucket defaults to ``rate`` (no burst)."""
    b = TokenBucket(rate_per_second=0.5)
    assert b.capacity == 1.0  # max(rate, 1.0)
    # Starts full so the first acquire is non-blocking.
    assert b.try_acquire() is True


def test_token_bucket_capacity_floor_one() -> None:
    """A rate below 1 still floors capacity at 1.0 — the smallest meaningful
    bucket size (you can't burst less than one request)."""
    b = TokenBucket(rate_per_second=0.1)
    assert b.capacity == 1.0


def test_token_bucket_explicit_capacity() -> None:
    b = TokenBucket(rate_per_second=1.0, capacity=5.0)
    assert b.capacity == 5.0
    # 5 quick acquires succeed.
    for _ in range(5):
        assert b.try_acquire() is True
    # 6th fails.
    assert b.try_acquire() is False


def test_token_bucket_try_acquire_no_block() -> None:
    """``try_acquire`` is non-blocking — returns False on exhaustion, not
    a hang."""
    b = TokenBucket(rate_per_second=0.1, capacity=2.0)
    assert b.try_acquire() is True
    assert b.try_acquire() is True
    assert b.try_acquire() is False


def test_token_bucket_acquire_blocks_on_exhaustion() -> None:
    """``acquire`` waits for a token, then consumes it. We patch ``time.sleep``
    so the test does not actually sleep for ``1/rate`` seconds (1.2s for the
    default rate).

    Strategy: advance ``time.monotonic`` alongside each ``sleep`` so the
    bucket's internal ``_last`` ticks forward, allowing the refill branch
    in the next loop iteration to add enough tokens to satisfy the
    ``>= 1.0`` threshold. Without this, the bucket is forever empty and
    the test would hang or burn CPU.
    """
    sleep_calls: list[float] = []

    def advance_sleep(secs: float) -> None:
        sleep_calls.append(secs)

    fake_now = {"t": 0.0}

    def fake_mono() -> float:
        # First call returns 0 (bucket init). Subsequent calls return
        # a clock that advances whenever sleep is invoked.
        return fake_now["t"]

    def maybe_advance_clock() -> None:
        # Bump the fake clock by enough wall-clock to accrue ≥ 1 token.
        # At rate 0.1/sec, we need at least 10 seconds of "elapsed" per
        # token; floor the bump so refill is meaningful in test time.
        fake_now["t"] += 12.0

    def advance_sleep_then_tick(secs: float) -> None:
        sleep_calls.append(secs)
        maybe_advance_clock()

    with patch(
        "tts_erp_v2.proxy.miaoshou.rate_limit.time.sleep",
        side_effect=advance_sleep_then_tick,
    ), patch(
        "tts_erp_v2.proxy.miaoshou.rate_limit.time.monotonic",
        side_effect=fake_mono,
    ):
        b = TokenBucket(rate_per_second=0.1, capacity=1.0)
        # First acquire — bucket has capacity → no sleep needed.
        assert sleep_calls == []
        b.acquire()
        assert sleep_calls == []
        # Second acquire — bucket empty → loops with sleep until refill.
        b.acquire()

    # The loop terminated AND sleep was invoked at least once. We don't
    # pin the exact number — that's an implementation detail.
    assert len(sleep_calls) >= 1
    # All recorded sleeps were non-negative (the bucket always picks
    # the positive-wait branch because rate > 0).
    assert all(s >= 0 for s in sleep_calls)


def test_token_bucket_refills_over_time() -> None:
    """After a synthetic time advance, the bucket regains a token."""
    import time as _time

    b = TokenBucket(rate_per_second=10.0, capacity=1.0)
    assert b.try_acquire() is True
    # No time has passed → still empty.
    assert b.try_acquire() is False

    # Move monotonic clock forward by 1 second → at rate 10/s, 10 tokens
    # accumulated, but capacity caps at 1.
    with patch.object(_time, "monotonic") as mock_mono:
        mock_mono.return_value = b._last + 1.0  # noqa: SLF001 — test seam
        assert b.try_acquire() is True


def test_token_bucket_acquire_returns_after_refill() -> None:
    """The acquire loop terminates as soon as a token is available —
    we don't over-sleep."""
    b = TokenBucket(rate_per_second=1000.0, capacity=1.0)
    # Drain.
    assert b.try_acquire() is True
    assert b.try_acquire() is False

    # On real wall-clock time, the next acquire should succeed quickly
    # (we don't patch — rate is so high it's effectively instant).
    b.acquire()  # No exception, no infinite loop.


def test_default_rate_per_second_value() -> None:
    """The default is documented as 0.83 req/s (1 req every 1.2s) — slightly
    under the upstream limit so the bucket doesn't race."""
    assert pytest.approx(0.8333333, abs=1e-6) == DEFAULT_RATE_PER_SECOND


# ─── is_rate_limited_response ───────────────────────────────────────


def test_is_rate_limited_response_string_code_match() -> None:
    """The Miaoshou-specific token ``accountApiQpsRateLimit`` is the
    canonical "QPS limit" signal."""
    assert (
        is_rate_limited_response({"code": "accountApiQpsRateLimit"}) is True
    )
    assert is_rate_limited_response({"code": "qpsRateLimit"}) is True
    assert is_rate_limited_response({"code": "rateLimit"}) is True


def test_is_rate_limited_response_chinese_tokens() -> None:
    """Chinese rate-limit strings ("限流", "请求过于频繁") are matched too —
    some Miaoshou vendors localise the message."""
    assert is_rate_limited_response({"code": "限流"}) is True
    assert is_rate_limited_response({"code": "请求过于频繁"}) is True


def test_is_rate_limited_response_int_code_returns_false() -> None:
    """The Miaoshou open-platform uses string codes for biz errors; an int
    code is treated as NOT rate-limit-shaped (it goes through the int-failure
    path in the retry layer instead)."""
    assert is_rate_limited_response({"code": 429}) is False
    assert is_rate_limited_response({"code": 0}) is False
    assert is_rate_limited_response({"code": 500}) is False


def test_is_rate_limited_response_reason_string_match() -> None:
    """The classifier also matches on ``reason`` / ``message`` / ``msg``
    when ``code`` is None or non-rate-limit-shaped."""
    assert is_rate_limited_response({"reason": "rate limit exceeded"}) is True
    assert is_rate_limited_response({"message": "请求过于频繁"}) is True
    assert is_rate_limited_response({"msg": "rate-limit"}) is True


def test_is_rate_limited_response_erp_shape() -> None:
    """The ERP-shaped body (``result=="fail"``, ``data is None``, with a
    rate-limit token in reason/message) is recognised."""
    payload = {
        "result": "fail",
        "data": None,
        "reason": "accountApiQpsRateLimit",
    }
    assert is_rate_limited_response(payload) is True


def test_is_rate_limited_response_no_match() -> None:
    """An unrelated error envelope is NOT classified as rate-limit."""
    assert is_rate_limited_response({"code": "OTHER_ERROR"}) is False
    assert is_rate_limited_response({"code": "INVALID_PARAM"}) is False
    assert is_rate_limited_response({"message": "something else"}) is False


def test_is_rate_limited_response_non_dict() -> None:
    """Non-dict payloads (None, list, str) return False — defensive."""
    assert is_rate_limited_response(None) is False
    assert is_rate_limited_response([]) is False  # type: ignore[arg-type]
    assert is_rate_limited_response("rate limit") is False  # type: ignore[arg-type]


def test_is_rate_limited_response_no_data_on_erp_shape() -> None:
    """``result == "fail"`` + no ``reason`` token + missing ``data`` field
    falls through to False (the ERP shape requires BOTH a rate-limit token
    in reason/message AND data being None)."""
    assert (
        is_rate_limited_response(
            {"result": "fail", "data": "some data"}
        )
        is False
    )


# ─── is_empty_due_to_rate_limit ─────────────────────────────────────


def test_is_empty_due_to_rate_limit_passes_when_rate_limited() -> None:
    """If the payload itself looks like rate-limit, ``is_empty_due_to_rate_limit``
    returns True regardless of page-count heuristics."""
    payload = {
        "result": "fail",
        "data": None,
        "code": "accountApiQpsRateLimit",
    }
    assert is_empty_due_to_rate_limit(payload, requested_page=1) is True


def test_is_empty_due_to_rate_limit_empty_list_with_expected_pages() -> None:
    """Empty list with ``expected_nonempty_pages > requested_page`` → rate-limit
    truncation (we KNOW more pages exist)."""
    payload = {"data": [], "totalPage": 5}
    assert (
        is_empty_due_to_rate_limit(
            payload, requested_page=2, expected_nonempty_pages=5
        )
        is True
    )


def test_is_empty_due_to_rate_limit_empty_list_no_expected_pages() -> None:
    """Empty list WITHOUT ``expected_nonempty_pages`` → conservative False
    (we don't know if more pages exist; bail out rather than loop forever)."""
    payload = {"data": []}
    assert (
        is_empty_due_to_rate_limit(payload, requested_page=1) is False
    )


def test_is_empty_due_to_rate_limit_empty_list_past_last_expected_page() -> None:
    """Empty list PAST the last advertised page is real end-of-data, not
    truncation — return False. The classifier uses ``requested_page <=
    expected_nonempty_pages`` (inclusive), so the boundary case (page ==
    total) still classifies as rate-limit truncation."""
    payload = {"data": [], "totalPage": 3}
    # Past the last page → not classified as truncation.
    assert (
        is_empty_due_to_rate_limit(
            payload, requested_page=4, expected_nonempty_pages=3
        )
        is False
    )
    # At the last page → still classified as truncation (we expected more
    # but didn't get them — could be truncation).
    assert (
        is_empty_due_to_rate_limit(
            payload, requested_page=3, expected_nonempty_pages=3
        )
        is True
    )


def test_is_empty_due_to_rate_limit_non_empty_list() -> None:
    """A non-empty list is never "empty due to rate limit"."""
    payload = {"data": [{"id": 1}]}
    assert (
        is_empty_due_to_rate_limit(payload, requested_page=1) is False
    )


def test_is_empty_due_to_rate_limit_non_dict_payload() -> None:
    assert is_empty_due_to_rate_limit(None, requested_page=1) is False  # type: ignore[arg-type]


def test_is_empty_due_to_rate_limit_data_not_list() -> None:
    """``data`` not shaped as a list → return False (only list shape is
    the silent-truncation pattern we recognise)."""
    payload = {"data": None}
    assert (
        is_empty_due_to_rate_limit(
            payload, requested_page=1, expected_nonempty_pages=5
        )
        is False
    )
