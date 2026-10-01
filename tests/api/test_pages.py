"""Server-rendered page tests (Lane E v2/pages/manual-costs).

2026-08-31 (feature/procurement-ui): the page was redesigned as a thin
HTML shell that links static assets — Bootstrap 5.3.8 (vendored at
``/static/vendor/bootstrap.min.css``) + ``/static/js/console.js``.
The detailed shell assertions (tab labels, static refs, no token-paste
block) live in ``tests/api/test_manual_costs_page_v2.py``.

This file keeps the two load-bearing contract checks:
- GET returns 200 + text/html and links the static assets
- No Authorization header → 401 (any /v2/* path requires readonly+)
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tts_erp_v2.api.v2.pages import (
    _SIDEBAR_CSS,
    _SIDEBAR_TOGGLE_JS,
    _sidebar_html,
    dashboard_page,
    enum_map_page,
    runtime_configs_page,
    sync_jobs_page,
)

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]


def test_sidebar_markup_connects_shared_styles_and_accessible_controls():
    """Sidebar markup must activate its CSS and expose truthful control state."""
    body = _sidebar_html("dashboard")

    assert re.search(r'<nav class="[^"]*\bsidebar\b[^"]*"[^>]+id="sidebar"', body)
    assert 'aria-label="主导航"' in body
    assert 'aria-controls="sidebar"' in body
    assert 'aria-expanded="false"' in body
    assert 'aria-current="page"' in body


def test_sidebar_groups_match_operator_workflow():
    """Sidebar grouping follows the operator-facing information architecture."""
    body = _sidebar_html("focused-spus")

    assert body.index("总览</div>") < body.index("控制台")
    assert body.index("控制台") < body.index("经营分析</div>")
    assert body.index("经营分析</div>") < body.index("重点关注 SPU")
    assert body.index("重点关注 SPU") < body.index("SPU ROI")
    assert body.index("SPU ROI") < body.index("广告日明细")
    assert body.index("基础设置</div>") < body.index("采购工作台")


def test_sidebar_marks_ad_daily_entry_active():
    body = _sidebar_html("ad-daily")

    assert 'href="../../v2/pages/ad-daily"' in body
    assert 'title="广告日明细" aria-current="page"' in body
    assert body.index("采购工作台") < body.index("店铺注册")
    assert body.index("店铺注册") < body.index("枚举映射")
    assert body.index("运行配置") < body.index("定时任务")
    assert body.index("数据工具</div>") < body.index("拦截配置")


def test_sidebar_script_synchronizes_persisted_and_responsive_state():
    """Restored collapse state and breakpoint changes use the same state setters."""
    assert "function setCollapsed(collapsed, persist)" in _SIDEBAR_TOGGLE_JS
    assert "setCollapsed(readCollapsed(), false);" in _SIDEBAR_TOGGLE_JS
    assert "function setMobileOpen(open, restoreFocus)" in _SIDEBAR_TOGGLE_JS
    assert "setMobileOpen(false, false);" in _SIDEBAR_TOGGLE_JS
    assert "sb.toggleAttribute('inert', hidden);" in _SIDEBAR_TOGGLE_JS
    assert "desktopQuery.addEventListener('change'" in _SIDEBAR_TOGGLE_JS


def test_sidebar_css_is_injected_after_page_styles():
    """Page-level ``margin`` declarations must not override the sidebar offset."""
    body = bytes(dashboard_page().body).decode()
    sidebar_css_at = body.index(_SIDEBAR_CSS.strip())

    assert body.index("html, body {") < sidebar_css_at < body.index("</style>")
    assert "margin-left: var(--sidebar-width);" in _SIDEBAR_CSS


def test_sidebar_css_is_injected_for_external_stylesheet_pages():
    """Pages with only linked CSS still need the shared responsive sidebar CSS."""
    for rendered in (runtime_configs_page(), sync_jobs_page()):
        body = bytes(rendered.body).decode()
        sidebar_css_at = body.index(_SIDEBAR_CSS.strip())

        assert "<style>" in body
        assert "margin-left: var(--sidebar-width);" in body
        assert "@media (max-width: 991.98px)" in body
        assert body.index("</head>") > sidebar_css_at


def test_sidebar_tokens_fall_back_on_pages_with_a_different_theme_vocabulary():
    """The enum-map page uses ``--bg``/``--text`` instead of paper tokens."""
    body = bytes(enum_map_page().body).decode()

    assert "--sidebar-paper-deep: var(--paper-deep, var(--card, #EAE3D2));" in body
    assert "--sidebar-ink: var(--ink, var(--text, #1B1814));" in body
    assert "background: var(--sidebar-paper-deep);" in body
    assert "font-family: var(--mono);" not in _SIDEBAR_CSS


def test_dashboard_consumes_backend_owned_summary_fields():
    """Dashboard cards must not count list rows in browser JavaScript."""
    src = (
        Path(__file__).resolve().parents[2]
        / "tts_erp_v2"
        / "static"
        / "js"
        / "dashboard.js"
    ).read_text(encoding="utf-8")

    assert "/v2/reporting/coverage" in src
    assert "/v2/reporting/missing-cost-products" not in src
    assert "data.missing_cost_spus" in src
    assert "data.total_spus" in src
    assert "X-Total-Count" in src
    assert "value: shopTotal" in src


def test_dashboard_links_to_runtime_config_console():
    body = bytes(dashboard_page().body).decode()

    assert 'href="../../v2/pages/runtime-configs"' in body
    assert "配置下发、灰度控制与凭证管理" in body
    assert "即将上线" not in body


def test_runtime_config_page_links_its_owned_assets():
    body = bytes(runtime_configs_page().body).decode()

    assert "运行配置" in body
    assert "../../static/css/runtime-configs.css?v=" in body
    assert "../../static/js/runtime-configs.js?v=" in body
    assert 'href="../../v2/pages/runtime-configs"' in body


def test_runtime_config_page_requires_readwrite_operator(
    api_client, readonly_key, readwrite_key
):
    readonly_response = api_client.get(
        "/v2/pages/runtime-configs",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert readonly_response.status_code == 403, readonly_response.text

    response = api_client.get(
        "/v2/pages/runtime-configs",
        headers={"Authorization": f"Bearer {readwrite_key}"},
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/html")
    assert "运行配置" in response.text


def test_sync_jobs_page_is_in_pages_shell_and_sidebar(api_client, readonly_key, readwrite_key):
    body = bytes(sync_jobs_page().body).decode()

    assert "定时任务管理" in body
    assert "../../static/css/sync-jobs.css?v=" in body
    assert "../../static/js/sync-jobs.js?v=" in body
    assert 'href="../../v2/pages/sync-jobs"' in body
    assert 'title="定时任务" aria-current="page"' in body

    readonly_response = api_client.get(
        "/v2/pages/sync-jobs",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert readonly_response.status_code == 403, readonly_response.text

    response = api_client.get(
        "/v2/pages/sync-jobs",
        headers={"Authorization": f"Bearer {readwrite_key}"},
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/html")


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


def test_shops_page_returns_200_with_html(api_client, readonly_key):
    """GET /v2/pages/shops → 200 text/html shell linking static assets."""
    r = api_client.get(
        "/v2/pages/shops",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/html")
    body = r.text
    assert "../../static/vendor/bootstrap.min.css" in body
    assert "../../static/js/shops.js" in body
    assert 'id="f-service-id"' in body
    assert 'id="f-app-key"' in body
    assert 'id="f-app-secret"' in body
    assert 'id="app-credentials-dialog"' in body
    assert 'id="d-app-secret"' in body
    assert body.count('type="password"') >= 2
    assert 'href="/static/' not in body
    assert 'src="/static/' not in body


def test_shops_page_js_manages_service_app_credentials() -> None:
    source = Path("tts_erp_v2/static/js/shops.js").read_text()
    assert "app_secret: appSecret || null" in source
    assert (
        'configBtn.className = "shop-action shop-action--credentials btn-config-app"'
        in source
    )
    assert 'authBtn.className = "shop-action shop-action--authorize btn-auth"' in source
    assert 'setActionContent(configBtn, "⚙", "配置 App")' in source
    assert "shop-action:focus-visible" in source
    assert "app_credentials_configured" in source
    assert "bindAppCredentialsDialog();" in source
    assert "admin 会话" in source


def test_shops_page_requires_some_auth(api_client):
    r = api_client.get("/v2/pages/shops")
    assert r.status_code == 401


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
