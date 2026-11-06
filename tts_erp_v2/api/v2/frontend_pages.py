"""Manifest-backed documents for explicitly migrated, already-authorized pages.

Vite's backend integration owns the manifest and React refresh protocol:
https://vite.dev/guide/backend-integration.html (Vite, MIT).
This renderer never infers development mode from missing production artifacts.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse

from tts_erp_v2.access._context import user_pages_var
from tts_erp_v2.accounts.pages import PAGES
from tts_erp_v2.api.deps import require_role_at_least
from tts_erp_v2.runtime_settings import get_setting

# Sole activation owner for reviewed content candidates, not a traffic switch.
# Candidate/native acceptance and same-version publication remain separate.
MIGRATED_PAGE_IDS: frozenset[str] = frozenset(
    {
        "dashboard",
        "intercept-requests",
        "intercept-stats",
        "shops",
        "enum-map",
        "ad-daily",
        "sync-jobs",
        "users",
        "runtime-configs",
        "intercept-configs",
        "spu-profit-deterioration",
        "manual-costs",
        "video-publish",
        "login",
        "spu-roi",
        "focused-spus",
        "media-assets",
        "digital-humans",
    }
)
_FRONTEND_DIR = Path(__file__).resolve().parents[2] / "static" / "frontend"
_ENTRY = "src/main.tsx"
_CACHE_CONTROL = {"Cache-Control": "private, no-store"}


@dataclass(frozen=True)
class _Assets:
    release_id: str
    entry: str
    css: tuple[str, ...]
    preloads: tuple[str, ...]


def _json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError("Expected JSON object")
    return value


def _asset_path(root: Path, name: str) -> Path:
    """Reject URL syntax, traversal and symlinks escaping the asset root."""
    if (
        not isinstance(name, str)
        or not name
        or any(character in name for character in "\\%?#:")
        or any(ord(character) <= 32 for character in name)
        or any(part in {"", ".", ".."} for part in name.split("/"))
    ):
        raise ValueError("Invalid frontend asset path")
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("Missing or escaping frontend asset")
    return path


def _string_list(record: dict[str, Any], key: str) -> list[str]:
    value = record.get(key, [])
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError("Invalid manifest list")
    return value


def _production_assets(root_path: str) -> _Assets:
    root = _FRONTEND_DIR.resolve()
    release_id = _json_object(root / "release.json").get("releaseId")
    if not isinstance(release_id, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]*", release_id
    ):
        raise ValueError("Invalid frontend release ID")
    release_root = (root / release_id).resolve()
    if not release_root.is_relative_to(root):
        raise ValueError("Escaping frontend release")
    manifest_path = _asset_path(release_root, ".vite/manifest.json")
    manifest = _json_object(manifest_path)
    entry = manifest.get(_ENTRY)
    if not isinstance(entry, dict) or entry.get("isEntry") is not True:
        raise ValueError("Missing frontend entry")

    # Validate the entire reachable graph, including lazy page chunks and fonts.
    # A visited set also handles legitimate shared/cyclic imports without recursion.
    records: dict[str, dict[str, Any]] = {}
    pending = [_ENTRY]
    while pending:
        key = pending.pop()
        if key in records:
            continue
        record = manifest.get(key)
        if not isinstance(record, dict):
            raise TypeError("Missing manifest chunk")
        filename = record.get("file")
        if not isinstance(filename, str) or not filename.endswith((".js", ".mjs")):
            raise ValueError("Invalid script asset")
        _asset_path(release_root, filename)
        for name in _string_list(record, "css"):
            _asset_path(release_root, name)
            if not name.endswith(".css"):
                raise ValueError("Invalid stylesheet asset")
        for name in _string_list(record, "assets"):
            _asset_path(release_root, name)
        records[key] = record
        pending.extend(_string_list(record, "imports"))
        pending.extend(_string_list(record, "dynamicImports"))

    prefix = f"{root_path}/static/frontend/{release_id}/"
    css: list[str] = []
    preloads: list[str] = []
    visited: set[str] = set()
    pending = [_ENTRY]
    while pending:
        key = pending.pop()
        if key in visited:
            continue
        visited.add(key)
        record = records[key]
        for name in _string_list(record, "css"):
            href = prefix + name
            if href not in css:
                css.append(href)
        if key != _ENTRY:
            preloads.append(prefix + record["file"])
        # Only eagerly imported chunks are linked. Vite loads lazy CSS itself.
        pending.extend(reversed(_string_list(record, "imports")))
    return _Assets(release_id, prefix + entry["file"], tuple(css), tuple(preloads))


def _development_origin(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or parsed.port is None
    ):
        raise ValueError(
            "Frontend development origin must be an explicit loopback origin"
        )
    return value.rstrip("/")


def _safe_json(value: object) -> str:
    # application/json remains an HTML raw-text element: </script> must not occur.
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _role_capabilities(request: Request) -> dict[str, bool]:
    """Presentation hints from the canonical gate, not resource/guard authority."""
    capabilities: dict[str, bool] = {}
    for role in ("readwrite", "admin"):
        try:
            require_role_at_least(request, role)
        except HTTPException as error:
            if error.status_code != 403:
                raise
            capabilities[role] = False
        else:
            capabilities[role] = True
    return capabilities


def render_frontend_page(
    request: Request, page_id: str, *, login_next_path: str | None = None
) -> HTMLResponse:
    """Render after the existing route's authentication and permission gates."""
    public_login = page_id == "login"
    if public_login and not login_next_path:
        raise HTTPException(
            status_code=503,
            detail="Frontend initialization unavailable",
            headers=_CACHE_CONTROL,
        )
    root_path = request.scope.get("root_path", "").rstrip("/")
    try:
        assets, origin = _load_assets(root_path)
    except (OSError, ValueError, TypeError) as error:
        raise HTTPException(
            status_code=503,
            detail="Frontend assets unavailable",
            headers=_CACHE_CONTROL,
        ) from error

    allowed = user_pages_var.get()
    bootstrap = {
        "rootPath": root_path,
        "pageId": page_id,
        "navigation": []
        if public_login
        else [
            {
                "id": page.page_id,
                "label": page.label,
                "group": page.group,
                "href": f"{root_path}/v2/pages/{page.page_id}",
            }
            for page in PAGES
            if allowed is None or page.permission_code in allowed
        ],
        "releaseId": assets.release_id,
        "roleCapabilities": (
            {"readwrite": False, "admin": False}
            if public_login
            else _role_capabilities(request)
        ),
    }
    if public_login:
        bootstrap["loginNextPath"] = login_next_path
    return _render_document(bootstrap, assets, origin)


def _load_assets(root_path: str) -> tuple[_Assets, str | None]:
    dev_origin = get_setting("TTS_ERP_FRONTEND_DEV_ORIGIN")
    if dev_origin is not None:
        origin = _development_origin(dev_origin)
        return _Assets("dev", f"{origin}/src/main.tsx", (), ()), origin
    return _production_assets(root_path), None


def _render_document(
    bootstrap: dict[str, Any],
    assets: _Assets,
    origin: str | None,
    *,
    status_code: int = 200,
    initial_content: str | None = None,
) -> HTMLResponse:
    # initial_content is generated only by our escaped structured-message renderer.
    # It is never accepted from HTTP input or placed into React as raw HTML.
    if initial_content is None:
        initial_content = (
            '<section id="frontend-startup" role="status" aria-live="polite">'
            "<h1>正在加载页面</h1><p>若持续显示此提示，前端资源可能加载失败。请刷新页面；如仍失败，请联系管理员。</p>"
            '<a href="">刷新页面</a></section>'
        )
    links = [
        f'<link rel="stylesheet" href="{escape(href, quote=True)}">'
        for href in assets.css
    ]
    links.extend(
        f'<link rel="modulepreload" href="{escape(href, quote=True)}">'
        for href in assets.preloads
    )
    development = ""
    if origin is not None:
        # Official @vitejs/plugin-react backend integration preamble. Keep before entry.
        development = f'''<script type="module">
import RefreshRuntime from {json.dumps(origin + "/@react-refresh")};
RefreshRuntime.injectIntoGlobalHook(window);
window.$RefreshReg$ = () => {{}};
window.$RefreshSig$ = () => (type) => type;
window.__vite_plugin_react_preamble_installed__ = true;
</script>
<script type="module" src="{escape(origin, quote=True)}/@vite/client"></script>'''
    page_data = bootstrap.get("pageData")
    document_title = (
        page_data.get("title", "新店接入授权")
        if isinstance(page_data, dict)
        else "tts-erp"
    )
    entry_script = (
        f'<script type="module" src="{escape(assets.entry, quote=True)}"></script>'
        if assets.entry
        else ""
    )
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(document_title)}</title>
{"\n".join(links)}
</head>
<body>
<div id="frontend-root">{initial_content}</div>
<script id="frontend-bootstrap" type="application/json">{_safe_json(bootstrap)}</script>
{development}
{entry_script}
</body>
</html>"""
    return HTMLResponse(html, status_code=status_code, headers=_CACHE_CONTROL)


# Auxiliary document metadata never adds permissions or navigation to PAGES.
# OAuth callbacks and denials must remain visible without an authenticated session.
_AUXILIARY_PAGE_IDS = frozenset({"message", "oauth-onboard"})
_MESSAGE_TONES = frozenset({"success", "danger", "warning", "default"})
_MESSAGE_ACTIONS = {
    "shops": ("/v2/pages/shops", "前往店铺注册台"),
    "login": ("/v2/auth/login", "返回登录页"),
    "onboard": ("/v2/oauth/tiktok/onboard", "重新开始授权"),
}


def _auxiliary_document(
    root_path: str,
    page_id: str,
    data: dict[str, Any],
    *,
    status_code: int,
    initial_content: str,
) -> HTMLResponse:
    if page_id not in _AUXILIARY_PAGE_IDS:
        raise ValueError("Unknown auxiliary frontend page")
    try:
        assets, origin = _load_assets(root_path)
    except (OSError, ValueError, TypeError):
        # Deliberate exception for result documents: a missing JS release cannot
        # replace a 403/400/502, or hide a completed single-use OAuth callback.
        # The same escaped facts remain readable without JS/CSS. When CSS exists,
        # the server content uses HeroUI 3.2.6 public component classes, not a skin.
        # No request is replayed; hydration only displays this completed result.
        assets, origin = _Assets("unavailable", "", (), ()), None
    bootstrap = {
        "rootPath": root_path,
        "pageId": page_id,
        "navigation": [],
        "releaseId": assets.release_id,
        "roleCapabilities": {"readwrite": False, "admin": False},
        "pageData": data,
    }
    return _render_document(
        bootstrap,
        assets,
        origin,
        status_code=status_code,
        initial_content=initial_content,
    )


def render_oauth_onboard_page(request: Request) -> HTMLResponse:
    root_path = request.scope.get("root_path", "").rstrip("/")
    # The shop workbench owns shop-bound authorization and command recovery.
    # The former onboard script called authorize without its required shop_pk.
    # Reuse its owner rather than copying that mutable workflow into this page.
    content = (
        '<main class="p-6"><section class="card card--default">'
        '<h1 class="card__title">新店接入授权</h1>'
        "<p>先在店铺注册台选择店铺、配置 App，再获取该店铺的授权链接。</p>"
        "<p>只读账号可查看说明；生成链接需要 readwrite 及以上权限和店铺页面访问权限。</p>"
        f'<a class="button button--primary" href="{escape(root_path, quote=True)}/v2/pages/shops">前往店铺注册台</a>'
        "<p>请使用目标店铺的卖家账号完成授权。授权完成后，同步任务会在下次运行时接管。</p>"
        f"<p>Partner Center 回调地址：<code>{escape(root_path)}/v2/oauth/tiktok/callback</code></p>"
        "</section></main>"
    )
    return _auxiliary_document(
        root_path,
        "oauth-onboard",
        {"kind": "oauth-onboard"},
        status_code=200,
        initial_content=content,
    )


def render_message_page(
    root_path: str,
    *,
    title: str,
    message: str,
    tone: str,
    status_code: int,
    action: str = "shops",
    sections: list[dict[str, Any]] | None = None,
    authorization: dict[str, str] | None = None,
) -> HTMLResponse:
    if tone not in _MESSAGE_TONES or action not in _MESSAGE_ACTIONS:
        raise ValueError("Invalid auxiliary message metadata")
    data: dict[str, Any] = {
        "kind": "message",
        "title": title,
        "message": message,
        "tone": tone,
        "action": action,
        "sections": sections or [],
    }
    content = [
        f'<main class="p-6"><section class="card card--default"><h1 class="card__title">{escape(title)}</h1>',
        f"<p>{escape(message)}</p>",
    ]
    if authorization is not None:
        url = authorization["url"]
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise ValueError("Invalid authorization link")
        data["authorization"] = {"url": url, "expiresAt": authorization["expiresAt"]}
        content.append(
            f"<p>链接有效至 {escape(authorization['expiresAt'])}</p>"
            f'<p class="break-all"><code>{escape(url)}</code></p>'
            f'<a class="button button--primary" href="{escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">打开授权页面</a>'
        )
    for section in data["sections"]:
        content.append(f"<section><h2>{escape(section['title'])}</h2><dl>")
        for fact in section["facts"]:
            content.append(
                f'<dt>{escape(fact["label"])}</dt><dd class="break-all">{escape(fact["value"])}</dd>'
            )
        content.append("</dl></section>")
    path, label = _MESSAGE_ACTIONS[action]
    content.append(
        f'<a class="button button--secondary" href="{escape(root_path + path, quote=True)}">{label}</a></section></main>'
    )
    return _auxiliary_document(
        root_path,
        "message",
        data,
        status_code=status_code,
        initial_content="".join(content),
    )
