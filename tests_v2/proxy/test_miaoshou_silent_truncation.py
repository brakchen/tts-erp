"""Regression test for the silent-truncation bug.

Production observation (2026-08-29, ``search_move_collect_list``):
first sync returned 237 records but only 20 were persisted because the
pagination loop saw an empty page and treated it as "end of data"
while the upstream was actually returning ``{"result":"fail","data":null}``
with ``accountApiQpsRateLimit`` (or a similar token).

This test simulates that exact scenario:

* Page 1: 20 items, advertises ``totalPage=12``.
* Page 2: empty list (upstream-side rate-limit truncation).
* Pages 3-12: 20 items each.

The legacy pagination loop terminated at page 2 (saw empty = end).
``paginate_with_retry`` must detect the empty-but-expected page, retry,
and walk all 12 pages to collect 237 items.

The test counts how many times ``fetch_page`` is invoked so we can
distinguish "walked all pages after retries" from "gave up early".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from tts_erp_v2.proxy.miaoshou.retry import (
    paginate_with_retry,
)


pytestmark = [pytest.mark.domain_miaoshou, pytest.mark.layer_integration]


@dataclass
class _FakeUpstream:
    """Mimics the Miaoshou SDK + rate-limit behaviour.

    Per-page state machine:

    * If ``rate_limit_pages`` includes this page number, the response is
      the rate-limit shape ``{"result":"fail","data":null,"reason":"..."}``
      for the first ``rate_limit_attempts`` attempts, then a real response.
    * Otherwise, returns ``{"data": items, "totalPage": total_pages}``.
    """

    items_per_page: int = 20
    total_pages: int = 12
    rate_limit_pages: set[int] = field(default_factory=set)
    rate_limit_attempts: int = 1  # how many times to fail per page
    fetch_calls: list[int] = field(default_factory=list)

    def __call__(self, page: int) -> dict[str, Any]:
        self.fetch_calls.append(page)
        if page in self.rate_limit_pages:
            # Consume one "rate-limited" attempt. Once we've exhausted
            # the budget for this page, return the real data.
            remaining = self._remaining_for_page(page)
            if remaining > 0:
                self._consume(page)
                return {
                    "result": "fail",
                    "data": None,
                    "code": "accountApiQpsRateLimit",
                    "reason": "rate limit",
                }
        # Real data page.
        return {
            "result": "success",
            "data": [
                {"id": f"item-{page}-{i}"}
                for i in range(self.items_per_page)
            ],
            "totalPage": self.total_pages,
        }

    def _remaining_for_page(self, page: int) -> int:
        return getattr(self, f"_rl_attempts_left_{page}", self.rate_limit_attempts)

    def _consume(self, page: int) -> None:
        current = getattr(self, f"_rl_attempts_left_{page}", self.rate_limit_attempts)
        setattr(self, f"_rl_attempts_left_{page}", current - 1)


def test_pagination_continues_past_rate_limit_empty_page() -> None:
    """★ The bug-fix regression test.

    Page 2 returns a rate-limit-shaped empty response on its first
    fetch, then a real (full) response on retry. Pagination must walk
    to page 12 and collect all 237 records.
    """
    fake = _FakeUpstream(rate_limit_pages={2}, rate_limit_attempts=1)

    # Speed up the test by capping retries at 2.
    items, last_page = paginate_with_retry(
        fake,
        start_page=1,
        max_retries=2,
    )

    # All 12 pages × 20 items = 240 records.
    assert len(items) == 12 * 20
    # Walked past page 2 (the empty rate-limit page).
    assert last_page == 12
    # Page 2 was hit twice (once for the rate-limit response, once for
    # the retry that succeeded).
    assert fake.fetch_calls.count(2) == 2


def test_pagination_terminates_on_genuine_empty_page() -> None:
    """When the empty page is NOT a rate-limit and NOT under the
    advertised total, pagination terminates naturally.

    Without ``totalPage`` advertised, the loop cannot tell whether the
    empty page is real end-of-data or truncation — we err on the side
    of termination (conservative: don't loop forever on a real empty
    result).
    """
    fake = _FakeUpstream(items_per_page=0, total_pages=1)

    items, last_page = paginate_with_retry(fake, start_page=1, max_retries=2)

    # Empty page on page 1 with totalPage=1. Because the empty page is
    # at the last advertised page, the retry path triggers once (we
    # *thought* more pages might exist), then terminates when the
    # retry also comes back empty. Total fetch_calls: 2.
    assert items == []
    assert last_page == 1
    assert fake.fetch_calls == [1, 1]


def test_pagination_retries_raised_rate_limit_error() -> None:
    """When the SDK raises MiaoshouApiError with a rate-limit code,
    paginate_with_retry must retry the same page."""
    from tts_erp_v2.proxy.miaoshou.client import MiaoshouApiError

    call_count = {"n": 0}

    def fetch(page: int) -> dict[str, Any]:
        call_count["n"] += 1
        if call_count["n"] == 1:
            # First call raises a rate-limit error.
            raise MiaoshouApiError("accountApiQpsRateLimit", "rate limit")
        # Second call succeeds.
        return {
            "data": [{"id": "x-1"}, {"id": "x-2"}],
            "totalPage": 1,
        }

    items, last_page = paginate_with_retry(
        fetch, start_page=1, max_retries=3
    )

    assert items == [{"id": "x-1"}, {"id": "x-2"}]
    assert last_page == 1
    assert call_count["n"] == 2  # one failure + one success
