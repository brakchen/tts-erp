"""Tests for the v2 GET /endpoints operator index.

The /endpoints endpoint lists every route the v2 app exposes so an
operator can quickly audit the surface area. It walks ``app.routes``
and emits a path + methods pair for each entry.

Historical bug (2026-08-30): FastAPI 0.141 introduces
``_IncludedRouter`` — a lazy wrapper around child routers that
``app.include_router`` registers. ``_IncludedRouter`` has no ``path``
attribute, so the original ``for r in app.routes: if not path: continue``
loop silently drops every route registered via ``include_router``. The
v2 app uses ``include_router`` for **every** router (commerce, linkage,
reporting, pages, llm_context, auth, analytics_sync), so /endpoints
returned only the FastAPI meta-routes (/docs, /openapi.json, /healthz,
/endpoints itself) and reported ``count: 6``.

The fix: walk recursively into ``_IncludedRouter.original_router.routes``
so mounted-router routes show up.

These tests guard the recursion contract end-to-end. If you add a new
``include_router`` to ``tts_erp_v2.app:build_app``, /endpoints must list
its paths without any further code change.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]


def _path_set(payload: dict) -> set[str]:
    """Collect the ``path`` field of every entry in /endpoints' items list."""
    return {entry["path"] for entry in payload["endpoints"]}


def test_endpoints_returns_200(api_client):
    r = api_client.get("/endpoints")
    assert r.status_code == 200, r.text


def test_endpoints_lists_v2_business_routes(api_client):
    """The v2 routers (commerce / linkage / reporting / pages / llm / auth) must appear."""
    r = api_client.get("/endpoints")
    payload = r.json()
    paths = _path_set(payload)
    # commerce
    assert "/v2/commerce/sales-orders" in paths
    assert "/v2/commerce/channel-accounts" in paths
    # linkage
    assert "/v2/linkage/product-links" in paths
    assert "/v2/linkage/overrides" in paths
    # reporting
    assert "/v2/reporting/cost-snapshots" in paths
    assert "/v2/reporting/profit-daily" in paths
    assert "/v2/reporting/coverage" in paths
    # pages
    assert "/v2/pages/manual-costs" in paths
    # llm
    assert "/v2/llm-context" in paths
    # auth (browser login)
    assert "/v2/auth/login" in paths
    assert "/v2/auth/me" in paths


def test_endpoints_lists_analytics_sync_routes(api_client):
    """analytics router is mounted under /v2/analytics/sync — must appear.

    Regression guard for the original bug: analytics routes lived
    inside an ``_IncludedRouter`` and were dropped by the original flat
    walk. This test fails (and the bug is back) if anyone reverts to a
    non-recursive walk without the recursion fix.

    2026-09-02 v2 化：路径从 /v1/analytics/sync/* 迁到 /v2/analytics/sync/*。
    """
    r = api_client.get("/endpoints")
    payload = r.json()
    paths = _path_set(payload)
    assert "/v2/analytics/sync/cursor" in paths, (
        f"/v2/analytics/sync/cursor missing from /endpoints; got paths={sorted(paths)}"
    )
    assert "/v2/analytics/sync/dumps" in paths


def test_endpoints_lists_path_param_routes(api_client):
    """Routes with ``{param}`` placeholders must surface too.

    FastAPI stores the path_format (``/v2/commerce/sales-orders/{order_id}``)
    on APIRoute. The recursion walker must read it, not skip it.
    """
    r = api_client.get("/endpoints")
    payload = r.json()
    paths = _path_set(payload)
    assert "/v2/commerce/sales-orders/{order_id}" in paths
    assert "/v2/linkage/issues/{issue_id}/resolve" in paths


def test_endpoints_count_matches_recursive_total(api_client):
    """``count`` must equal ``len(endpoints)``.

    Guards against the lazy-resolution mismatch where FastAPI reports
    fewer routes in ``app.routes`` than what is actually routable after
    lifespan startup. ``/endpoints`` is meant to be the source of truth
    for the operator, so internal:external consistency is mandatory.
    """
    r = api_client.get("/endpoints")
    payload = r.json()
    assert payload["count"] == len(payload["endpoints"])
    # And it must be more than just the FastAPI meta-routes — every
    # include_router() in build_app() must have contributed.
    assert payload["count"] > 6, (
        "count looks like only FastAPI meta-routes; the recursion fix "
        "is probably not active"
    )


def test_endpoints_excludes_meta_only_routes(api_client):
    """/docs, /redoc, /openapi.json are documented separately; OK either way.

    Currently /endpoints lists them — that's fine (they have path + methods).
    The point of THIS test is to assert the recursion doesn't accidentally
    surface ASGI internals like the lifespan Mount or the CORS preflight
    sentinel.
    """
    r = api_client.get("/endpoints")
    payload = r.json()
    paths = _path_set(payload)
    # No path-less entries should appear (no empty strings, no None)
    assert "" not in paths
    assert all(isinstance(p, str) and p.startswith("/") for p in paths), (
        f"unexpected non-path entries: {[p for p in paths if not p.startswith('/')]}"
    )
