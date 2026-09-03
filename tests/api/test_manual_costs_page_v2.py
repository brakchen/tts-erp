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
    assert "/static/css/console.css" not in body, (
        "retired custom stylesheet still linked"
    )


def test_manual_costs_page_v2_has_two_operational_tabs(api_client, readonly_key):
    """Page must render two tabs with operational state labels.

    2026-09-01: the old 待填成本 and 待传图片 tabs were merged into one
    待处理 tab (both endpoints returned the same set: products with no
    manual cost and no effective link). Operators no longer need to
    click between two redundant tabs to enter cost then upload photo —
    each row carries both inputs and one submit.
    """
    r = api_client.get(
        "/v2/pages/manual-costs",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    body = r.text
    for label in ("待处理", "最近提交"):
        assert label in body, f"missing tab label: {label!r}"
    # The retired labels must be gone (regression guard).
    for retired in ("待填成本", "待传图片"):
        assert retired not in body, (
            f"retired tab label still rendered: {retired!r}"
        )


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
    """No raw hex values OUTSIDE the inline <style> block.

    Design doc §2.3 enumerates 7 named hex tokens. The inline <style>
    block is allowed to define them (token declarations are the
    legitimate source of hex), but element attributes, inline style="…"
    attributes, and other non-CSS sites must NOT carry one-off colors.
    Everything that needs to render a colour must reference a class
    that resolves through :root { --accent: … } etc.
    """
    import re

    r = api_client.get(
        "/v2/pages/manual-costs",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    body = r.text
    # Strip the inline <style>…</style> block before scanning — token
    # declarations live there, that's fine.
    stripped = re.sub(r"<style[\s\S]*?</style>", "", body)
    # Also strip the <link rel="stylesheet" href="…"> reference itself
    # (no hex inside but defensive).
    stripped = re.sub(r'<link\s+rel="stylesheet"[^>]*>', "", stripped)
    hex_colors = re.findall(r"#[0-9A-Fa-f]{6}\b", stripped)
    assert not hex_colors, (
        f"raw hex colors leaked into HTML outside <style>: {hex_colors}"
    )


def test_manual_costs_page_v2_signature_counter_present(api_client, readonly_key):
    """The signature oversized queue counter element must render.

    The .op-counter block (with id="op-counter", the .op-counter-num
    span, and the .op-counter-label "待处理") is the page's primary
    visual element. JS populates .op-counter-num with the pending
    total on first API response.
    """
    r = api_client.get(
        "/v2/pages/manual-costs",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    body = r.text
    assert 'id="op-counter"' in body, "signature counter section missing"
    assert 'id="op-counter-num"' in body, "signature counter num span missing"
    assert "op-counter-label" in body, "counter label class missing"
    assert "待处理" in body, "counter label text '待处理' missing"
    # Industrial-console fingerprints in the inline <style>
    assert "--paper:" in body, "paper token not declared"
    assert "--accent:" in body, "accent token not declared"
    assert "--mono:" in body, "monospace font stack not declared"
    # Border-radius zero is part of the aesthetic (no rounded corners)
    assert "border-radius: 0" in body, "expected flat (zero radius) design"


def test_console_js_uses_redesign_class_names():
    """console.js row HTML must use the op-* class hooks from the redesign.

    Regression guard so the next refactor doesn't regress the page to
    raw Bootstrap classes (which the CSS no longer styles).
    """
    from pathlib import Path

    js = (
        Path(__file__).resolve().parents[2]
        / "tts_erp_v2"
        / "static"
        / "js"
        / "console.js"
    )
    src = js.read_text(encoding="utf-8")
    for cls in (
        "op-td-sku",
        "op-td-cost",
        "op-input-cost",
        "op-select-currency",
        "op-dropzone",
        "op-btn-primary",
        "op-loading",
    ):
        assert cls in src, f"console.js missing class hook: {cls!r}"


def test_console_js_populates_signature_counter():
    """console.js loadPending must populate #op-counter-num on success.

    The signature counter is purely JS-driven — without the population
    step the page would render '·' forever.
    """
    from pathlib import Path

    js = (
        Path(__file__).resolve().parents[2]
        / "tts_erp_v2"
        / "static"
        / "js"
        / "console.js"
    )
    src = js.read_text(encoding="utf-8")
    assert '"#op-counter-num"' in src or "#op-counter-num" in src, (
        "console.js does not target #op-counter-num"
    )
    assert 'data-state' in src and '"ready"' in src, (
        "console.js does not flip counter data-state to ready on success"
    )


def test_manual_costs_page_v2_self_hosted_font_hint(api_client, readonly_key):
    """Page must not link a public CDN for fonts.

    Operators reach this service over a private NAT tunnel; CDN links
    leak operator IPs. We assert no fonts.googleapis.com or
    fonts.gstatic.com link in the HTML. The page actually uses
    Bootstrap 5.3.8's default font stack now (2026-08-31 — the custom
    IBM Plex typography was dropped with the rest of console.css).
    """
    r = api_client.get(
        "/v2/pages/manual-costs",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    body = r.text
    assert "fonts.googleapis.com" not in body, "Google Fonts CDN leak"
    assert "fonts.gstatic.com" not in body, "Google Fonts CDN leak"


def test_console_js_unwraps_api_envelope():
    """Regression guard for the '(items || []).filter is not a function' crash.

    2026-08-31: the backend rolled out an envelope
        { items: [...], total_missing_photo: N }
    on /v2/reporting/missing-cost-products. console.js must unwrap that
    envelope before iterating; otherwise loadNeedsPhoto throws on the
    Needs photo tab. Also protects loadNeedsCost + loadRecent against
    any future envelope roll-out on their endpoints.
    """
    from pathlib import Path

    js = (
        Path(__file__).resolve().parents[2]
        / "tts_erp_v2"
        / "static"
        / "js"
        / "console.js"
    )
    src = js.read_text(encoding="utf-8")
    assert "function unwrap(payload)" in src, "unwrap helper missing from console.js"
    # Both load functions (pending + recent) must pipe their payload
    # through unwrap(). Was ≥3 when there were three tabs.
    assert src.count("unwrap(payload)") >= 2, (
        f"unwrap(payload) called {src.count('unwrap(payload)')} times, "
        "expected ≥ 2 (pending + recent tabs)"
    )
    # Spot-check: filter() must not be called on a payload that wasn't
    # unwrapped (the original bug pattern).
    assert ".filter((it)" not in src or "unwrap(payload).filter" in src, (
        ".filter called on a payload without unwrap — likely the 2026-08-31 bug"
    )
