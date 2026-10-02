"""Thin ASGI adapter for the access policy module.

The adapter translates ASGI scope values into the public access interface,
publishes the resulting grant for downstream callers, and renders typed
access decisions. Route policy, credential lookup, caching, and decision
ordering live in :mod:`tts_erp_v2.access`.
"""

from __future__ import annotations

import json
import os
import sys

from tts_erp_v2.access import (
    AccessEffect,
    AccessRequest,
    AuthMode,
    DeploymentPathInput,
    Role,
    authenticate_hash,
    authenticate_key,
    canonicalize_path,
    clear_credential_cache,
    evaluate_access,
)
from tts_erp_v2.access import required_role as _required_role
from tts_erp_v2.access._context import user_label_var, user_pages_var

# Compatibility exports for existing handler helpers and test fixtures.
ROLE_LEVEL = {role.value: role.level for role in Role}
_LEVEL_NAME = {value: name for name, value in ROLE_LEVEL.items()}


def clear_cache() -> None:
    clear_credential_cache()


def required_role(method: str, path: str) -> int | None:
    role = _required_role(method, path)
    return role.level if role is not None else None


def lookup_role_by_hash(key_hash: str) -> tuple[int, tuple[str, ...]] | None:
    credential = authenticate_hash(key_hash)
    if credential is None:
        return None
    return credential.role.level, credential.scopes


def lookup_role(key: str) -> tuple[int, tuple[str, ...]] | None:
    credential = authenticate_key(key)
    if credential is None:
        return None
    return credential.role.level, credential.scopes


def _extract_keys(scope: dict) -> tuple[str | None, str | None]:
    """Return Bearer and X-API-Key values with deterministic precedence."""

    bearer: str | None = None
    api_key: str | None = None
    for header_name, header_value in scope.get("headers") or []:
        name = header_name.decode("latin-1").lower()
        value = header_value.decode("latin-1")
        if name == "authorization" and bearer is None:
            scheme, _, token = value.partition(" ")
            if scheme.lower() == "bearer" and token.strip():
                bearer = token.strip()
        elif name == "x-api-key" and api_key is None and value.strip():
            api_key = value.strip()
    return bearer, api_key


def _extract_cookie(scope: dict, name: str) -> str | None:
    target = name.encode("latin-1")
    for header_name, header_value in scope.get("headers") or []:
        if header_name.lower() != b"cookie":
            continue
        for segment in header_value.decode("latin-1").split(";"):
            key, separator, value = segment.strip().partition("=")
            if separator and key.encode("latin-1") == target:
                return value.strip()
    return None


def _accept_text_html(scope: dict) -> bool:
    for header_name, header_value in scope.get("headers") or []:
        if header_name.lower() == b"accept":
            return "text/html" in header_value.decode("latin-1").lower()
    return False


def _prefix_of(key: str | None) -> str:
    return key[:16] if key else "-"


def _deny_response(
    status: int, message: str, *, accepts_html: bool = False
) -> tuple[int, list[tuple[bytes, bytes]], bytes]:
    if accepts_html and status == 403:
        # 浏览器页面请求的 403：返回友好 HTML（API 调用仍收 JSON）。
        body = _FORBIDDEN_HTML.format(
            message=_escape_html(message),
        ).encode()
        headers: list[tuple[bytes, bytes]] = [
            (b"content-type", b"text/html; charset=utf-8")
        ]
        return status, headers, body
    body = json.dumps({"detail": message}).encode()
    headers = [(b"content-type", b"application/json")]
    if status == 401:
        headers.append((b"www-authenticate", b"Bearer"))
    return status, headers, body


def _escape_html(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


_FORBIDDEN_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>403 · 无权访问</title>
<style>
  body {{ margin: 0; font-family: ui-monospace, monospace; background: #F4EFE4; color: #1B1814; }}
  .box {{ max-width: 520px; margin: 12vh auto; padding: 2.5rem; background: #EAE3D2;
         border: 1px solid #C9BFA8; box-shadow: 4px 4px 0 #C9BFA8; }}
  h1 {{ font-size: 2.2rem; margin: 0 0 0.5rem; }}
  .code {{ color: #B8390E; font-size: 0.85rem; letter-spacing: 0.12em; text-transform: uppercase; }}
  p {{ line-height: 1.7; margin: 0.6rem 0 1.4rem; }}
  a {{ color: #B8390E; text-decoration: none; border-bottom: 1px solid #B8390E; }}
</style>
</head>
<body>
  <div class="box">
    <div class="code">HTTP 403</div>
    <h1>无权访问该页面</h1>
    <p>{message}</p>
    <p><a href="./login">返回登录页</a></p>
  </div>
</body>
</html>
"""


def _auth_mode() -> AuthMode:
    raw = os.environ.get("TTS_ERP_AUTH_MODE", "off")
    try:
        return AuthMode(raw)
    except ValueError:
        sys.stderr.write(
            f"[auth] invalid TTS_ERP_AUTH_MODE={raw!r}; failing closed as enforce\n"
        )
        return AuthMode.ENFORCE


class AuthMiddleware:
    """Translate ASGI requests and typed access decisions."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        canonical = canonicalize_path(
            DeploymentPathInput(
                path=scope.get("path", ""),
                raw_path=scope.get("raw_path"),
                root_path=scope.get("root_path", ""),
            )
        )
        scope["path"] = canonical.downstream_path
        if canonical.downstream_raw_path is not None:
            scope["raw_path"] = canonical.downstream_raw_path

        from tts_erp_v2.middleware import rate_limit, session_auth

        bearer_key, api_key = _extract_keys(scope)
        client = str((scope.get("client") or ("?",))[0])
        decision = await evaluate_access(
            AccessRequest(
                method=scope["method"],
                route_path=canonical.route_path,
                accepts_html=_accept_text_html(scope),
                client_ip=client,
                session_cookie=_extract_cookie(
                    scope, session_auth.SESSION_COOKIE_NAME
                ),
                bearer_key=bearer_key,
                api_key=api_key,
            ),
            mode=_auth_mode(),
        )

        grant = decision.grant
        scope["access_grant"] = grant
        scope["api_key_hash"] = grant.key_hash
        scope["api_key_role"] = grant.role.value if grant.role else None
        scope["api_key_scopes"] = grant.scopes
        scope["auth_method"] = grant.auth_method
        # 会话用户上下文（设计 §5.2）：user_id / username / pages 供 handler、
        # 侧边栏渲染（ContextVar）与审计使用。
        if grant.user is not None:
            scope["user_id"] = grant.user.user_id
            scope["username"] = grant.user.username
            scope["user_pages"] = grant.user.pages
            user_pages_var.set(grant.user.pages)
            user_label_var.set(
                grant.user.display_name or grant.user.username
            )
        else:
            scope["user_id"] = None
            scope["username"] = None
            scope["user_pages"] = None
            user_pages_var.set(None)
            user_label_var.set(None)

        attempted_key = bearer_key or api_key
        if decision.effect in {AccessEffect.ALLOW, AccessEffect.SHADOW_ALLOW}:
            if decision.effect is AccessEffect.SHADOW_ALLOW:
                sys.stderr.write(
                    f"[auth-shadow] would-deny {decision.status} "
                    f"{scope['method']} {scope['path']} from {client} "
                    f"key_prefix={_prefix_of(attempted_key)}\n"
                )
            await self.app(scope, receive, send)
            return

        if decision.effect is AccessEffect.RATE_LIMITED:
            retry_after = decision.retry_after or 1
            limit = decision.rate_limit or rate_limit.shared_counter().limit
            sys.stderr.write(
                f"[rate-limit] 429 (auth-denied) path={scope['path']} "
                f"retry_after={retry_after}s\n"
            )
            status, headers, body = rate_limit.too_many_response(
                limit, retry_after
            )
            await send(
                {"type": "http.response.start", "status": status, "headers": headers}
            )
            await send({"type": "http.response.body", "body": body})
            return

        if decision.effect is AccessEffect.REDIRECT:
            query = scope.get("query_string", b"").decode("latin-1")
            next_value = canonical.route_path + (("?" + query) if query else "")
            location = f"{canonical.root_path}/v2/auth/login?next={next_value}"
            await send(
                {
                    "type": "http.response.start",
                    "status": 302,
                    "headers": [
                        (b"location", location.encode("latin-1")),
                        (b"content-type", b"text/plain; charset=utf-8"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": b""})
            return

        status = decision.status or 503
        detail = decision.detail or "auth store unavailable"
        sys.stderr.write(
            f"[auth] denied {status} {scope['method']} {scope['path']} "
            f"from {client} key_prefix={_prefix_of(attempted_key)}\n"
        )
        response_status, headers, body = _deny_response(
            status, detail, accepts_html=_accept_text_html(scope)
        )
        await send(
            {
                "type": "http.response.start",
                "status": response_status,
                "headers": headers,
            }
        )
        await send({"type": "http.response.body", "body": body})
