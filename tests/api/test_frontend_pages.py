"""Contracts for the manifest-backed frontend document, not business APIs."""

from __future__ import annotations

import json
import re

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from tts_erp_v2.access import AccessGrant, AuthMode, Role
from tts_erp_v2.access._context import user_pages_var
from tts_erp_v2.accounts.pages import PAGES
from tts_erp_v2.api.v2 import frontend_pages

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]


def _request(root_path: str = "") -> Request:
    return Request(
        {"type": "http", "root_path": root_path, "path": "/v2/pages/dashboard"}
    )


def _bootstrap(body: str) -> dict:
    match = re.search(
        r'<script id="frontend-bootstrap" type="application/json">(.*?)</script>',
        body,
        re.DOTALL,
    )
    assert match is not None
    return json.loads(match[1])


@pytest.fixture
def release(tmp_path, monkeypatch):
    root = tmp_path / "frontend"
    release_root = root / "TEST_release"
    (release_root / ".vite").mkdir(parents=True)
    (release_root / "assets").mkdir()
    manifest = {
        "src/main.tsx": {
            "file": "assets/main.js",
            "isEntry": True,
            "imports": ["_shared.js"],
            "dynamicImports": ["src/pages/dashboard.tsx"],
            "css": ["assets/main.css"],
        },
        "_shared.js": {
            "file": "assets/shared.js",
            "css": ["assets/shared.css"],
            "assets": ["assets/font.woff2"],
        },
        "src/pages/dashboard.tsx": {"file": "assets/dashboard.js"},
    }
    for name in (
        "main.js",
        "shared.js",
        "dashboard.js",
        "main.css",
        "shared.css",
        "font.woff2",
    ):
        (release_root / "assets" / name).write_text("/* TEST_asset */")
    (root / "release.json").write_text(json.dumps({"releaseId": "TEST_release"}))
    manifest_path = release_root / ".vite/manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    monkeypatch.setattr(frontend_pages, "_FRONTEND_DIR", root)
    monkeypatch.delenv("TTS_ERP_FRONTEND_DEV_ORIGIN", raising=False)
    return root, manifest_path, manifest


@pytest.mark.parametrize("prefix", ["", "/tts"])
def test_document_bootstrap_and_recursive_assets(release, prefix):
    response = frontend_pages.render_frontend_page(_request(prefix), "dashboard")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    body = bytes(response.body).decode()
    bootstrap = _bootstrap(body)
    assert set(bootstrap) == {"rootPath", "pageId", "navigation", "releaseId", "roleCapabilities"}
    assert bootstrap["roleCapabilities"] == {"readwrite": False, "admin": False}
    assert bootstrap["rootPath"] == prefix
    assert bootstrap["pageId"] == "dashboard"
    assert bootstrap["releaseId"] == "TEST_release"
    assert bootstrap["navigation"] == [
        {
            "id": p.page_id,
            "label": p.label,
            "group": p.group,
            "href": f"{prefix}/v2/pages/{p.page_id}",
        }
        for p in PAGES
    ]
    assert 'id="frontend-root"' in body
    for name in ("main.js", "shared.js", "main.css", "shared.css"):
        assert f"{prefix}/static/frontend/TEST_release/assets/{name}" in body
    assert 'rel="modulepreload"' in body
    for legacy in (
        "bootstrap.min",
        "sidebar-toggle",
        "static/js/",
        "static/css/",
        "dashboard.js",
    ):
        assert legacy not in body


@pytest.mark.parametrize("mode,role,bypass,expected", [
    ("enforce", "readonly", False, {"readwrite": False, "admin": False}),
    ("enforce", "readwrite", False, {"readwrite": True, "admin": False}),
    ("enforce", "admin", False, {"readwrite": True, "admin": True}),
    ("enforce", None, False, {"readwrite": False, "admin": False}),
    ("off", None, True, {"readwrite": True, "admin": True}),
    ("shadow", "readonly", True, {"readwrite": True, "admin": True}),
])
def test_role_hints_use_canonical_grant_not_legacy_role_or_cookie_me(
    release, mode, role, bypass, expected,
):
    request = _request()
    request.scope["api_key_role"] = "admin"  # Must not override a resolved readonly grant.
    request.scope["access_grant"] = AccessGrant(
        mode=AuthMode(mode), role=Role(role) if role else None, bypass=bypass,
    )
    response = frontend_pages.render_frontend_page(request, "dashboard")
    assert _bootstrap(bytes(response.body).decode())["roleCapabilities"] == expected
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.parametrize("key_fixture,expected", [
    ("readonly_key", {"readwrite": False, "admin": False}),
    ("readwrite_key", {"readwrite": True, "admin": False}),
    ("admin_key", {"readwrite": True, "admin": True}),
])
def test_role_hints_follow_actual_key_middleware(api_client, request, release, key_fixture, expected):
    key = request.getfixturevalue(key_fixture)
    response = api_client.get("/v2/pages/dashboard", headers={"Authorization": f"Bearer {key}"})
    assert response.status_code == 200
    assert _bootstrap(response.text)["roleCapabilities"] == expected
    assert key not in response.text


@pytest.mark.parametrize("role,expected", [
    ("viewer", {"readwrite": False, "admin": False}),
    ("operator", {"readwrite": True, "admin": False}),
    ("admin", {"readwrite": True, "admin": True}),
])
def test_role_hints_cookie_wins_over_admin_bearer(
    api_client, admin_key, ua_user_factory, release, role, expected,
):
    user = ua_user_factory("test_ua_p2_capabilities", roles=(role,))
    login = api_client.post("/v2/auth/login", json={"username": user.username, "password": user.password})
    assert login.status_code == 200
    response = api_client.get("/v2/pages/dashboard", headers={"Authorization": f"Bearer {admin_key}"})
    assert response.status_code == 200
    assert _bootstrap(response.text)["roleCapabilities"] == expected
    assert admin_key not in response.text and user.username not in response.text


@pytest.mark.parametrize("allowed", [frozenset(), frozenset({"page:dashboard"})])
def test_navigation_uses_existing_permission_context(release, allowed):
    token = user_pages_var.set(allowed)
    try:
        body = bytes(
            frontend_pages.render_frontend_page(_request(), "dashboard").body
        ).decode()
    finally:
        user_pages_var.reset(token)
    assert [p["id"] for p in _bootstrap(body)["navigation"]] == (
        ["dashboard"] if allowed else []
    )


def test_bootstrap_cannot_terminate_script(release, monkeypatch):
    from tts_erp_v2.accounts.pages import PageDef

    hostile = '</script><script>alert("TEST")</script>&\u2028\u2029'
    monkeypatch.setattr(
        frontend_pages, "PAGES", (PageDef("dashboard", "", hostile, hostile),)
    )
    body = bytes(
        frontend_pages.render_frontend_page(_request(), "dashboard").body
    ).decode()
    assert _bootstrap(body)["navigation"][0]["label"] == hostile
    assert hostile not in body
    match = re.search(r'type="application/json">(.*?)</script>', body, re.DOTALL)
    assert match is not None
    data = match[1]
    assert "<" not in data and "&" not in data
    assert "\u2028" not in data and "\u2029" not in data


@pytest.mark.parametrize(
    "damage",
    [
        "release_missing",
        "release_json",
        "release_shape",
        "release_path",
        "manifest_missing",
        "manifest_json",
        "manifest_shape",
        "entry_missing",
        "entry_shape",
        "entry_not_entry",
        "chunk_missing",
        "dynamic_missing",
        "css_missing",
        "asset_missing",
        "file_missing",
        "imports_shape",
        "css_shape",
        "file_traversal",
        "file_absolute",
        "file_url",
        "file_backslash",
        "file_query",
        "file_symlink",
    ],
)
def test_broken_release_fails_closed(release, damage, tmp_path):
    root, manifest_path, manifest = release
    entry = manifest["src/main.tsx"]
    if damage == "release_missing":
        (root / "release.json").unlink()
    elif damage == "release_json":
        (root / "release.json").write_text("{")
    elif damage == "release_shape":
        (root / "release.json").write_text("[]")
    elif damage == "release_path":
        (root / "release.json").write_text(json.dumps({"releaseId": "../TEST_other"}))
    elif damage == "manifest_missing":
        manifest_path.unlink()
    elif damage == "manifest_json":
        manifest_path.write_text("{")
    elif damage == "manifest_shape":
        manifest_path.write_text("[]")
    elif damage == "entry_missing":
        del manifest["src/main.tsx"]
    elif damage == "entry_shape":
        manifest["src/main.tsx"] = []
    elif damage == "entry_not_entry":
        entry["isEntry"] = False
    elif damage == "chunk_missing":
        del manifest["_shared.js"]
    elif damage == "dynamic_missing":
        del manifest["src/pages/dashboard.tsx"]
    elif damage in {"css_missing", "asset_missing", "file_missing"}:
        name = {
            "css_missing": "shared.css",
            "asset_missing": "font.woff2",
            "file_missing": "main.js",
        }[damage]
        (manifest_path.parent.parent / "assets" / name).unlink()
    elif damage == "imports_shape":
        entry["imports"] = "_shared.js"
    elif damage == "css_shape":
        entry["css"] = [None]
    elif damage.startswith("file_"):
        entry["file"] = {
            "file_traversal": "../outside.js",
            "file_absolute": "/tmp/outside.js",
            "file_url": "https://example.com/main.js",
            "file_backslash": "assets\\main.js",
            "file_query": "assets/main.js?test",
        }.get(damage, "assets/link.js")
        if damage == "file_symlink":
            outside = tmp_path / "outside.js"
            outside.write_text("TEST_outside")
            (manifest_path.parent.parent / "assets/link.js").symlink_to(outside)
    if damage not in {"manifest_missing", "manifest_json", "manifest_shape"}:
        manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(HTTPException) as error:
        frontend_pages.render_frontend_page(_request(), "dashboard")
    assert error.value.status_code == 503
    assert error.value.headers is not None
    assert error.value.headers["Cache-Control"] == "private, no-store"


@pytest.mark.parametrize(
    "origin",
    [
        "https://example.com",
        "http://0.0.0.0:5173",
        "http://localhost.evil:5173",
        "http://localhost:5173/path",
        "http://user@localhost:5173",
        "http://localhost:bad",
    ],
)
def test_development_origin_rejects_non_loopback_or_non_origin(
    release, monkeypatch, origin
):
    monkeypatch.setenv("TTS_ERP_FRONTEND_DEV_ORIGIN", origin)
    with pytest.raises(HTTPException) as error:
        frontend_pages.render_frontend_page(_request(), "dashboard")
    assert error.value.status_code == 503


@pytest.mark.parametrize(
    "origin", ["http://localhost:5173", "http://127.0.0.1:5173", "http://[::1]:5173"]
)
def test_explicit_development_origin_injects_react_refresh(
    release, monkeypatch, origin
):
    (release[0] / "release.json").unlink()
    monkeypatch.setenv("TTS_ERP_FRONTEND_DEV_ORIGIN", origin)
    body = bytes(
        frontend_pages.render_frontend_page(_request("/tts"), "dashboard").body
    ).decode()
    assert _bootstrap(body)["releaseId"] == "dev"
    assert f"{origin}/@vite/client" in body
    assert f"{origin}/src/main.tsx" in body
    assert f"{origin}/@react-refresh" in body
    assert "injectIntoGlobalHook(window)" in body
    assert "__vite_plugin_react_preamble_installed__" in body
    assert body.index("injectIntoGlobalHook") < body.index(f"{origin}/src/main.tsx")


def test_activation_matches_implemented_candidate_pages():
    assert frontend_pages.MIGRATED_PAGE_IDS == frozenset({"dashboard", "intercept-requests", "intercept-stats", "shops", "enum-map", "ad-daily", "sync-jobs", "users", "runtime-configs", "intercept-configs", "spu-profit-deterioration", "manual-costs", "video-publish", "login"})


@pytest.fixture
def activated(release, monkeypatch):
    monkeypatch.setattr(
        frontend_pages,
        "MIGRATED_PAGE_IDS",
        frozenset({"dashboard", "intercept-requests", "intercept-stats", "shops", "enum-map"}),
    )


@pytest.mark.parametrize("page_id", ["dashboard", "intercept-requests", "intercept-stats", "shops", "enum-map"])
def test_original_route_uses_frontend_only_when_activated(
    api_client, readonly_key, activated, page_id
):
    response = api_client.get(
        f"/v2/pages/{page_id}", headers={"X-API-Key": readonly_key}
    )
    assert response.status_code == 200, response.text
    assert _bootstrap(response.text)["pageId"] == page_id
    assert response.headers["cache-control"] == "private, no-store"
    assert "bootstrap.min.css" not in response.text


def test_prefixed_route_produces_prefixed_links(
    prefixed_client, readonly_key, activated
):
    response = prefixed_client.get(
        "/v2/pages/dashboard", headers={"X-API-Key": readonly_key}
    )
    assert response.status_code == 200, response.text
    assert _bootstrap(response.text)["rootPath"] == "/tts"
    assert 'src="/tts/static/frontend/TEST_release/assets/main.js"' in response.text


def test_anonymous_and_denied_session_keep_original_gates(
    api_client, ua_user_factory, activated
):
    assert api_client.get("/v2/pages/dashboard").status_code == 401
    user = ua_user_factory("TEST_ua_frontend_viewer", roles=("viewer",))
    login = api_client.post(
        "/v2/auth/login", json={"username": user.username, "password": user.password}
    )
    assert login.status_code == 200
    permitted = api_client.get("/v2/pages/dashboard")
    assert permitted.status_code == 200
    assert {p["id"] for p in _bootstrap(permitted.text)["navigation"]} == {
        "dashboard",
        "focused-spus",
        "spu-roi",
        "ad-daily",
        "intercept-stats",
    }
    assert api_client.get("/v2/pages/intercept-requests").status_code == 403


def test_activated_route_never_falls_back_when_release_missing(
    api_client, readonly_key, activated, release
):
    (release[0] / "release.json").unlink()
    response = api_client.get(
        "/v2/pages/dashboard", headers={"X-API-Key": readonly_key}
    )
    assert response.status_code == 503
    assert "bootstrap.min.css" not in response.text


def test_non_migrated_route_keeps_legacy_renderer(api_client, readonly_key, activated):
    response = api_client.get("/v2/pages/intercept-configs", headers={"X-API-Key": readonly_key})
    assert response.status_code == 200
    assert "bootstrap.min.css" in response.text
    assert "frontend-bootstrap" not in response.text
