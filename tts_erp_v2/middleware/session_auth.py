"""Browser-session cookie helpers + login throttle (tts-erp v2).

会话 cookie = 不透明随机 token（``v2.<token>``），权威状态在
``security.user_sessions``（只存 sha256(token)）。``AuthMiddleware``
每请求回库复查会话与用户状态，所以登出/禁用/改密即时生效。

同域多服务部署（设计 §5.2）：cookie 专属名 + Path=root_path 严格隔离。

Env:
- ``TTS_ERP_SESSION_TTL``      seconds; default 43200 (12 h, fixed)
- ``TTS_ERP_SESSION_SECURE``   ``1`` default; set ``0`` for local http dev
- ``TTS_ERP_LOGIN_RATE_LIMIT`` login attempts/min per client; default 10

Design: tech-doc/user-account-authz-design.md
"""

from __future__ import annotations

import os

from tts_erp_v2.middleware.rate_limit import SlidingWindow

# 专属命名（防同域其他服务的 cookie 撞名）；旧名 tts_session 的
# api-key-hmac 会话一律视为未登录（不做过渡兼容）。
SESSION_COOKIE_NAME = "tts_erp_session"
SESSION_TTL_DEFAULT_S = 12 * 3600
LOGIN_RATE_LIMIT_DEFAULT = 10

_login_limiter: SlidingWindow | None = None


def session_ttl_seconds() -> int:
    raw = os.environ.get("TTS_ERP_SESSION_TTL")
    try:
        return max(300, int(raw)) if raw else SESSION_TTL_DEFAULT_S
    except ValueError:
        return SESSION_TTL_DEFAULT_S


def session_secure_flag() -> bool:
    """Secure cookie flag — on by default; off only for local http dev."""
    raw = os.environ.get("TTS_ERP_SESSION_SECURE", "1")
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def cookie_path(root_path: str | None) -> str:
    """Cookie Path = 外部挂载前缀（生产 /tts）；绝不放宽到 /。

    登出删 cookie 必须用完全相同的 Path，否则删不掉。
    """
    return root_path or "/"


# ------------------------------------------------------------- login throttle


def _env_login_limit() -> int:
    raw = os.environ.get("TTS_ERP_LOGIN_RATE_LIMIT")
    try:
        return max(1, int(raw)) if raw else LOGIN_RATE_LIMIT_DEFAULT
    except ValueError:
        return LOGIN_RATE_LIMIT_DEFAULT


def login_throttle_hit(client_key: str) -> int | None:
    """Count one login attempt; return Retry-After seconds when over budget.

    Separate from the request rate limiter: the login endpoint is exempt
    from auth, so ``RateLimitMiddleware`` passes anonymous requests
    through unthrottled — without this, the login form is a free
    brute-force target.
    """
    global _login_limiter
    if _login_limiter is None:
        _login_limiter = SlidingWindow(_env_login_limit())
    return _login_limiter.hit(client_key)


def reset_login_throttle(limit: int | None = None) -> None:
    """Test helper: drop the login limiter (optionally with a new limit)."""
    global _login_limiter
    _login_limiter = None
    if limit is not None:
        _login_limiter = SlidingWindow(limit)
