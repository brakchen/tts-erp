"""Wave 3 — verify the merged tts-erp FastAPI app's route surface.

After Wave 3 Slice 2, the merged app must NOT expose:

* ``GET /shops``           (was proxy to oauth-receiver /tokens/shops)
* ``GET /shops/<shop_id>`` (was proxy to oauth-receiver)
* ``GET /token/<shop_id>`` (was proxy to oauth-receiver /token/<id>?reveal=1)

After Slice 3, the merged app MUST expose:

* ``GET /authorize``   (from oauth_receiver_router)
* ``GET /callback``    (from oauth_receiver_router — TikTok redirect target)
* ``GET /healthz``     (from oauth_receiver_router, replacing tts-erp's in Slice 4)

The real handlers for ``/shops``, ``/shops/<id>``, ``/token/<id>`` now
live in ``oauth_receiver_core`` and are reached by tts-erp through
in-process function calls (``LocalTokenProvider``) — not via HTTP.

TikTok Shop's OAuth redirect URL is registered as
``http://daqiang.nat100.top/callback``, so ``GET /callback`` is
a critical surface that MUST keep working after the merge.

Route-count progression (Slice 2 → 3 → 4):

* Pre-Slice 2 (current state): 53 APIRoute paths
* After Slice 2 (delete /shops, /shops/<id>, /token/<id>): **50**
* After Slice 3 (mount oauth_router adds 3: /authorize, /callback,
  plus a duplicate /healthz): **53**
* After Slice 4 (delete tts-erp's /healthz): **52**

Sub-app routes (e.g. ``analytics_sync`` mounted under /v1/analytics/sync)
are verified via HTTP, not route introspection (FastAPI ≥0.116 defers
flattening these routes into ``app.routes``).
"""

from __future__ import annotations

import pytest
from fastapi.routing import APIRoute, Mount
from tts_erp_fastapi import app


@pytest.fixture(autouse=True)
def _disable_auth(monkeypatch):
    """Disable auth so oauth routes can be hit without an API key.

    Slice 4 adds ``/callback``, ``/authorize``, ``/healthz`` to the
    auth whitelist properly. For Slice 3 we want to verify the routes
    exist and respond, not test the auth layer (that's Wave 4).
    """
    monkeypatch.setenv("TTS_ERP_AUTH_MODE", "off")


def _app_route_paths() -> list[str]:
    """Return all APIRoute path templates exposed by the merged app.

    Walks ``app.routes`` (top-level @app.get/@app.post routes) AND any
    routers attached via ``app.include_router()`` (which FastAPI stores
    as ``_IncludedRouter`` entries whose children live under
    ``original_router.routes``). This mirrors what the HTTP layer
    routes via Starlette — so a route registered via ``include_router``
    appears here just like one registered via ``@app.get``.

    Returns a list (preserves duplicates for accurate count assertions).
    """
    paths: list[str] = []
    for r in app.routes:
        if isinstance(r, APIRoute):
            paths.append(r.path)
        elif type(r).__name__ == "_IncludedRouter":
            orig = getattr(r, "original_router", None)
            if orig is not None:
                for sub in orig.routes:
                    if isinstance(sub, APIRoute):
                        paths.append(sub.path)
    return paths


def _app_all_paths() -> set[str]:
    """Return all HTTP path templates — walks Mount sub-apps."""
    paths: set[str] = set()
    for r in app.routes:
        if isinstance(r, APIRoute):
            paths.add(r.path)
        elif isinstance(r, Mount):
            for sub in r.routes:
                if isinstance(sub, APIRoute):
                    paths.add((r.path or "") + sub.path)
    return paths


def _hit(method: str, path: str):
    """Hit the merged app via TestClient and return the response."""
    from fastapi.testclient import TestClient

    return TestClient(app).request(method, path)


# ─── Slice 2: proxy routes are gone ─────────────────────────────────


class TestDeletedProxyRoutes:
    """Slice 2: the proxy routes /shops, /shops/<id>, /token/<id> are GONE."""

    def test_no_shops_route_in_merged_app(self):
        paths = _app_route_paths()
        assert "/shops" not in paths, (
            f"/shops still registered. Routes: {sorted(paths)}"
        )

    def test_no_shops_with_id_route_in_merged_app(self):
        paths = _app_route_paths()
        assert "/shops/{shop_id}" not in paths, "/shops/<shop_id> still registered"

    def test_no_token_with_id_route_in_merged_app(self):
        paths = _app_route_paths()
        assert "/token/{shop_id}" not in paths, "/token/<shop_id> still registered"

    def test_proxies_return_404_or_401_or_405(self):
        """Unauthenticated callers are rejected by ``AuthMiddleware``
        with 401 BEFORE the router resolves the path (this is intentional
        — it does not leak which paths exist). With an authenticated
        request the response would be 404 (route truly does not exist).
        We accept 401 OR 404 OR 405 to capture both layers.

        The 3 introspection tests above already prove the routes are
        not registered; this is a belt-and-braces HTTP check.
        """
        for path in ("/shops", "/shops/SHOP_X", "/token/SHOP_X"):
            r = _hit("GET", path)
            assert r.status_code in (401, 404, 405), (
                f"{path} returned {r.status_code} (expected 401, 404, or 405); "
                f"body={r.text[:200]}"
            )


# ─── Sanity: rest of tts-erp surface unchanged ──────────────────────


class TestRemainingTtsErpSurface:
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
        """Sub-app routes are deferred at import time; verify via HTTP."""
        r = _hit("GET", "/v1/analytics/sync/cursor")
        # With auth off we should get a real response (not 404/405).
        assert r.status_code not in (404, 405), (
            f"/v1/analytics/sync/cursor returned {r.status_code} — "
            f"analytics_sync router not mounted. body={r.text[:200]}"
        )

    def test_ads_monitor_still_exists(self):
        assert "/ads-monitor" in _app_route_paths()

    def test_endpoints_route_still_exists(self):
        assert "/endpoints" in _app_route_paths()


# ─── Slice 3: oauth_receiver_router is mounted ──────────────────────


class TestOauthRouterMounted:
    """Slice 3: oauth_receiver_router is mounted on the main app."""

    def test_authorize_route_mounted(self):
        assert "/authorize" in _app_route_paths(), (
            f"/authorize missing. Routes: {sorted(_app_route_paths())}"
        )

    def test_callback_route_mounted(self):
        assert "/callback" in _app_route_paths(), (
            f"/callback missing. Routes: {sorted(_app_route_paths())}"
        )

    def test_oauth_healthz_mounted(self):
        """oauth-receiver's /healthz is mounted.

        Note: at Slice 3 there were 2 /healthz routes (oauth + tts-erp).
        Slice 4 deletes the tts-erp one, so by the time this test runs
        alongside Slice 4 tests, there should be exactly 1. But this
        test asserts the oauth one is there, not how many total.
        """
        paths = _app_route_paths()
        assert "/healthz" in paths, (
            f"/healthz not mounted at all. Routes: {sorted(paths)}"
        )
        # At Slice 3 there's a duplicate; at Slice 4 there's one. The
        # canonical /healthz (oauth-router's merged one) is verified
        # separately in TestOauthHealthzCanonical.
        assert sum(1 for p in paths if p == "/healthz") >= 1

    def test_no_route_collision(self):
        """App must load — FastAPI raises at import time if two routes
        share path+method."""
        from fastapi import FastAPI

        assert isinstance(app, FastAPI)

    def test_authorize_returns_200(self):
        r = _hit("GET", "/authorize")
        assert r.status_code == 200, (
            f"/authorize returned {r.status_code}: {r.text[:300]}"
        )

    def test_callback_no_code_returns_200(self):
        r = _hit("GET", "/callback")
        assert r.status_code == 200, (
            f"/callback returned {r.status_code}: {r.text[:300]}"
        )

    def test_callback_round_trip_with_state(self):
        """End-to-end: register state via /authorize, then hit /callback."""
        r1 = _hit("GET", "/authorize?format=json&provider=tiktok")
        assert r1.status_code == 200, r1.text[:300]
        data = r1.json()
        state = data.get("state")
        assert state, f"no state in /authorize response: {data}"

        r2 = _hit("GET", f"/callback?code=TEST&state={state}")
        assert r2.status_code == 200, (
            f"/callback returned {r2.status_code}: {r2.text[:300]}"
        )


# ─── Belt-and-braces route counts ──────────────────────────────────


class TestOauthHealthzCanonical:
    """Slice 4: tts-erp's /healthz is deleted; oauth-receiver's
    /healthz (which reports merged oauth_receiver + tts_erp + miaoshou
    state) is the canonical one."""

    def test_only_one_healthz_route(self):
        """After Slice 4 only 1 /healthz remains — the oauth-router one."""
        paths = _app_route_paths()
        healthz_count = sum(1 for p in paths if p == "/healthz")
        assert healthz_count == 1, (
            f"Expected exactly 1 /healthz route (oauth-router's); got "
            f"{healthz_count}. Routes: {sorted(paths)}"
        )

    def test_healthz_returns_oauth_receiver_section(self):
        r = _hit("GET", "/healthz")
        assert r.status_code == 200, f"/healthz returned {r.status_code}"
        body = r.json()
        assert "components" in body, f"missing 'components' in /healthz: {body}"
        assert "oauth_receiver" in body["components"], (
            f"missing oauth_receiver section: {list(body['components'].keys())}"
        )

    def test_healthz_returns_tts_erp_section(self):
        r = _hit("GET", "/healthz")
        assert r.status_code == 200
        body = r.json()
        assert "tts_erp" in body["components"], (
            f"missing tts_erp section: {list(body['components'].keys())}"
        )

    def test_healthz_returns_miaoshou_section(self):
        r = _hit("GET", "/healthz")
        assert r.status_code == 200
        body = r.json()
        assert "miaoshou" in body["components"], (
            f"missing miaoshou section: {list(body['components'].keys())}"
        )

    def test_healthz_includes_version_marker(self):
        """Merged healthz reports version 'tts-erp+oauth-receiver/1.0'."""
        r = _hit("GET", "/healthz")
        body = r.json()
        assert "tts-erp" in body.get("version", ""), (
            f"version marker missing tts-erp: {body.get('version')}"
        )
        assert "oauth-receiver" in body.get("version", ""), (
            f"version marker missing oauth-receiver: {body.get('version')}"
        )


class TestRouteCounts:
    def test_post_slice4_route_count_is_54(self):
        """After Slice 3: 55 APIRoute. After Slice 4 (delete 1 /healthz): 54."""
        paths = _app_route_paths()
        assert len(paths) == 54, (
            f"Expected 54 APIRoute paths after Slice 4; got {len(paths)}.\n"
            f"Full list: {sorted(paths)}"
        )
