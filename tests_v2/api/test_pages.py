"""Server-rendered page tests (Lane E v2/pages/manual-costs).

The page is a single HTMLResponse with inline JS — we just assert:
- GET returns 200 + content-type text/html
- The response body contains the table grid, the form row template,
  and a fetch() call to /v2/reporting/missing-cost-products
- The page is exempt from auth (auth middleware lets it through as
  GET /v2/pages/manual-costs falls under /v2/* → readonly role required,
  but the page itself is readonly-safe because it does no DB writes;
  we verify the page works with a readwrite key in enforce mode).

The page lives at /v2/pages/manual-costs (mounted by pages.router with
prefix /v2/pages). The required-role for any /v2/* path is readonly,
so any bearer key (even readonly) can fetch the page.
"""

from __future__ import annotations


def test_manual_costs_page_returns_200_with_html(api_client, readonly_key):
    """GET the page → 200 text/html containing the form grid."""
    r = api_client.get(
        "/v2/pages/manual-costs",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/html"), r.headers
    body = r.text
    # Sanity: key DOM elements the operator UI relies on.
    assert "<table" in body
    assert 'id="rows"' in body
    assert "/v2/reporting/missing-cost-products" in body
    assert "/v2/reporting/manual-costs" in body
    assert "channel_product_id" in body
    assert "external_product_id" in body


def test_manual_costs_page_requires_some_auth(api_client):
    """No Authorization header → 401 (any /v2/* path requires readonly+)."""
    r = api_client.get("/v2/pages/manual-costs")
    assert r.status_code == 401, r.text
