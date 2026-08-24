"""Thin FastAPI router for oauth-receiver — Wave 2 of the merge.

Exposes ONLY 3 endpoints (contract per /home/schan/merge-design.md §3.1):

  GET /authorize   browser-initiated OAuth flow (returns JSON or HTML)
  GET /callback    TikTok OAuth redirect target (PUBLIC, must work)
  GET /healthz     merged health check (oauth_receiver + tts_erp + miaoshou)

All business logic lives in oauth_receiver_core; this router is glue.
The parent app does app.include_router(router).
"""

from __future__ import annotations

import html
import os
import time
from typing import Any

import oauth_receiver_core as oc
import psycopg
from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

router = APIRouter()


# ─── Page rendering helpers ───────────────────────────────────────────


def _html_page(title: str, headline_html: str, body_html: str) -> str:
    """Minimal standalone HTML page (mirrors original oauth_receiver.py.bak)."""
    safe_title = html.escape(title)
    return (
        "<!DOCTYPE html>\n"
        '<html><head><meta charset="utf-8"><title>' + safe_title + "</title>\n"
        "<style>\n"
        "body{font-family:-apple-system,Segoe UI,sans-serif;"
        "max-width:720px;margin:60px auto;padding:0 16px;"
        "color:#222;line-height:1.5}\n"
        "code{background:#f4f4f4;padding:2px 6px;border-radius:3px;"
        "font-size:0.92em}\n"
        "pre{background:#f6f8fa;padding:12px;border-radius:6px;"
        "overflow:auto;font-size:0.85em}\n"
        ".ok{color:#2c8a4a;font-weight:600}\n"
        ".err{color:#c53030;font-weight:600}\n"
        ".warn{color:#9a6700;font-weight:600}\n"
        "a.btn{display:inline-block;padding:8px 14px;background:#1a7f37;"
        "color:white;border-radius:6px;text-decoration:none;font-weight:600}\n"
        "</style></head>\n"
        "<body><h1>" + headline_html + "</h1>" + body_html + "</body></html>"
    )


def _help_page_html(path: str) -> str:
    """Rendered when /callback is hit without ?code (acts as root help page)."""
    return _html_page(
        "tts-erp oauth-receiver — status",
        '<span class="ok">tts-erp oauth-receiver is up</span>',
        "<p>You reached <code>" + html.escape(path) + "</code>. This is the "
        "TikTok OAuth redirect target. To start the flow, visit "
        '<a href="/authorize">/authorize</a> in a browser.</p>'
        "<p>Endpoints exposed by this router:</p><ul>"
        "<li><code>GET /authorize</code> — start OAuth flow</li>"
        "<li><code>GET /callback</code> — TikTok redirect target "
        "(you are here)</li>"
        "<li><code>GET /healthz</code> — health check</li></ul>"
        "<p>Internal token management functions are NOT HTTP endpoints. "
        "They are called directly by tts-erp in-process. See "
        "<code>oauth_receiver_core.py</code>.</p>",
    )


def _render_authorize_html(cfg: dict, registered_state: str, auth_url: str) -> str:
    return _html_page(
        "Authorize with " + cfg["label"],
        '<span class="ok">State token registered.</span>',
        (
            "<p>State token <code>"
            + html.escape(registered_state)
            + "</code> registered. Open the URL below to start the OAuth flow.</p>"
            "<p><strong>Authorize URL:</strong></p>"
            "<pre>" + html.escape(auth_url) + "</pre>"
            '<p><a class="btn" href="'
            + html.escape(auth_url)
            + '">Open in browser →</a></p>'
            "<p>Or copy and paste into a browser. After authorization, the "
            "provider will redirect to <code>"
            + html.escape(cfg["redirect_uri"])
            + "</code> with "
            "<code>?code=...&amp;state=...</code>.</p>"
        ),
    )


def _render_callback_token(result: dict) -> HTMLResponse:
    state_status = result["state_status"]
    code = result["code"]
    state = result.get("state") or "(none)"
    provider = result["provider"]
    token_result = result.get("token_result")

    state_color = "ok" if state_status == "matched" else "warn"
    state_line = (
        '<br><span class="'
        + state_color
        + '">state validation: '
        + html.escape(state_status)
        + "</span>"
        if state != "(none)"
        else ""
    )

    auto_html = ""
    if token_result and token_result.get("ok"):
        d = token_result["response"]["data"]
        auto_html = (
            "<h3>✓ Auto token exchange</h3>"
            '<p><span class="ok">Token obtained automatically.</span></p>'
            "<p><strong>access_token:</strong></p><pre>"
            + html.escape(str(d.get("access_token", "")))
            + "</pre>"
            "<p><strong>refresh_token:</strong></p><pre>"
            + html.escape(str(d.get("refresh_token", "")))
            + "</pre>"
            "<p><strong>shop_cipher:</strong> <code>"
            + html.escape(str(d.get("shop_cipher", "")))
            + "</code></p>"
            "<p><strong>shop_region:</strong> <code>"
            + html.escape(str(d.get("shop_region", "")))
            + "</code></p>"
            "<p><strong>access_token expires at:</strong> <code>"
            + html.escape(str(d.get("access_token_expire_in", "")))
            + "</code> (unix ts, ~7 days from now)</p>"
            "<p>Fetch via tts-erp business layer "
            "(<code>oauth_receiver_core.db_load_token(shop_id, provider)"
            "</code>).</p>"
        )
    elif token_result is not None:
        msg = token_result["response"].get("message", "unknown")
        auto_html = (
            "<h3>⚠ Auto token exchange failed</h3>"
            '<p><span class="err">' + html.escape(str(msg)) + "</span></p>"
        )

    title = "✓ Code Captured" + (
        " + Token Exchanged" if token_result and token_result.get("ok") else ""
    )
    body = _html_page(
        title,
        '<span class="ok">Authorization code received.</span> ' + state_line,
        (
            "<p><strong>code:</strong></p><pre>" + html.escape(str(code)) + "</pre>"
            "<p><strong>state:</strong> <code>"
            + html.escape(str(state))
            + "</code></p>"
            "<p><strong>provider:</strong> <code>"
            + html.escape(provider)
            + "</code></p>"
            + auto_html
        ),
    )
    return HTMLResponse(content=body)


def _render_callback_error(result: dict) -> HTMLResponse:
    err = result.get("error", "unknown")
    state = result.get("state")
    body = _html_page(
        "OAuth Error",
        '<span class="err">Provider returned error: <code>'
        + html.escape(str(err))
        + "</code></span>",
        (
            "<p>state: <code>"
            + html.escape(str(state) if state else "(none)")
            + "</code></p>"
            "<p>Check the provider's authorization request — something "
            "was rejected.</p>"
        ),
    )
    return HTMLResponse(status_code=400, content=body)


# ─── Slice 1: GET /authorize ─────────────────────────────────────────


@router.get("/authorize")
def authorize(
    request: Request,
    provider: str = Query("tiktok"),
    state: str | None = Query(None),
    format: str | None = Query(None, alias="format"),
) -> Any:
    """Build the provider's authorize URL and register CSRF state."""
    cfg = oc.provider_config(provider)
    if not cfg:
        return JSONResponse(
            status_code=400,
            content={"error": "unknown provider: " + provider},
        )

    registered_state = oc.register_state(provider, state)
    auth_url = oc.build_authorize_url(provider, registered_state)
    if not auth_url:
        return JSONResponse(
            status_code=400,
            content={"error": "could not build authorize URL for " + provider},
        )

    payload = {
        "provider": provider,
        "state": registered_state,
        "authorize_url": auth_url,
        "redirect_uri": cfg["redirect_uri"],
        "configured": bool(cfg.get("app_key")) or cfg.get("mock", False),
        "hint": "open authorize_url in browser to start OAuth flow",
    }

    if format == "html":
        return HTMLResponse(
            content=_render_authorize_html(cfg, registered_state, auth_url)
        )

    # Browser context → HTML landing page (matches original stdlib behavior).
    accept = request.headers.get("accept", "")
    if "text/html" in accept:
        return HTMLResponse(
            content=_render_authorize_html(cfg, registered_state, auth_url)
        )

    return payload


# ─── Slice 2: GET /callback ───────────────────────────────────────────


@router.get("/callback")
def callback(
    code: str | None = Query(None),
    state: str | None = Query(None),
    error: str | None = Query(None),
    provider: str = Query("tiktok"),
) -> Any:
    """TikTok OAuth redirect target (PUBLIC — TikTok servers hit this)."""
    result = oc.handle_callback(code=code, state=state, provider=provider, error=error)

    if not result["handled"]:
        return HTMLResponse(content=_help_page_html("/callback"))

    if result["kind"] == "error":
        return _render_callback_error(result)

    return _render_callback_token(result)


# ─── Slice 3: GET /healthz (merged) ───────────────────────────────────


def _oauth_receiver_section() -> dict:
    try:
        cfg = oc.provider_config("tiktok")
    except Exception as e:  # noqa: BLE001
        raise RuntimeError("oauth_receiver_core not initialized: " + str(e)) from e

    providers: dict = {}
    if cfg:
        providers["tiktok"] = {
            "configured": bool(cfg.get("app_key")),
            "mock": bool(cfg.get("mock")),
            "authorize_url": cfg.get("authorize_url"),
            "token_url": cfg.get("token_url"),
        }

    return {
        "db_ok": oc.is_db_ok(),
        "token_count": len(oc._token_history),
        "active_states": len(oc._states),
        "providers": providers,
    }


def _tts_erp_section() -> dict:
    """Best-effort — never raises.

    The merge-design §3.3 spec asks for a `tts_erp.db_ok` boolean, but
    `tts_erp.py` never exposed a `_db_ready()` helper. Probing the DB
    directly with `psycopg.connect` is the honest, dependency-free
    answer — and it matches what the oauth_receiver section already
    does for its own DB. ~5 lines, no `_db_ready` indirection.

    The previous implementation `from tts_erp import _db_ready` raised
    ImportError on every cold start, was silently swallowed by
    `except Exception`, and permanently returned db_ok=False — a lie
    (DB was reachable, /db/orders returned rows).
    """
    section: dict = {"db_ok": False, "last_sync_at": None}
    url = os.environ.get("TTS_ERP_DB_URL")
    if not url:
        return section
    try:
        with psycopg.connect(url, connect_timeout=2) as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        section["db_ok"] = True
    except Exception as e:  # noqa: BLE001
        print(f"[oauth-receiver/healthz] tts_erp DB probe failed: {e}")
    return section


def _miaoshou_section() -> dict:
    """Best-effort — never raises."""
    return {
        "configured": bool(os.environ.get("MIAOSHOU_LICENSE_ID")),
        "env": os.environ.get("MIAOSHOU_ENV", "test"),
    }


@router.get("/healthz")
def healthz() -> Any:
    """Merged health check per merge-design.md §3.3."""
    try:
        oauth_section = _oauth_receiver_section()
    except Exception as e:  # noqa: BLE001
        return JSONResponse(
            status_code=503,
            content={
                "status": "down",
                "ts": time.time(),
                "version": "tts-erp+oauth-receiver/1.0",
                "error": "oauth_receiver init failed: " + str(e),
            },
        )

    overall_status = "ok" if oauth_section["db_ok"] else "degraded"
    return JSONResponse(
        status_code=200,
        content={
            "status": overall_status,
            "ts": time.time(),
            "version": "tts-erp+oauth-receiver/1.0",
            "components": {
                "oauth_receiver": oauth_section,
                "tts_erp": _tts_erp_section(),
                "miaoshou": _miaoshou_section(),
            },
        },
    )
