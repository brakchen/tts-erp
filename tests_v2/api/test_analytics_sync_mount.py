"""TDD tests: analytics_sync router is mounted under tts-erp v2 app.

The Chrome extension (tk-adv-cost-monitor) calls
``GET /v1/analytics/sync/cursor`` against the public domain. Previously
this required nginx to reverse-proxy to standalone port 9878 and the
syncBaseUrl was implicitly the public host. Now that the data sync
lives under tts-erp v2 (per the "统一在tts-erp管理" refactor decision),
the same URL must reach the analytics_sync router through the v2 app.

These tests guard three contracts:
1. ``tts_erp_v2.app:build_app()`` exposes ``/v1/analytics/sync/cursor``
   (route exists; not 404).
2. The v2 AuthMiddleware classifies the path as ``readwrite`` (so a
   readonly key gets 403, not silently 200).
3. The path is NOT exempt from auth (anonymous is 401).

If any of these break, the production sync goes silent — same symptom
as the 14:59:29~15:00:25 UTC outage (54 cursor calls returning 404
because nginx had no location block).
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]


def test_cursor_route_present_in_v2_app(api_client):
    """``/v1/analytics/sync/cursor`` must be a registered route on v2.

    Without an Authorization header, the request should hit the auth
    middleware and return 401 — NOT 404 (which would mean the router
    isn't mounted). The body must come from tts-erp's auth middleware
    (the ``or X-API-Key`` phrasing), proving the request reached the
    v2 app and not standalone analytics_sync.
    """
    r = api_client.get(
        "/v1/analytics/sync/cursor",
        params={
            "sellerId": "7494763368967603447",
            "advertiserId": "7661087232599212040",
        },
    )
    assert r.status_code == 401, r.text
    assert "X-API-Key" in r.text, (
        "expected v2 auth error phrasing ('...or X-API-Key: <key>'); "
        "got a non-v2 response — analytics_sync router is mounted "
        "behind standalone SyncAuthMiddleware, not v2 AuthMiddleware"
    )


def test_cursor_route_readonly_key_is_forbidden(api_client, readonly_key):
    """A readonly key gets 403 (v2 classifies /v1/analytics/sync/* as readwrite)."""
    r = api_client.get(
        "/v1/analytics/sync/cursor",
        params={
            "sellerId": "7494763368967603447",
            "advertiserId": "7661087232599212040",
        },
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 403, r.text


def test_cursor_route_readwrite_key_passes_auth(api_client, readwrite_key):
    """A readwrite key passes auth and reaches the handler.

    We don't assert on the body — the handler hits PG and depends on
    row state. The contract here is: auth passed (not 401/403) and
    the response came from the analytics_sync handler (JSON body with
    the analytics_sync envelope, NOT the v2 router 404 envelope).
    """
    r = api_client.get(
        "/v1/analytics/sync/cursor",
        params={
            "sellerId": "7494763368967603447",
            "advertiserId": "7661087232599212040",
        },
        headers={"Authorization": f"Bearer {readwrite_key}"},
    )
    # Handler runs: returns either 200 (data envelope) or some other
    # status if PG fails. The point is "not 401/403/404".
    assert r.status_code not in (401, 403, 404), r.text


def test_batches_route_present_in_v2_app(api_client, readwrite_key):
    """The /batches POST endpoint is also routed.

    Even an empty/invalid body should reach the handler, not the v2
    404 catch-all. The analytics_sync handler returns a structured
    JSON error envelope (not the v2 ``{"detail": "Not Found"}``).
    """
    r = api_client.post(
        "/v1/analytics/sync/batches",
        json={"protocolVersion": 1, "scope": {}, "records": []},
        headers={"Authorization": f"Bearer {readwrite_key}"},
    )
    # Either: 400 (validation failed at handler) or 200 (handler ran).
    # What we forbid: 401 (auth issue), 403 (role issue), 404 (route
    # missing).
    assert r.status_code not in (401, 403, 404), r.text


def test_cursor_items_include_scope_fields(api_client, readwrite_key):
    """Cursor items must echo sellerId/advertiserId for each row.

    The Chrome extension's parseCursor strictly matches items by
    ``sellerId``/``advertiserId`` (per the protocol contract the client
    implemented). Server items without those fields make every cursor
    parse to null → clients re-seed the full lookback window forever.
    Regression guard for the 2026-08-30 protocol mismatch.
    """
    from analytics_sync.pg_repositories import connect

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO analytics_cursors (
                seller_id, advertiser_id, storage_key, campaign_id,
                latest_completed_day, first_seen_day
            ) VALUES ('TEST_seller-scope', 'TEST_adv-scope',
                      'productAnalyses', 'TEST_campaign-scope',
                      '2026-08-28', '2026-08-27')
            ON CONFLICT (seller_id, advertiser_id, storage_key, campaign_id)
            DO UPDATE SET latest_completed_day = EXCLUDED.latest_completed_day
            """
        )
        conn.commit()
    try:
        r = api_client.get(
            "/v1/analytics/sync/cursor",
            params={
                "sellerId": "TEST_seller-scope",
                "advertiserId": "TEST_adv-scope",
                "storageKey": "productAnalyses",
                "campaignId": "TEST_campaign-scope",
            },
            headers={"Authorization": f"Bearer {readwrite_key}"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["code"] == 0
        assert body["requestId"]
        assert body["data"]["nextCursor"] is None
        items = body["data"]["items"]
        assert len(items) == 1, items
        item = items[0]
        assert item["sellerId"] == "TEST_seller-scope"
        assert item["advertiserId"] == "TEST_adv-scope"
        assert item["storageKey"] == "productAnalyses"
        assert item["campaignId"] == "TEST_campaign-scope"
        assert item["latestCompletedDay"] == "2026-08-28"
        assert item["nextRequiredDay"] == "2026-08-29"
    finally:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM analytics_cursors WHERE seller_id = 'TEST_seller-scope'"
            )
            cur.execute(
                "DELETE FROM analytics_shop_timezones WHERE seller_id = 'TEST_seller-scope'"
            )
            conn.commit()


@pytest.mark.parametrize(
    "readonly_path",
    [
        "/v2/commerce/sales-orders",
        "/v2/linkage/product-links",
        "/v2/reporting/coverage",
    ],
)
def test_other_routes_still_work(api_client, readonly_key, readonly_path):
    """Regression guard: mounting analytics_sync doesn't break v2 routes."""
    r = api_client.get(
        readonly_path,
        params={"shop_id": "7494763368967603447"}
        if "commerce" in readonly_path
        else {},
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    # Either 200 (data) or 400 (handler-level validation) is fine.
    # What we forbid: 401/403/404 (auth/route regression).
    assert r.status_code not in (401, 403, 404), (
        f"{readonly_path} regressed after analytics_sync mount: {r.status_code} {r.text}"
    )
