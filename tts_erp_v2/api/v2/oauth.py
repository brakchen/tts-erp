"""Targeted TikTok Shop authorization and public callback.

The operator starts with an existing shop_pk and its own App configuration.
A successful callback updates only that shop; the worker picks it up on its
next normal scheduling tick.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
)
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from tts_erp_v2.api.deps import get_session, require_role_at_least
from tts_erp_v2.api.v2 import frontend_pages
from tts_erp_v2.db.models.commerce import ChannelAccount
from tts_erp_v2.proxy.errors import ProxyError, SigningError, UpstreamHttpError
from tts_erp_v2.proxy.tiktok_auth import build_authorize_url
from tts_erp_v2.proxy.tiktok_oauth import (
    OAuthFlowError,
    complete_tiktok_authorization,
    pop_state,
    register_state,
)
from tts_erp_v2.proxy.token_service import resolve_tiktok_app_credentials

router = APIRouter(prefix="/v2/oauth/tiktok", tags=["oauth-tiktok"])

log = logging.getLogger("tts_erp_v2.oauth")

# Callback query params must be declared on the handler; FastAPI maps
# them from the redirect query string.

_CALLBACK_PATH = "/v2/oauth/tiktok/callback"  # must match middleware EXEMPT_PATHS


def _json(*, ok: bool, http_status: int, **fields: Any) -> JSONResponse:
    return JSONResponse(
        status_code=http_status,
        content=jsonable_encoder({"ok": ok, **fields}),
    )


# ─── authorize link ──────────────────────────────────────────────────


@router.get("/authorize", summary="Start TikTok seller authorization")
def authorize(
    request: Request,
    sess: Session = Depends(get_session),  # noqa: B008 — FastAPI DI 惯例
    redirect_to: str | None = Query(
        default=None,
        description="(display hint only) where the operator should land after the flow.",
    ),
    shop_pk: int = Query(..., gt=0),
):
    """Create a single-use authorization link for this shop's own App."""
    require_role_at_least(request, "readwrite")
    account = sess.execute(select(ChannelAccount).where(
        ChannelAccount.id == shop_pk, ChannelAccount.platform == "tiktok",
    )).scalar_one_or_none()
    if account is None:
        raise HTTPException(404, "Shop not found")
    if not account.service_id:
        raise HTTPException(422, "Configure this shop's Service ID and App credentials first")
    service_id = account.service_id
    shop_id = account.shop_id
    try:
        app = resolve_tiktok_app_credentials(
            sess, shop_pk=account.id, service_id=service_id,
        )
        raw_state, expires_at = register_state(sess, extra={
            "expected_shop_pk": account.id,
            "expected_shop_id": shop_id,
            "service_id": service_id,
            "app_revision": app.revision,
            "created_by": request.scope.get("api_key_hash"),
        })
        authorize_url = build_authorize_url(state=raw_state, service_id=service_id)
    except SigningError as exc:
        raise HTTPException(422, str(exc)) from exc

    log.info(
        "oauth authorize: state registered for redirect=%s expires=%s",
        redirect_to,
        expires_at.isoformat(),
    )
    payload = {
        "authorize_url": authorize_url,
        "shop_id": shop_id,
        "service_id": service_id,
        "state": raw_state,
        "state_expires_at": expires_at.isoformat(),
        "hint": (
            "open authorize_url in a browser, approve as the seller, then "
            f"TikTok redirects to {_CALLBACK_PATH} with code+state"
        ),
    }
    if request.query_params.get("format") == "json":
        return _json(ok=True, http_status=200, **payload)
    return frontend_pages.render_message_page(
        request.scope.get("root_path", "").rstrip("/"), title="授权链接已生成",
        message="请使用目标店铺的卖家账号打开授权页面；链接仅可使用一次，过期后需重新获取。",
        tone="success", status_code=200,
        authorization={"url": authorize_url, "expiresAt": payload["state_expires_at"]},
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
    app_key: str | None = Query(default=None),
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
        return _handle_json(
            code=code,
            state=state,
            error=error,
            callback_app_key=app_key,
            sess=sess,
        )
    return _handle_html(
        request=request,
        code=code,
        state=state,
        error=error,
        callback_app_key=app_key,
        sess=sess,
    )


def _handle_json(
    *,
    code: str | None,
    state: str | None,
    error: str | None,
    callback_app_key: str | None,
    sess: Any,
) -> JSONResponse:
    code_pfx = (code or "")[:10]
    state_present = bool(state)
    # Seller rejected at the consent screen.
    if error:
        if state_present:
            pop_state(sess, state)  # spend the CSRF token best-effort; ignore return
        log.warning(
            "oauth callback json denied: error=%s state_present=%s",
            error,
            state_present,
        )
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
        out = complete_tiktok_authorization(
            sess,
            code=code,
            state=state or "",
            callback_app_key=callback_app_key,
        )
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
    for shop in out.get("shops") or []:
        log.info(
            "oauth callback: shop=%s authorized (json) credential_id=%s",
            shop.get("shop_id"),
            shop.get("credential_id"),
        )
    return _json(ok=True, http_status=200, kind="authorized", result=out)


def _handle_html(
    *,
    request: Request,
    code: str | None,
    state: str | None,
    error: str | None,
    callback_app_key: str | None,
    sess: Any,
) -> HTMLResponse:
    # Presentation consumes the existing flow once. React only displays its result;
    # it never calls callback/authorize or retries an exchanged single-use code.
    root_path = request.scope.get("root_path", "").rstrip("/")
    code_pfx = (code or "")[:10]
    state_present = bool(state)
    if error:
        if state:
            pop_state(sess, state)
        return frontend_pages.render_message_page(root_path,
            title="未完成授权", tone="warning", status_code=200, action="onboard",
            message="卖家未授予访问权限。准备好后，请重新获取授权链接。")
    if not code:
        return frontend_pages.render_message_page(root_path,
            title="缺少授权码", tone="warning", status_code=400, action="onboard",
            message="此地址用于接收 TikTok 的授权结果。请从店铺注册台重新开始授权。")
    try:
        out = complete_tiktok_authorization(
            sess, code=code, state=state or "", callback_app_key=callback_app_key,
        )
    except OAuthFlowError as exc:
        log.warning(
            "oauth callback rejected: kind=%s code_prefix=%s state_present=%s msg=%s",
            exc.kind, code_pfx, state_present, exc.message,
        )
        # Only known public descriptions cross the presentation boundary. The
        # exception message may contain an upstream payload; it is not page data.
        messages = {
            "state_reused": "此链接已使用，请重新获取授权链接。",
            "state_invalid": "此链接无效或已过期，请重新获取授权链接。",
            "configuration_changed": "店铺 App 配置已变化，请重新获取授权链接。",
            "app_key_mismatch": "回调应用与该店铺的 App 配置不一致，请核对后重新授权。",
            "user_type": "请使用目标 TikTok Shop 店铺的卖家账号授权。",
            "shop_mismatch": "授权店铺与目标店铺不一致，未更新店铺。请核对后重新授权。",
            "missing_shop_cipher": "授权结果缺少必要店铺信息，请重新授权或联系管理员。",
        }
        return frontend_pages.render_message_page(root_path,
            title="授权失败", tone="danger", status_code=400, action="onboard",
            message=messages.get(exc.kind, "授权结果未通过校验，请重新授权或联系管理员。"))
    except UpstreamHttpError as exc:
        log.error(
            "oauth callback upstream failure: code_prefix=%s upstream_code=%s %s",
            code_pfx, getattr(exc, "upstream_code", None), exc,
        )
        return frontend_pages.render_message_page(root_path,
            title="授权失败", tone="danger", status_code=502, action="onboard",
            message="上游令牌交换失败。一次性授权码已使用，请重新获取授权链接。")
    except ProxyError as exc:
        log.error("oauth callback proxy failure: code_prefix=%s %s", code_pfx, exc)
        return frontend_pages.render_message_page(root_path,
            title="授权失败", tone="danger", status_code=502, action="onboard",
            message="授权服务暂时不可用，请重新获取授权链接或联系管理员。")

    def displayed(value: Any) -> str:
        # Never stringify nested objects from the result into browser content.
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value) if isinstance(value, (str, int, float)) else "—"

    sections = []
    for shop in out.get("shops") or []:
        log.info(
            "oauth callback: shop=%s authorized (html) credential_id=%s account_id=%s",
            shop.get("shop_id"), shop.get("credential_id"), shop.get("account_id"),
        )
        facts = [{"label": label, "value": displayed(shop.get(key))} for label, key in (
            ("店铺 ID", "shop_id"), ("店铺名称", "account_name"), ("地区", "region"),
            ("卖家类型", "seller_type"), ("凭据记录 ID", "credential_id"),
            ("店铺记录 ID", "account_id"), ("访问令牌到期时间", "expires_at"),
        )]
        scopes = shop.get("granted_scopes")
        facts.append({"label": "授权范围", "value": ", ".join(
            scope for scope in scopes if isinstance(scope, str)) if isinstance(scopes, list) else "—"})
        sections.append({"title": f"店铺 {displayed(shop.get('shop_id'))}", "facts": facts})
    return frontend_pages.render_message_page(root_path,
        title="店铺授权成功", tone="success", status_code=200,
        message="店铺已完成授权。同步任务会在下次运行时自动接管。", sections=sections)


@router.get("/onboard", response_class=HTMLResponse, summary="新店授权控制台页 (HTML)")
def onboard_page(request: Request) -> HTMLResponse:
    """Readonly instructions; the existing shop workbench owns authorization."""
    return frontend_pages.render_oauth_onboard_page(request)
