"""/v2/oauth/tiktok/* — TikTok Shop seller authorization (new-shop onboarding).

The front half of the OAuth lifecycle that v1's standalone
``oauth-receiver`` used to own (its ``/authorize`` + ``/callback``);
v2 retires that service, so the redirect target moves here.

Routes:
- ``GET /v2/oauth/tiktok/authorize`` — admin. Mints a single-use CSRF
  ``state`` and returns the TikTok authorization link. Open the link in
  a browser, sign in as the seller, approve.
- ``GET /v2/oauth/tiktok/callback`` — **public** (TikTok redirects the
  seller's browser here with ``?code=...&state=...``). Validates state,
  exchanges the auth_code, and bootstraps the ``integration.credentials``
  + ``commerce.shops`` rows. Renders an HTML result page by default;
  pass ``?format=json`` for machine-readable output.

The Redirect URL registered in Partner Center must point at the public
form of ``/callback`` with the external prefix, no port — e.g.
``http://daqiang.nat100.top/tts/v2/oauth/tiktok/callback`` (nginx only
proxies ``/tts/*`` to :9877; the prefix comes from
``TTS_ERP_EXTERNAL_PREFIX``, currently ``/tts``).

Contract + setup: ``tech-doc/api/tiktok-shop-oauth.md``. Orchestration
logic lives in :mod:`tts_erp_v2.proxy.tiktok_oauth` (kept router-free
so it is unit-testable without HTTP); outbound token calls live in
:mod:`tts_erp_v2.proxy.tiktok_auth`.
"""

from __future__ import annotations

import html as _html
import logging
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    status,
)
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from tts_erp_v2.api.deps import get_session, require_role_at_least
from tts_erp_v2.proxy.errors import ProxyError, SigningError, UpstreamHttpError
from tts_erp_v2.proxy.tiktok_auth import build_authorize_url
from tts_erp_v2.proxy.tiktok_oauth import (
    OAuthFlowError,
    complete_tiktok_authorization,
    pop_state,
    register_state,
)

router = APIRouter(prefix="/v2/oauth/tiktok", tags=["oauth-tiktok"])

log = logging.getLogger("tts_erp_v2.oauth")

# Callback query params must be declared on the handler; FastAPI maps
# them from the redirect query string.

_CALLBACK_PATH = "/v2/oauth/tiktok/callback"  # must match middleware EXEMPT_PATHS


# ─── HTML rendering (minimal, mirrors api/v2/auth.py style) ─────────


def _page(title: str, body_html: str, *, status_line: str = "") -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_html.escape(title)}</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; background: #f6f8fa; margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center; color: #1f2328; }}
  .card {{ background: #fff; border: 1px solid #d0d7de; border-radius: 8px; padding: 24px 28px; width: 480px; box-shadow: 0 1px 3px rgba(27,31,36,.12); }}
  h1 {{ font-size: 18px; margin: 0 0 6px; }}
  .status {{ font-size: 13px; font-weight: 600; margin: 0 0 14px; }}
  .ok .status {{ color: #1a7f37; }}
  .err .status {{ color: #cf222e; }}
  p {{ font-size: 13px; line-height: 1.5; margin: 6px 0; }}
  code {{ background: #f6f8fa; border: 1px solid #d0d7de; border-radius: 4px; padding: 1px 5px; font-size: 12px; }}
  pre {{ background: #f6f8fa; border: 1px solid #d0d7de; border-radius: 6px; padding: 10px; font-size: 12px; overflow: auto; }}
  table {{ border-collapse: collapse; font-size: 13px; width: 100%; margin: 8px 0; }}
  td, th {{ border: 1px solid #d0d7de; padding: 5px 8px; text-align: left; }}
  th {{ background: #f6f8fa; font-weight: 600; }}
  a {{ color: #0969da; }}
</style>
</head>
<body>
<div class="card {_html.escape('ok' if 'success' in title.lower() else 'err')}">
  <p class="status">{status_line}</p>
  {body_html}
</div>
</body>
</html>
"""


def _err_page(title: str, detail: str) -> HTMLResponse:
    return HTMLResponse(
        content=_page(
            title,
            f"<p>{_html.escape(detail)}</p>",
            status_line=_html.escape(title),
        ),
        status_code=status.HTTP_400_BAD_REQUEST,
    )


def _json(*, ok: bool, http_status: int, **fields: Any) -> JSONResponse:
    return JSONResponse(
        status_code=http_status,
        content={"ok": ok, **fields},
    )


# ─── authorize link ──────────────────────────────────────────────────


@router.get(
    "/authorize", summary="Start TikTok seller authorization"
)
def authorize(
    request: Request,
    sess: Session = Depends(get_session),  # noqa: B008 — FastAPI DI 惯例
    redirect_to: str | None = Query(
        default=None,
        description="(display hint only) where the operator should land after the flow.",
    ),
):
    """Mint a single-use CSRF state and build the TikTok authorization link.

    **Admin only.** Returns the ``authorize_url`` to open in a browser
    (as the seller) plus the raw ``state`` for diagnostics.

    Requires ``TIKTOK_SERVICE_ID`` (Partner Center App & Service page)
    in the server env — else 500 with a config message.
    """
    require_role_at_least(request, "admin")
    try:
        raw_state, expires_at = register_state(sess)
        authorize_url = build_authorize_url(state=raw_state)
    except SigningError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
        ) from exc

    log.info(
        "oauth authorize: state registered for redirect=%s expires=%s",
        redirect_to,
        expires_at.isoformat(),
    )
    payload = {
        "authorize_url": authorize_url,
        "state": raw_state,
        "state_expires_at": expires_at.isoformat(),
        "hint": (
            "open authorize_url in a browser, approve as the seller, then "
            f"TikTok redirects to {_CALLBACK_PATH} with code+state"
        ),
    }
    if request.query_params.get("format") == "json":
        return _json(ok=True, http_status=200, **payload)
    body = (
        "<h1>Authorize a new TikTok shop</h1>"
        f"<p>State registered (single-use, expires "
        f"<code>{_html.escape(payload['state_expires_at'])}</code> UTC).</p>"
        '<p><strong>Open this link in a browser</strong> and approve as the '
        "seller:</p>"
        f'<p><a href="{_html.escape(authorize_url)}">Open authorization link</a></p>'
        f"<p><code>{_html.escape(authorize_url)}</code></p>"
        "<p>After approval, TikTok redirects here with "
        "<code>?code=...&amp;state=...</code> and this page shows the result.</p>"
    )
    return HTMLResponse(
        content=_page("tts-erp · authorize", body, status_line="Authorization link ready"),
        status_code=status.HTTP_200_OK,
    )


# ─── callback (public) ───────────────────────────────────────────────


@router.get("/callback", summary="TikTok OAuth redirect target (public)")
def callback(
    request: Request,
    sess: Session = Depends(get_session),  # noqa: B008 — FastAPI DI 惯例
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    format: str | None = Query(default=None),
):
    """Handle TikTok's redirect after the seller approves/rejects.

    **Public** — TikTok hits this URL (browser redirect), so it is in
    the auth-middleware exempt list and needs no API key.

    Success: state validated + consumed, auth_code exchanged, and the
    ``integration.credentials`` + ``commerce.shops`` rows upserted.
    The sync worker picks the new shop up automatically on its next
    tick (it fans out over credentials rows).
    """
    fmt = (format or "").lower() == "json"
    if code:
        log.info(
            "oauth callback received: code_prefix=%s state_present=%s fmt=%s",
            code[:10],
            bool(state),
            fmt,
        )
    else:
        log.warning(
            "oauth callback received without code (error=%s state_present=%s)",
            error,
            bool(state),
        )
    if fmt:
        return _handle_json(code=code, state=state, error=error, sess=sess)
    return _handle_html(code=code, state=state, error=error, sess=sess)


def _handle_json(
    *, code: str | None, state: str | None, error: str | None, sess: Any
) -> JSONResponse:
    code_pfx = (code or "")[:10]
    state_present = bool(state)
    # Seller rejected at the consent screen.
    if error:
        if state_present:
            pop_state(sess, state)  # spend the CSRF token best-effort
        log.warning("oauth callback json denied: error=%s state_present=%s", error, state_present)
        return _json(
            ok=False,
            http_status=200,
            kind="denied",
            error=error,
        )
    if not code:
        log.warning("oauth callback json missing_code: state_present=%s", state_present)
        return _json(
            ok=False,
            http_status=400,
            kind="missing_code",
            error="callback hit without ?code — start from /v2/oauth/tiktok/authorize",
        )
    try:
        out = complete_tiktok_authorization(sess, code=code, state=state or "")
    except OAuthFlowError as exc:
        log.warning(
            "oauth callback json rejected: kind=%s code_prefix=%s state_present=%s msg=%s",
            exc.kind,
            code_pfx,
            state_present,
            exc.message,
        )
        return _json(
            ok=False,
            http_status=400,
            kind=exc.kind,
            error=exc.message,
        )
    except UpstreamHttpError as exc:
        log.error(
            "oauth callback json upstream failure: code_prefix=%s upstream_code=%s %s",
            code_pfx,
            getattr(exc, "upstream_code", None),
            exc,
        )
        return _json(
            ok=False,
            http_status=502,
            kind="upstream",
            error=f"token exchange failed: {exc}",
            upstream_code=getattr(exc, "upstream_code", None),
        )
    except ProxyError as exc:
        log.error("oauth callback json proxy failure: code_prefix=%s %s", code_pfx, exc)
        return _json(
            ok=False,
            http_status=502,
            kind="proxy",
            error=str(exc),
        )
    log.info("oauth callback: shop=%s authorized", out["shop_id"])
    return _json(ok=True, http_status=200, kind="authorized", result=out)


def _handle_html(
    *, code: str | None, state: str | None, error: str | None, sess: Any
) -> HTMLResponse:
    code_pfx = (code or "")[:10]
    state_present = bool(state)
    if error:
        if state:
            pop_state(sess, state)
        return HTMLResponse(
            content=_page(
                "tts-erp · authorization not granted",
                f"<p>TikTok reports: <code>{_html.escape(error)}</code>.</p>"
                "<p>The seller did not grant access. Start a fresh "
                "<code>/v2/oauth/tiktok/authorize</code> when ready.</p>",
                status_line="Authorization not granted",
            ),
            status_code=status.HTTP_200_OK,
        )
    if not code:
        return HTMLResponse(
            content=_page(
                "tts-erp · callback",
                "<p>This is the OAuth redirect target — it was opened "
                "without a <code>?code</code>.</p>"
                "<p>Start the flow from "
                "<code>/v2/oauth/tiktok/authorize</code>.</p>",
                status_line="No authorization code",
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    try:
        out = complete_tiktok_authorization(sess, code=code, state=state or "")
    except OAuthFlowError as exc:
        log.warning(
            "oauth callback rejected: kind=%s code_prefix=%s state_present=%s msg=%s",
            exc.kind,
            code_pfx,
            state_present,
            exc.message,
        )
        return _err_page("Authorization failed", exc.message)
    except UpstreamHttpError as exc:
        log.error(
            "oauth callback upstream failure: code_prefix=%s upstream_code=%s %s",
            code_pfx,
            getattr(exc, "upstream_code", None),
            exc,
        )
        return HTMLResponse(
            content=_page(
                "Authorization failed",
                f"<p>Token exchange was rejected upstream: "
                f"<code>{_html.escape(str(exc))}</code></p>"
                "<p>The one-time auth code is now spent — run "
                "<code>/v2/oauth/tiktok/authorize</code> again for a fresh link.</p>",
                status_line="Upstream token exchange failed",
            ),
            status_code=status.HTTP_502_BAD_GATEWAY,
        )
    except ProxyError as exc:
        log.error("oauth callback proxy failure: code_prefix=%s %s", code_pfx, exc)
        return HTMLResponse(
            content=_page(
                "Authorization failed",
                f"<p><code>{_html.escape(str(exc))}</code></p>",
                status_line="Proxy error",
            ),
            status_code=status.HTTP_502_BAD_GATEWAY,
        )

    log.info(
        "oauth callback: shop=%s authorized (html) credential_id=%s account_id=%s",
        out.get("shop_id"),
        out.get("credential_id"),
        out.get("account_id"),
    )
    rows = []
    for label, value in (
        ("Shop id", out.get("shop_id")),
        ("Seller name", out.get("account_name")),
        ("Region", out.get("region")),
        ("Seller type", out.get("seller_type")),
        ("Credentials row", out.get("credential_id")),
        ("Channel account row", out.get("account_id")),
        ("Access token expires", out.get("expires_at")),
    ):
        rows.append(
            f"<tr><th>{_html.escape(label)}</th>"
            f"<td><code>{_html.escape(str(value))}</code></td></tr>"
        )
    scopes = ", ".join(out.get("granted_scopes") or [])
    body = (
        "<p>The shop is authorized. Sync jobs pick it up automatically "
        "on their next tick.</p>"
        f"<table>{''.join(rows)}</table>"
        f"<p>Granted scopes: <code>{_html.escape(scopes)}</code></p>"
    )
    return HTMLResponse(
        content=_page(
            "tts-erp · shop authorized",
            body,
            status_line=f"Shop {out.get('shop_id')} authorized",
        ),
        status_code=status.HTTP_200_OK,
    )
