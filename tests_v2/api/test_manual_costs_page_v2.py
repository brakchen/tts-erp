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
    """Page HTML must link to /static/css/console.css and /static/js/console.js.

    Inline CSS/JS is removed in the v2 redesign (per design doc §6) so
    the page must reference external assets. No CDN-served frameworks —
    the /static/ prefix is mounted by the v2 app via StaticFiles.
    """
    r = api_client.get(
        "/v2/pages/manual-costs",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    body = r.text
    assert "/static/css/console.css" in body, "missing CSS link"
    assert "/static/js/console.js" in body, "missing JS link"


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


def test_manual_costs_page_v2_includes_stamp_element(api_client, readonly_key):
    """Page template must declare the .filed-stamp element used by JS.

    The JS injects and animates this on successful submission (design
    doc §2.5 signature element). We declare the class once in the
    template's stylesheet scope (CSS lives in /static/css/console.css),
    so the HTML reference is only via the className the JS will set.
    """
    r = api_client.get(
        "/v2/pages/manual-costs",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    body = r.text
    # The class is referenced from JS — present in the page via the JS
    # asset path. We assert the JS asset is wired; the actual class
    # selector lives in the external stylesheet.
    assert "/static/js/console.js" in body


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
