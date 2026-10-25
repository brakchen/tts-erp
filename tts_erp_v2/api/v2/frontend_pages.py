"""Manifest-backed documents for explicitly migrated, already-authorized pages.

Vite's backend integration owns the manifest and React refresh protocol:
https://vite.dev/guide/backend-integration.html (Vite, MIT).
This renderer never infers development mode from missing production artifacts.
"""

from __future__ import annotations

import json
import os
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

# Sole activation owner for reviewed content candidates, not a traffic switch.
# Candidate/native acceptance and same-version publication remain separate.
MIGRATED_PAGE_IDS: frozenset[str] = frozenset({
    "dashboard", "intercept-requests", "intercept-stats", "shops", "enum-map",
    "ad-daily", "sync-jobs", "users", "runtime-configs", "intercept-configs",
    "spu-profit-deterioration", "manual-costs", "video-publish", "login",
})
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
    dev_origin = os.getenv("TTS_ERP_FRONTEND_DEV_ORIGIN")
    try:
        if dev_origin is not None:
            origin = _development_origin(dev_origin)
            assets = _Assets("dev", f"{origin}/src/main.tsx", (), ())
        else:
            origin = None
            assets = _production_assets(root_path)
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
        "navigation": [] if public_login else [
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
            if public_login else _role_capabilities(request)
        ),
    }
    if public_login:
        bootstrap["loginNextPath"] = login_next_path
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
    html = f'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>tts-erp</title>
{"\n".join(links)}
</head>
<body>
<div id="frontend-root"><section id="frontend-startup" role="status" aria-live="polite">
<h1>正在加载页面</h1>
<p>若持续显示此提示，前端资源可能加载失败。请刷新页面；如仍失败，请联系管理员。</p>
<a href="">刷新页面</a>
</section></div>
<script id="frontend-bootstrap" type="application/json">{_safe_json(bootstrap)}</script>
{development}
<script type="module" src="{escape(assets.entry, quote=True)}"></script>
</body>
</html>'''
    return HTMLResponse(html, headers=_CACHE_CONTROL)
