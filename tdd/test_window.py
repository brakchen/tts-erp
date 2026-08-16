"""TDD test suite for sync_cron.py compute_window + constants.

compute_window is the heart of the incremental sync strategy:
- last_epoch=None → fall back to 7 days ago
- last_epoch=N → return N - 5min, but never earlier than now - 7 days

The 5-min buffer guards against TikTok server clock drift / async landing.
The 7-day floor prevents re-scanning ancient history if sync_log is somehow
wiped or contains corrupt data.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Add sync_cron.py's directory to path
SYNC_CRON_DIR = Path(__file__).resolve().parent.parent
if str(SYNC_CRON_DIR) not in sys.path:
    sys.path.insert(0, str(SYNC_CRON_DIR))

from sync_cron import compute_window, WINDOW_BACKOFF_SEC, FALLBACK_LOOKBACK_SEC


# Sanity: constants should match design intent
def test_constants_match_design():
    assert WINDOW_BACKOFF_SEC == 5 * 60, "backoff must be exactly 5 minutes"
    assert FALLBACK_LOOKBACK_SEC == 7 * 24 * 3600, "fallback must be exactly 7 days"


class TestComputeWindowNoHistory:
    """last_epoch=None means no prior successful sync → use fallback window."""

    def test_returns_now_minus_7_days_when_no_history(self):
        now = 1_700_000_000
        result = compute_window(None, now)
        assert result == now - 7 * 24 * 3600

    def test_no_history_ignores_clock_arbitrarily(self):
        # Even with a wildly different now, fallback is always exactly 7 days
        now = 2_000_000_000  # far in future
        result = compute_window(None, now)
        assert result == now - 7 * 24 * 3600


class TestComputeWindowWithHistory:
    """last_epoch is set → use it minus 5-min buffer, but respect 7-day floor."""

    def test_recent_last_returns_minus_5min(self):
        now = 1_700_000_000
        last = now - 60  # 1 minute ago
        # ge = last - 5min = now - 6min
        assert compute_window(last, now) == now - 6 * 60

    def test_just_now_last_returns_minus_5min(self):
        now = 1_700_000_000
        # last = now → ge = now - 5min (buffer still applied)
        assert compute_window(now, now) == now - 5 * 60

    def test_very_old_last_capped_at_7day_floor(self):
        now = 1_700_000_000
        # last = 100 days ago — but 7-day floor means we don't go earlier than now-7d
        last = now - 100 * 24 * 3600
        # Without floor: ge = last - 5min = now - 100d - 5min (way too old)
        # With floor: ge = now - 7d (the floor)
        assert compute_window(last, now) == now - 7 * 24 * 3600

    def test_7day_boundary_just_above_floor(self):
        now = 1_700_000_000
        # last = 6 days, 23 hours, 55 minutes ago — last - 5min = 7d exactly
        last = now - (7 * 24 * 3600 - 5 * 60)
        assert compute_window(last, now) == now - 7 * 24 * 3600

    def test_7day_boundary_just_below_floor(self):
        now = 1_700_000_000
        # last = 7 days, 1 minute ago — last - 5min = 7d + 6min (past floor)
        last = now - (7 * 24 * 3600 + 60)
        # Should cap at floor
        assert compute_window(last, now) == now - 7 * 24 * 3600

    def test_idempotent_when_now_equals_last(self):
        # Edge: last == now → ge = now - 5min (not 0, not -5min)
        now = 1_700_000_000
        assert compute_window(now, now) == now - 5 * 60

    def test_future_last_clamped_via_buffer(self):
        # Pathological: last > now (clock skew between servers)
        # ge = last - 5min — still a "future" timestamp relative to now
        # Behavior: implementation returns the buffer result without clamping to now.
        # Documented as "trust the source" — sync_log.finished_at is server-side.
        now = 1_700_000_000
        last = now + 3600  # 1 hour in the future
        assert compute_window(last, now) == last - 5 * 60


class TestComputeWindowReturnType:
    def test_returns_int(self):
        assert isinstance(compute_window(None, 1_700_000_000), int)
        assert isinstance(compute_window(1_700_000_000, 1_700_000_000), int)
