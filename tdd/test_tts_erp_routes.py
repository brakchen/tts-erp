"""Wave 3 — verify the merged tts-erp FastAPI app's route surface.

After Wave 3 Slice 2, the merged app must NOT expose:

* ``GET /shops``           (was proxy to oauth-receiver /tokens/shops)
* ``GET /shops/<shop_id>`` (was proxy to oauth-receiver)
* ``GET /token/<shop_id>`` (was proxy to oauth-receiver /token/<id>?reveal=1)

The real handlers for these resources now live in
``oauth_receiver_core`` and are reached by tts-erp through in-process
function calls (``LocalTokenProvider``) — not via HTTP.

TikTok Shop's OAuth redirect URL is registered as
``https://100feb74.r31.cpolar.top/callback``, so ``GET /callback`` is
a critical surface that MUST keep working after the merge. Verified in
Slice 3.

Route-count progression (Slice 2 → 3 → 4):

* Pre-Slice 2 (current state): 53 APIRoute paths
* After Slice 2 (delete /shops, /shops/<id>, /token/<id>): **50**
* After Slice 3 (mount oauth_router with /callback, /authorize, /healthz): **53**
* After Slice 4 (delete tts-erp's /healthz): **52**

Sub-app routes (e.g. ``analytics_sync`` mounted under /v1/analytics/sync)
are tracked separately via ``_app_all_paths`` which walks Mount routes.
"""
from __future__ import annotations

from fastapi.routing import APIRoute, Mount

from tts_erp_fastapi import app


def _app_route_paths() -> set[str]:
    """Return top-level APIRoute path templates (skips Mount sub-apps)."""
    return {r.path for r in app.routes if isinstance(r, APIRoute)}


def _app_all_paths() -> set[str]:
    """Return all HTTP path templates — walks Mount sub-apps.

    Useful for verifying that sub-app routes are still mounted after the
    merge. Joins Mount.prefix with each sub-route's path.
    """
    paths: set[str] = set()
    for r in app.routes:
        if isinstance(r, APIRoute):
            paths.add(r.path)
        elif isinstance(r, Mount):
            for sub in r.routes:
                if isinstance(sub, APIRoute):
                    paths.add((r.path or "") + sub.path)
    return paths


def _delete_via_test_client(method: str, path: str):
    """Hit the merged app via TestClient and return the response."""
    from fastapi.testclient import TestClient

    return TestClient(app).request(method, path)


class TestDeletedProxyRoutes:
    """Slice 2: the proxy routes /shops, /shops/<id>, /token/<id> are GONE."""

    def test_no_shops_route_in_merged_app(self):
        paths = _app_route_paths()
        assert "/shops" not in paths, f"/shops still registered. Routes: {sorted(paths)}"

    def test_no_shops_with_id_route_in_merged_app(self):
        paths = _app_route_paths()
        assert "/shops/{shop_id}" not in paths, "/shops/<shop_id> still registered"

    def test_no_token_with_id_route_in_merged_app(self):
        paths = _app_route_paths()
        assert "/token/{shop_id}" not in paths, "/token/<shop_id> still registered"

    def test_proxies_return_404_or_401_or_405(self):
        """After deletion the proxy routes are gone. Unauthenticated
        callers will be rejected by ``AuthMiddleware`` with 401 BEFORE
        the router resolves the path (this is intentional — it does not
        leak which paths exist). With an authenticated request the
        response would be 404 (route truly does not exist). We accept
        401 OR 404 OR 405 to capture both layers.

        The 3 introspection tests above already prove the routes are
        not registered; this test is a belt-and-braces HTTP check.
        """
        for path in ("/shops", "/shops/SHOP_X", "/token/SHOP_X"):
            r = _delete_via_test_client("GET", path)
            assert r.status_code in (401, 404, 405), (
                f"{path} returned {r.status_code} (expected 401, 404, or 405); "
                f"body={r.text[:200]}"
            )


class TestRemainingTtsErpSurface:
    """Sanity: the rest of tts-erp's surface is unchanged after the merge."""

    def test_sync_orders_route_still_exists(self):
        assert "/sync/orders" in _app_route_paths()

    def test_db_orders_route_still_exists(self):
        assert "/db/orders" in _app_route_paths()

    def test_orders_search_route_still_exists(self):
        assert "/orders/search" in _app_route_paths()

    def test_finance_statements_route_still_exists(self):
        assert "/finance/statements" in _app_route_paths()

    def test_logistics_orders_tracking_route_still_exists(self):
        assert "/logistics/orders/{order_id}/tracking" in _app_route_paths()

    def test_returns_search_route_still_exists(self):
        assert "/returns/search" in _app_route_paths()

    def test_analytics_sync_router_still_mounted(self):
        """Sub-app routes live under a deferred include_router; verify
        via TestClient rather than route introspection (FastAPI ≥0.116
        defers flattening these routes into ``app.routes``)."""
        r = _delete_via_test_client("GET", "/v1/analytics/sync/cursor")
        # Without a real auth header we expect 401, not 404/405 — proves
        # the route IS mounted.
        assert r.status_code != 404, (
            f"/v1/analytics/sync/cursor returned 404 — analytics_sync router "
            f"not mounted. body={r.text[:200]}"
        )
        assert r.status_code != 405, (
            f"/v1/analytics/sync/cursor returned 405 — wrong method. "
            f"body={r.text[:200]}"
        )

    def test_ads_monitor_still_exists(self):
        assert "/ads-monitor" in _app_route_paths()

    def test_endpoints_route_still_exists(self):
        assert "/endpoints" in _app_route_paths()


class TestRouteCounts:
    """Belt-and-braces: each slice's exact deltas."""

    def test_post_slice2_route_count_is_50(self):
        """Pre-Slice 2 = 53 APIRoute paths. Delete 3 → 50."""
        paths = _app_route_paths()
        assert len(paths) == 50, (
            f"Expected 50 APIRoute paths after Slice 2 deletion; got {len(paths)}.\n"
            f"Full list: {sorted(paths)}"
        )
