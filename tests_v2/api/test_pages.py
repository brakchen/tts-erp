"""Server-rendered page tests (Lane E v2/pages/manual-costs).

2026-08-31 (feature/procurement-ui): the page was redesigned as a thin
HTML shell that links ``/static/css/console.css`` + ``/static/js/console.js``
— all endpoint URLs and the workbench DOM now live in the static assets.
The detailed redesign assertions (tab labels, static refs, no token-paste
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
    assert "/static/css/console.css" in body
    assert "/static/js/console.js" in body
    # Token-paste UI must stay gone.
    assert "API token" not in body
    assert "mc_token" not in body


def test_self_hosted_fonts_exist_and_are_wired():
    """console.css declares @font-face for every woff2 under static/fonts.

    Regression guard (2026-08-31): the redesign shipped referencing IBM Plex /
    JetBrains Mono with NO font files and NO @font-face — everything fell
    back to system fonts and the page lost its entire typographic identity.
    """
    import re
    from pathlib import Path

    static = Path(__file__).resolve().parents[2] / "tts_erp_v2" / "static"
    css = (static / "css" / "console.css").read_text(encoding="utf-8")
    font_files = sorted(p.name for p in (static / "fonts").glob("*.woff2"))
    # All three design-token families must be present.
    assert any("plex-sans" in f for f in font_files), font_files
    assert any("plex-serif" in f for f in font_files), font_files
    assert any("jetbrains-mono" in f for f in font_files), font_files
    # Every woff2 on disk must be referenced by an @font-face src URL,
    # and every src URL must resolve to a real, non-trivial file.
    srcs = re.findall(r'url\("(/static/fonts/[^"]+\.woff2)"\)', css)
    assert srcs, "no @font-face src urls found in console.css"
    referenced = {s.rsplit("/", 1)[-1] for s in srcs}
    assert referenced == set(font_files), (referenced, font_files)
    for name in font_files:
        assert (static / "fonts" / name).stat().st_size > 10_000, name


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
