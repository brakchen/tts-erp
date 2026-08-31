"""Server-rendered page tests (Lane E v2/pages/manual-costs).

2026-08-31 (feature/procurement-ui): the page was redesigned as a thin
HTML shell that links static assets — Bootstrap 5.3.8 (vendored at
``/static/vendor/bootstrap.min.css``) + ``/static/js/console.js``.
The detailed shell assertions (tab labels, static refs, no token-paste
block) live in ``tests_v2/api/test_manual_costs_page_v2.py``.

This file keeps the two load-bearing contract checks:
- GET returns 200 + text/html and links the static assets
- No Authorization header → 401 (any /v2/* path requires readonly+)
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]


def test_manual_costs_page_returns_200_with_html(api_client, readonly_key):
    """GET the page → 200 text/html shell linking the static assets."""
    r = api_client.get(
        "/v2/pages/manual-costs",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/html"), r.headers
    body = r.text
    # The page is a shell; endpoint URLs and the grid DOM live in
    # /static/js/console.js (see test_manual_costs_page_v2.py).
    # Asset paths are RELATIVE so the page works both on :9877 directly
    # and behind the NGINX /tts prefix (2026-08-31: absolute /static/...
    # links 404'd publicly — the unstyled page looked broken).
    assert "../../static/vendor/bootstrap.min.css" in body
    assert "../../static/js/console.js" in body
    assert 'href="/static/' not in body
    assert 'src="/static/' not in body
    # Token-paste UI must stay gone.
    assert "API token" not in body
    assert "mc_token" not in body


def test_manual_costs_page_requires_some_auth(api_client):
    """No Authorization header → 401 (any /v2/* path requires readonly+)."""
    r = api_client.get("/v2/pages/manual-costs")
    assert r.status_code == 401, r.text


def test_endpoints_index_lists_included_router_routes(api_client):
    """/endpoints must expand FastAPI ≥0.141 lazy _IncludedRouter wrappers.

    Regression guard for the 2026-08-31 finding: FastAPI 0.141 makes
    include_router lazy, so a naive ``app.routes`` iteration sees only the
    eagerly-added public routes. The operator index must still list every
    v2 route (prod restart with the new FastAPI would otherwise degrade
    /endpoints to 6 entries).
    """
    r = api_client.get("/endpoints")
    assert r.status_code == 200, r.text
    paths = {e["path"] for e in r.json()["endpoints"]}
    # Representative routes from every included router.
    assert "/v2/pages/manual-costs" in paths
    assert "/v2/reporting/manual-costs" in paths
    assert "/v2/commerce/channel-accounts" in paths
    assert "/v2/spu-images/upload-url" in paths
    assert "/v2/spu-images/{image_id}/confirm" in paths
    assert "/v2/auth/login" in paths
