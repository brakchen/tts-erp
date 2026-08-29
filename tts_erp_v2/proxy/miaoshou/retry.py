"""Retry layer for the Miaoshou SDK.

Three retry primitives, used together:

* :func:`call_with_retry` — generic wrapper for one HTTP call. Handles
  transient network errors + transient upstream status codes with
  exponential backoff + jitter. Caps at ``max_retries``.
* :func:`paginate_with_retry` — paginated list fetcher that **does
  not terminate on an empty page** when (a) the empty page is shaped
  like a rate-limit response, or (b) we already know more pages are
  expected. This is the fix for the silent-truncation bug observed
  in production (237 → 20 records dropped).

Backoff strategy
----------------
Exponential with jitter, base = 0.5s, factor = 2, capped at 30s.

Why we expose ``is_rate_limited_response`` here too
----------------------------------------------------
``call_with_retry`` classifies a *raised* :class:`MiaoshouApiError`.
``paginate_with_retry`` classifies a *successful-but-empty* page. Both
helpers must agree on what "rate-limited" looks like; they share the
classifiers from :mod:`tts_erp_v2.proxy.miaoshou.rate_limit`.
"""
from __future__ import annotations

import contextlib
import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from tts_erp_v2.proxy.errors import (
    ProxyError,
    RateLimitedError,
    TransientProxyError,
)
from tts_erp_v2.proxy.miaoshou.rate_limit import (
    is_rate_limited_response,
)

log = logging.getLogger("tts_erp_v2.proxy.miaoshou.retry")

T = TypeVar("T")

MAX_RETRIES_DEFAULT = 6
BACKOFF_BASE_SEC = 0.5
BACKOFF_CAP_SEC = 30.0


# ---- Errors raised by the SDK layer ---------------------------------


class MiaoshouRetryExhausted(TransientProxyError):
    """We retried the max number of times and still failed.

    Wraps the last underlying error so logs/handlers see context.
    """

    def __init__(self, attempts: int, last_error: BaseException) -> None:
        super().__init__(
            f"miaoshou call exhausted {attempts} attempts: {last_error!r}"
        )
        self.attempts = attempts
        self.last_error = last_error


# ---- Single-call retry ----------------------------------------------


def _sleep_backoff(attempt: int) -> None:
    """Exponential backoff with jitter. attempt is 1-based."""
    raw = BACKOFF_BASE_SEC * (2 ** (attempt - 1))
    capped = min(raw, BACKOFF_CAP_SEC)
    time.sleep(random.uniform(0.5, 1.0) * capped)


def _safe_on_retry(
    cb: Callable[[int, BaseException], None] | None,
    attempt: int,
    err: BaseException,
) -> None:
    """Invoke the user-supplied on_retry callback without ever
    letting observability break the retry loop.
    """
    if cb is None:
        return
    with contextlib.suppress(Exception):
        cb(attempt, err)


def call_with_retry(
    fn: Callable[[], T],
    *,
    is_retryable: Callable[[BaseException], bool],
    max_retries: int = MAX_RETRIES_DEFAULT,
    on_retry: Callable[[int, BaseException], None] | None = None,
) -> T:
    """Invoke ``fn`` with bounded retry on retryable failures.

    Args:
        fn: Zero-arg callable performing one upstream call.
        is_retryable: Predicate on a raised exception. ``True`` means
            we should retry (e.g. network blip, 5xx, rate-limit).
        max_retries: Total extra attempts after the first try
            (default 6 → 7 attempts total).
        on_retry: Optional callback ``(attempt_index, exception)`` for
            logging/metrics. Called *before* the backoff sleep.

    Raises:
        Whatever ``fn`` raises on the final attempt (not wrapped).
        :class:`MiaoshouRetryExhausted` if we run out of budget.
    """
    attempts = max_retries + 1
    last_err: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except BaseException as e:  # noqa: BLE001 -- we re-classify below
            last_err = e
            if not is_retryable(e):
                raise
            if attempt >= attempts:
                raise MiaoshouRetryExhausted(attempt, e) from e
            _safe_on_retry(on_retry, attempt, e)
            _sleep_backoff(attempt)
    # Unreachable: loop either returns or raises. Keep for mypy.
    raise MiaoshouRetryExhausted(  # pragma: no cover
        attempts, last_err if last_err is not None else RuntimeError("no attempt")
    )


def is_retryable_miaoshou_error(e: BaseException) -> bool:
    """Predicate: should we retry this exception from the Miaoshou layer?"""
    if isinstance(e, (TransientProxyError, RateLimitedError)):
        return True
    # Importing lazily to avoid a cycle at module load.
    from tts_erp_v2.proxy.miaoshou.client import MiaoshouApiError

    if isinstance(e, MiaoshouApiError):
        # MiaoshouApiError carries an HTTP status / biz code.
        code = getattr(e, "code", None)
        # Network errors come through as code=0; retry those.
        if code == 0:
            return True
        # String code that LOOKS like a rate limit.
        return isinstance(code, str) and is_rate_limited_response({"code": code})
    return False


# ---- Pagination loop with rate-limit awareness ----------------------


@dataclass
class PageResult:
    """One page's worth of items + metadata the pagination loop needs."""

    items: list[Any]
    page: int
    # Optional explicit total page count advertised by the upstream.
    # If provided, pagination terminates when ``page >= total_pages``.
    total_pages: int | None = None
    total_count: int | None = None


def _coerce_page_payload(
    result: PageResult | dict[str, Any],
) -> tuple[list[Any], int | None, int | None]:
    """Normalise ``fetch_page`` return into ``(items, total_pages, total)``."""
    if isinstance(result, PageResult):
        return list(result.items or []), result.total_pages, result.total_count
    if isinstance(result, dict):
        data = result.get("data")
        items = list(data) if isinstance(data, list) else []
        tp = result.get("totalPage") or result.get("total_pages")
        tc = result.get("total")
        return items, tp, tc
    raise ProxyError(
        f"paginate_with_retry: fetch_page returned "
        f"{type(result).__name__}, expected PageResult or dict"
    )


def _retry_fetch_page(
    fetch_page: Callable[[int], PageResult | dict[str, Any]],
    page: int,
    *,
    is_retryable: Callable[[BaseException], bool],
    max_retries: int,
    on_retry: Callable[[int, BaseException], None] | None,
) -> PageResult | dict[str, Any]:
    """Bind ``page`` into the retry callable without late-binding through a lambda.

    Extracted so the lambda captures ``page`` *now* (default-arg trick),
    sidestepping the loop-variable late-binding footgun.
    """
    return call_with_retry(
        lambda p=page: fetch_page(p),
        is_retryable=is_retryable,
        max_retries=max_retries,
        on_retry=on_retry,
    )


def paginate_with_retry(
    fetch_page: Callable[[int], PageResult | dict[str, Any]],
    *,
    start_page: int = 1,
    max_pages: int = 1000,
    max_retries: int = MAX_RETRIES_DEFAULT,
    on_retry: Callable[[int, BaseException], None] | None = None,
) -> tuple[list[Any], int]:
    """Walk all pages of a list endpoint, treating rate-limit empty
    pages as transient (do NOT terminate pagination on them).

    Args:
        fetch_page: callable(page_number) returning either a
            :class:`PageResult` or a raw dict (in which case ``data``
            must be the list, ``total``/``totalPage`` are optional).
        start_page: 1-based first page (default 1).
        max_pages: hard cap on pages walked (safety against infinite loops
            when upstream is misbehaving).
        max_retries: same semantics as :func:`call_with_retry`.
        on_retry: same semantics as :func:`call_with_retry`.

    Returns:
        ``(all_items, last_page_walked)``.

    Behaviour
    ---------
    * On success → collect items, advance to next page.
    * On raised :class:`MiaoshouApiError` matching rate-limit →
      retry the same page (with backoff) up to ``max_retries``.
    * On raised retryable network error → retry the same page.
    * On returned empty page that is shaped like a rate-limit response
      AND we know more pages are expected → retry the same page.
    * On returned empty page with NO signal that more pages exist →
      terminate pagination naturally.
    * On raised non-retryable exception → propagate.
    """
    collected: list[Any] = []
    expected_total_pages: int | None = None
    last_page = start_page - 1

    for page in range(start_page, start_page + max_pages):
        last_page = page

        # Step 1: fetch + handle raised retryable errors.
        try:
            result = fetch_page(page)
        except BaseException as e:  # noqa: BLE001
            if not is_retryable_miaoshou_error(e):
                raise
            _safe_on_retry(on_retry, page, e)
            result = _retry_fetch_page(
                fetch_page, page,
                is_retryable=is_retryable_miaoshou_error,
                max_retries=max_retries,
                on_retry=on_retry,
            )

        # Step 2: detect upstream-shown rate-limit (200 OK + fail-shaped body).
        if isinstance(result, dict) and is_rate_limited_response(result):
            _safe_on_retry(on_retry, page, RuntimeError(f"rate-limit shape: {result}"))
            result = _retry_fetch_page(
                fetch_page, page,
                is_retryable=is_retryable_miaoshou_error,
                max_retries=max_retries,
                on_retry=on_retry,
            )

        # Step 3: normalise into items + totals.
        items, tp, tc = _coerce_page_payload(result)

        # Track advertised totals.
        if isinstance(tp, int) and tp > 0:
            expected_total_pages = tp
        elif isinstance(tc, int) and expected_total_pages is None and tc > 0:
            expected_total_pages = tc

        # Step 4: empty-page handling. If we KNOW more pages exist,
        # the empty page is rate-limit truncation — retry once.
        if not items:
            if (
                expected_total_pages is not None
                and page <= expected_total_pages
            ):
                _safe_on_retry(
                    on_retry,
                    page,
                    RuntimeError(
                        f"empty page {page} but expected_total_pages="
                        f"{expected_total_pages}"
                    ),
                )
                try:
                    result = _retry_fetch_page(
                        fetch_page, page,
                        is_retryable=lambda e: True,
                        max_retries=max_retries,
                        on_retry=on_retry,
                    )
                    items, _tp, _tc = _coerce_page_payload(result)
                except Exception:
                    break  # Genuine empty — terminate pagination.
                if not items:
                    break  # Still empty after retry.
            else:
                break  # No signal that more pages exist.

        collected.extend(items)
        # Last advertised page reached — terminate.
        if expected_total_pages is not None and page >= expected_total_pages:
            break

    return collected, last_page


# ---- Public alias for the new SDK -----------------------------------

#: Alias kept for readability inside the SDK package.
paginate = paginate_with_retry


__all__ = [
    "call_with_retry",
    "paginate_with_retry",
    "paginate",
    "is_retryable_miaoshou_error",
    "MiaoshouRetryExhausted",
    "PageResult",
    "MAX_RETRIES_DEFAULT",
]
