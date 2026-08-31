"""Manual-costs page v2 redesign — frontend HTML shell contract tests.

Scope: assert the redesigned page renders the three operational-state tabs,
references external static assets, and removes the legacy token-paste block.
We only test the HTML shell — runtime JS behaviour (fetch/upload) lives in
the browser and is covered by manual smoke tests, not FastAPI TestClient.

See ``tech-doc/procurement-ui-redesign.md`` §2 (design tokens) and §6
(frontend contracts) for the full design rationale.
"""

from __future__ import annotations


def test_manual_costs_page_v2_returns_200_html(api_client, readonly_key):
    """GET the redesigned page → 200 text/html."""
    r = api_client.get(
        "/v2/pages/manual-costs",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/html"), r.headers


def test_manual_costs_page_v2_references_static_assets(api_client, readonly_key):
    """Page HTML must link Bootstrap (vendored) + console.js, prefix-relative.

    Styling is Bootstrap 5.3.8 self-hosted at /static/vendor/ (2026-08-31:
    the custom console.css design system was dropped per user decision).
    All asset paths must be RELATIVE (../../static/...) so the page works
    behind the NGINX /tts prefix as well as on :9877 directly.
    """
    r = api_client.get(
        "/v2/pages/manual-costs",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    body = r.text
    assert "../../static/vendor/bootstrap.min.css" in body, "missing Bootstrap CSS link"
    assert "../../static/js/console.js" in body, "missing JS link"
    assert "/static/css/console.css" not in body, "retired custom stylesheet still linked"


def test_manual_costs_page_v2_has_three_operational_tabs(api_client, readonly_key):
    """Page must render three tabs with operational state labels.

    Per design doc §2.4 — tabs are operational states (Needs cost /
    Needs photo / Recently filed), NOT numeric indices. The filter
    toolbar (search / page size) lives below the tabs.
    """
    r = api_client.get(
        "/v2/pages/manual-costs",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    body = r.text
    for label in ("Needs cost", "Needs photo", "Recently filed"):
        assert label in body, f"missing tab label: {label!r}"


def test_manual_costs_page_v2_has_shop_switcher(api_client, readonly_key):
    """Header must expose a shop switcher dropdown (id="shop-switcher")."""
    r = api_client.get(
        "/v2/pages/manual-costs",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    body = r.text
    assert 'id="shop-switcher"' in body, "missing shop switcher control"
    assert 'name="channel_account_id"' in body, "shop switcher missing name attr"


def test_manual_costs_page_v2_drops_token_paste_block(api_client, readonly_key):
    """The legacy <details>API token…</details> block must be gone.

    The page now relies on /v2/auth/login (session cookie) — token paste
    is dead. We assert by the absence of the unique identifier text.
    """
    r = api_client.get(
        "/v2/pages/manual-costs",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    body = r.text
    assert "API token (paste once; stored in localStorage)" not in body, (
        "legacy token paste UI still present"
    )
    assert "mc_token" not in body, "legacy localStorage key still referenced"


def test_manual_costs_page_v2_asset_paths_prefix_safe(api_client, readonly_key):
    """No root-absolute asset hrefs/srcs — regression guard for the 404.

    2026-08-31: absolute /static/... links 404'd behind the NGINX /tts
    prefix (daqiang.nat100.top/static/... has no route), leaving the page
    completely unstyled in production. The page is served at
    /v2/pages/manual-costs, so ../../static/ resolves to the deployment
    root under any prefix.
    """
    r = api_client.get(
        "/v2/pages/manual-costs",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    body = r.text
    assert 'href="/' not in body, "root-absolute href found"
    assert 'src="/' not in body, "root-absolute src found"


def test_manual_costs_page_v2_no_inline_event_handlers(api_client, readonly_key):
    """No onclick / onsubmit / onchange inline handlers.

    Per the task brief (no inline event handlers — use addEventListener).
    """
    r = api_client.get(
        "/v2/pages/manual-costs",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    body = r.text
    for forbidden in ("onclick=", "onsubmit=", "onchange="):
        assert forbidden not in body, f"inline handler found: {forbidden}"


def test_manual_costs_page_v2_uses_design_token_variables(api_client, readonly_key):
    """No raw hex values in the page HTML — colors must come from CSS tokens.

    Design doc §2.3 enumerates 7 named hex tokens. The HTML shell may
    not introduce one-off colors; everything renders via class hooks
    that the CSS file resolves. We grep for stray `#` hex literals in
    the HTML body (excluding the /static/ asset URLs).
    """
    r = api_client.get(
        "/v2/pages/manual-costs",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    body = r.text
    # Cheap but effective: the page itself must not carry a color hex.
    # CSS asset contents are not loaded here so we can't grep them.
    import re
    hex_colors = re.findall(r"#[0-9A-Fa-f]{6}\b", body)
    assert not hex_colors, f"raw hex colors leaked into HTML: {hex_colors}"


def test_manual_costs_page_v2_self_hosted_font_hint(api_client, readonly_key):
    """Page hint about font hosting: the CSS file is local, not a CDN <link>.

    The design doc self-correction (latest revision) moved off Google
    Fonts CDN to local /static/fonts/. We assert no fonts.googleapis.com
    or fonts.gstatic.com CDN link in the HTML — operators run on a
    private LAN, the CDN leak is unwanted.
    """
    r = api_client.get(
        "/v2/pages/manual-costs",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    body = r.text
    assert "fonts.googleapis.com" not in body, "Google Fonts CDN leak"
    assert "fonts.gstatic.com" not in body, "Google Fonts CDN leak"
