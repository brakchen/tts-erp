"""TDD test suite for sync_cron.discover_shops.

Wave 3 Slice 2 deleted the /shops HTTP route. sync_cron still calls
GET /shops → 404 → cron dies silently at step 1 every 10 minutes. This
test pins down the new contract: discover_shops MUST go through
oauth_receiver_core.db_list_shops in-process (no HTTP), and MUST NOT
silently fail when oauth DB is unreachable (graceful empty list).
"""

from __future__ import annotations

from contextlib import suppress
from typing import Any, cast
from unittest.mock import patch

import psycopg

import sync_cron

# ─── Test doubles ─────────────────────────────────────────────────────


class FakeHttpUrlopen:
    """Spy that raises if urllib.request.urlopen is ever called.

    discover_shops MUST NOT make any HTTP request — it talks to
    oauth_receiver_core in-process (Wave 3 Slice 2).
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def __call__(self, req: Any, *args: Any, **kwargs: Any):
        url = getattr(req, "full_url", str(req))
        self.calls.append((getattr(req, "method", "?"), url))
        raise AssertionError(
            f"discover_shops must not make HTTP calls, but tried {url}"
        )


# ─── discover_shops contract ──────────────────────────────────────────


def test_discover_shops_returns_shop_ids_from_oauth_core():
    """discover_shops delegates to oauth_receiver_core.db_list_shops.

    db_list_shops filters by provider at the SQL level — the mock mirrors
    that behavior via a side_effect so the test exercises the real contract.
    """
    fake_rows = [
        {"shop_id": "111", "provider": "tiktok"},
        {"shop_id": "222", "provider": "tiktok"},
        {"shop_id": "333", "provider": "miaoshou"},  # different provider
    ]

    def mock_db_list_shops(provider=None):
        return [
            r for r in fake_rows if provider is None or r.get("provider") == provider
        ]

    with patch.object(sync_cron, "oauth_receiver_core") as mock_oc:
        mock_oc.is_db_ok.return_value = True
        mock_oc.db_list_shops.side_effect = mock_db_list_shops
        result = sync_cron.discover_shops(provider="tiktok")

    # Filtered by provider="tiktok" at the DB layer
    mock_oc.db_list_shops.assert_called_once_with(provider="tiktok")
    assert result == ["111", "222"]


def test_discover_shops_skips_rows_without_shop_id():
    """Rows missing shop_id are silently dropped (defensive)."""
    fake_rows = [
        {"shop_id": "111", "provider": "tiktok"},
        {"provider": "tiktok"},  # no shop_id
        {"shop_id": "", "provider": "tiktok"},  # empty shop_id
        {"shop_id": None, "provider": "tiktok"},  # None shop_id
    ]
    with patch.object(sync_cron, "oauth_receiver_core") as mock_oc:
        mock_oc.is_db_ok.return_value = True
        mock_oc.db_list_shops.return_value = fake_rows
        result = sync_cron.discover_shops(provider="tiktok")

    assert result == ["111"]


def test_discover_shops_returns_empty_when_db_not_ok():
    """If oauth DB init failed, is_db_ok()=False → return [] (don't raise).

    The cron must NOT raise — main() catches exceptions and logs them,
    but we want the actual sync work to be a no-op (not a hard failure)
    when the oauth DB is unreachable.
    """
    with patch.object(sync_cron, "oauth_receiver_core") as mock_oc:
        mock_oc.is_db_ok.return_value = False
        mock_oc.db_list_shops.side_effect = AssertionError(
            "db_list_shops must NOT be called when is_db_ok=False"
        )
        result = sync_cron.discover_shops(provider="tiktok")

    assert result == []
    mock_oc.db_list_shops.assert_not_called()


def test_discover_shops_makes_no_http_requests():
    """Regression guard: discover_shops MUST NOT issue any HTTP call.

    Original buggy implementation hit GET /shops — which was deleted in
    Wave 3 Slice 2, so every cron run died with 404. This test will
    fail loudly if anyone re-introduces an HTTP call inside discover_shops.
    """
    fake_rows = [{"shop_id": "999", "provider": "tiktok"}]
    spy = FakeHttpUrlopen()
    with (
        patch.object(sync_cron, "oauth_receiver_core") as mock_oc,
        patch.object(sync_cron.urllib.request, "urlopen", spy),
    ):
        mock_oc.is_db_ok.return_value = True
        mock_oc.db_list_shops.return_value = fake_rows
        result = sync_cron.discover_shops(provider="tiktok")

    assert result == ["999"]
    assert spy.calls == [], f"unexpected HTTP calls: {spy.calls}"


def test_discover_shops_passes_through_provider():
    """provider arg is forwarded to db_list_shops (so future callers can
    filter by 'miaoshou' etc. without re-implementing)."""
    with patch.object(sync_cron, "oauth_receiver_core") as mock_oc:
        mock_oc.is_db_ok.return_value = True
        mock_oc.db_list_shops.return_value = []
        sync_cron.discover_shops(provider="miaoshou")

    mock_oc.db_list_shops.assert_called_once_with(provider="miaoshou")


# ─── main() signature regression guard ────────────────────────────────


def test_main_no_longer_calls_http_shops_endpoint():
    """Regression guard: sync_cron.main() must not invoke any HTTP call
    against the tts-erp /shops route. This is the actual production bug
    — every 10 minutes the cron died at discover_shops with HTTP 404.
    """
    spy = FakeHttpUrlopen()
    with (
        patch.object(sync_cron, "oauth_receiver_core") as mock_oc,
        patch.object(sync_cron.urllib.request, "urlopen", spy),
        patch.object(sync_cron.time, "time", return_value=1_700_000_000),
    ):
        mock_oc.is_db_ok.return_value = True
        mock_oc.db_list_shops.return_value = []
        # main() reads .env then exits early on empty shop list — that's OK
        with suppress(SystemExit):
            sync_cron.main()

    # Either no HTTP at all, or none targeting /shops
    for method, url in spy.calls:
        assert "/shops" not in url, (
            f"main() still issues HTTP to /shops: {method} {url}"
        )


# ─── L1 watermark optimization ────────────────────────────────────────
#
# Goal: avoid the 99%-empty upstream calls on returns / cancellations /
# orders by tracking local MAX(update_time) and only asking TikTok for
# rows whose update_time is newer than the local watermark.
#
# Strategy: extract two pure helpers from the cron main loop so they're
# individually unit-testable (the main() function is too side-effect-
# heavy to mock cleanly).
#
#   1) watermark_value(conn, shop_id, table, column) -> int | None
#      — reads MAX(<column>) FROM <table> WHERE shop_id = ?
#
#   2) compute_l1_body(plan, shop_id, local_watermark, now_epoch) -> dict | None
#      — decides the body for the upstream call (None = SKIP the HTTP call)
#
# Four branches compute_l1_body must implement:
#   - plan has no `watermark` config             → 7-day create_time backfill
#   - plan has watermark, local_watermark is None → 7-day create_time backfill (first sync)
#   - plan has watermark, local exists + fresh (< THRESHOLD)  → return None (skip)
#   - plan has watermark, local exists + stale                → use update_time_ge = watermark - OVERLAP


# ─── watermark_value: DB query helper ─────────────────────────────────


class _FakeCursor:
    def __init__(self, fetchone_return):
        self._fetchone_return = fetchone_return
        self.executed: list[tuple[str, tuple]] = []

    def execute(self, sql, params):
        self.executed.append((sql, params))

    def fetchone(self):
        return self._fetchone_return

    def close(self):
        pass

    # Context manager protocol — watermark_value uses `with conn.cursor() as cur:`
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class _FakeConn:
    def __init__(self, fetchone_return):
        self._cursor = _FakeCursor(fetchone_return)

    def cursor(self):
        return self._cursor


class TestWatermarkValue:
    def test_returns_max_value_when_rows_exist(self):
        from sync_cron import watermark_value

        conn = cast(psycopg.Connection, _FakeConn((1_750_000_000,)))
        result = watermark_value(conn, "shop-1", "returns", "update_time")
        assert result == 1_750_000_000

    def test_returns_none_for_empty_table(self):
        from sync_cron import watermark_value

        conn = cast(psycopg.Connection, _FakeConn((None,)))
        result = watermark_value(conn, "shop-1", "returns", "update_time")
        assert result is None

    def test_sql_filters_by_shop_id_and_aggregates_column(self):
        from psycopg.sql import Composed

        from sync_cron import watermark_value

        fake = _FakeConn((123,))
        conn = cast(psycopg.Connection, fake)
        watermark_value(conn, "shop-XYZ", "returns", "update_time")
        sql_obj, params = fake._cursor.executed[0]
        # Critical safety contract: shop_id is parameterized (%s), not
        # inlined into the SQL string. SQL injection via shop_id is impossible.
        assert params == ("shop-XYZ",)
        # SQL is built via psycopg3's safe Composed (sql.SQL().format() with
        # sql.Identifier() for table/column). Verify the composition happened:
        assert isinstance(sql_obj, Composed)
        # Verify the shape via the Composed repr (psycopg3 doesn't expose
        # a stable .string API for parts, but repr contains all parts).
        sql_repr = repr(sql_obj)
        # shop_id is parameterized → its value must NOT appear in SQL
        assert "shop-XYZ" not in sql_repr
        # But the WHERE clause + parameter placeholder are there
        assert "shop_id" in sql_repr
        assert "%s" in sql_repr
        # The dynamic identifiers (column + table) flow through
        assert "update_time" in sql_repr
        assert "returns" in sql_repr
        # MAX(...) shell is in the static SQL fragment
        assert "max(" in sql_repr.lower()


# ─── compute_l1_body: pure decision logic ─────────────────────────────


_PLAN_RETURNS_WITH_WATERMARK = {
    "key": "returns",
    "path": "/sync/returns",
    "time_field": "create_time_ge",
    "watermark": {
        "table": "returns",
        "column": "update_time",
        "body_field": "update_time_ge",
    },
    "log_type": "returns",
}

_PLAN_PAYMENTS_NO_WATERMARK = {
    "key": "payments",
    "path": "/sync/payments",
    "time_field": "create_time_ge",
    "watermark": None,
    "log_type": "payments",
}


class TestComputeL1Body:
    """Pin down the four branches of compute_l1_body."""

    def test_no_watermark_config_uses_7day_create_time_backfill(self):
        # Plans without `watermark` (payments, statements, statement_transactions)
        # must keep the original behavior: create_time_ge = now - 7d.
        from sync_cron import FALLBACK_LOOKBACK_SEC, compute_l1_body

        now = 1_700_000_000
        body = compute_l1_body(_PLAN_PAYMENTS_NO_WATERMARK, "shop-1", None, now)
        assert body is not None
        assert body["create_time_ge"] == now - FALLBACK_LOOKBACK_SEC
        assert "update_time_ge" not in body

    def test_no_watermark_config_ignores_local_watermark_even_if_present(self):
        # Defensive: even if caller passes a local watermark, plans without
        # watermark config should ignore it (otherwise we'd accidentally
        # apply L1 to plans that lack an update_time column).
        from sync_cron import compute_l1_body

        now = 1_700_000_000
        body = compute_l1_body(
            _PLAN_PAYMENTS_NO_WATERMARK, "shop-1", 1_700_000_000 - 600, now
        )
        assert body is not None
        assert "create_time_ge" in body
        assert "update_time_ge" not in body

    def test_first_sync_local_watermark_none_uses_7day_backfill(self):
        # Plan has watermark config but no local data → 7-day create_time
        # backfill (we don't have a watermark yet to anchor on).
        from sync_cron import FALLBACK_LOOKBACK_SEC, compute_l1_body

        now = 1_700_000_000
        body = compute_l1_body(_PLAN_RETURNS_WITH_WATERMARK, "shop-1", None, now)
        assert body is not None
        assert body["create_time_ge"] == now - FALLBACK_LOOKBACK_SEC
        assert "update_time_ge" not in body

    def test_fresh_local_watermark_returns_none_to_skip_http(self):
        # L1 core win: local watermark < FRESHNESS_THRESHOLD seconds old →
        # return None to signal "skip the HTTP call entirely".
        from sync_cron import FRESHNESS_THRESHOLD_SEC, compute_l1_body

        now = 1_700_000_000
        fresh = now - FRESHNESS_THRESHOLD_SEC + 5  # just inside the threshold
        body = compute_l1_body(_PLAN_RETURNS_WITH_WATERMARK, "shop-1", fresh, now)
        assert body is None

    def test_stale_local_watermark_uses_update_time_ge_with_overlap(self):
        # Stale watermark → ask upstream for rows updated since
        # (watermark - WATERMARK_OVERLAP_SEC). Overlap compensates for
        # clock drift and TikTok's eventual consistency on update_time.
        from sync_cron import WATERMARK_OVERLAP_SEC, compute_l1_body

        now = 1_700_000_000
        stale = now - 600  # 10 min old
        body = compute_l1_body(_PLAN_RETURNS_WITH_WATERMARK, "shop-1", stale, now)
        assert body is not None
        assert body["update_time_ge"] == stale - WATERMARK_OVERLAP_SEC
        # When using update_time_ge, do NOT also send create_time_ge
        # (the two filters are mutually exclusive in practice — we
        # choose the more selective one).
        assert "create_time_ge" not in body

    def test_exactly_at_threshold_returns_none(self):
        # Edge case: watermark == now - FRESHNESS_THRESHOLD → still "fresh"
        # (skip). Off-by-one: the threshold is inclusive (>= threshold).
        from sync_cron import FRESHNESS_THRESHOLD_SEC, compute_l1_body

        now = 1_700_000_000
        watermark = now - FRESHNESS_THRESHOLD_SEC
        body = compute_l1_body(_PLAN_RETURNS_WITH_WATERMARK, "shop-1", watermark, now)
        assert body is None

    def test_body_field_name_is_configurable(self):
        # Different plans may map to different upstream body field names.
        # The helper must use plan["watermark"]["body_field"], not a
        # hardcoded "update_time_ge".
        from sync_cron import compute_l1_body

        plan = {
            **_PLAN_RETURNS_WITH_WATERMARK,
            "watermark": {
                "table": "returns",
                "column": "update_time",
                "body_field": "custom_filter_field",
            },
        }
        now = 1_700_000_000
        body = compute_l1_body(plan, "shop-1", now - 600, now)
        assert body is not None
        assert "custom_filter_field" in body
        assert "update_time_ge" not in body  # must use configured name, not hardcoded


# ─── MOCK shop filter (2026-08-25: MOCK_SHOP_12345 leaked into production
#     oauth_tokens / tts_erp.shops, was triggering 1008 wasted TikTok calls/day) ─


def test_discover_shops_filters_out_mock_shops():
    """Test data like MOCK_SHOP_12345 leaked into oauth_tokens (via the
    hardcoded fallback in oauth_receiver_core.py:569) and gets picked up
    by cron. cron MUST skip them — every sync attempt fails with
    "Invalid shop_cipher" but still counts against TikTok rate limits,
    which is exactly how the 429 errors we kept seeing were triggered.
    """
    fake_rows = [
        {"shop_id": "MOCK_SHOP_12345", "provider": "tiktok"},
        {"shop_id": "7494763368967603447", "provider": "tiktok"},
        {"shop_id": "MOCK_FOO", "provider": "tiktok"},  # any MOCK_ prefix
    ]
    with patch.object(sync_cron, "oauth_receiver_core") as mock_oc:
        mock_oc.is_db_ok.return_value = True
        mock_oc.db_list_shops.return_value = fake_rows
        result = sync_cron.discover_shops(provider="tiktok")

    # Real shop only — MOCK_* filtered out
    assert result == ["7494763368967603447"]


def test_discover_shops_keeps_real_shops_when_no_mock_present():
    """Defensive: filter must be a no-op when only real shops exist."""
    fake_rows = [
        {"shop_id": "111", "provider": "tiktok"},
        {"shop_id": "222", "provider": "tiktok"},
    ]
    with patch.object(sync_cron, "oauth_receiver_core") as mock_oc:
        mock_oc.is_db_ok.return_value = True
        mock_oc.db_list_shops.return_value = fake_rows
        result = sync_cron.discover_shops(provider="tiktok")

    assert result == ["111", "222"]


def test_discover_shops_filters_only_mock_prefix_not_substring():
    """Filter must match prefix, not substring — a real shop like
    "MOCKERY_BAKERY_999" should not be excluded."""
    fake_rows = [
        {"shop_id": "MOCKERY_BAKERY_999", "provider": "tiktok"},
        {"shop_id": "MOCK_SHOP_12345", "provider": "tiktok"},
    ]
    with patch.object(sync_cron, "oauth_receiver_core") as mock_oc:
        mock_oc.is_db_ok.return_value = True
        mock_oc.db_list_shops.return_value = fake_rows
        result = sync_cron.discover_shops(provider="tiktok")

    # MOCK_SHOP_12345 has prefix MOCK_ → filtered
    # MOCKERY_BAKERY_999 has prefix MOCKERY_ → kept (not a MOCK_ sentinel)
    assert result == ["MOCKERY_BAKERY_999"]


# ─── W1.5: http_json timeout resilience + DB-down exit code ──────────


def test_http_json_catches_timeout():
    """Python 3.10+ urlopen timeout raises TimeoutError (OSError subclass),
    NOT URLError. It must be caught so one slow call doesn't kill the
    whole cron tick."""

    def raise_timeout(req, timeout=None):
        raise TimeoutError("timed out")

    with patch.object(sync_cron.urllib.request, "urlopen", raise_timeout):
        result = sync_cron.http_json("POST", "http://127.0.0.1:9877/x", {"a": 1})
    assert result["_error"] is True


def test_http_json_catches_socket_timeout():
    """socket.timeout (also an OSError) must be caught too."""

    def raise_sock_timeout(req, timeout=None):
        raise TimeoutError("timed out")

    with patch.object(sync_cron.urllib.request, "urlopen", raise_sock_timeout):
        result = sync_cron.http_json("GET", "http://127.0.0.1:9877/x")
    assert result["_error"] is True


def test_main_returns_nonzero_when_oauth_db_down(tmp_path, monkeypatch):
    """DB down must be distinguishable from 'no shops' via exit code.
    Monitoring watches exit codes; silent 0 = blind (2026-08-23 incident:
    34h of silent no-op runs)."""
    env_file = tmp_path / ".env"
    env_file.write_text("TTS_ERP_DB_URL=postgresql://x\nTTS_ERP_SERVICE_KEY=k\n")
    monkeypatch.setattr(sync_cron, "ENV_FILE", env_file)
    with patch.object(sync_cron, "oauth_receiver_core") as mock_oc:
        mock_oc.is_db_ok.return_value = False
        rc = sync_cron.main()
    assert rc != 0
