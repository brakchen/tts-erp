"""Browser-session cookie helpers + login throttle (tts-erp v2).

Converts an API key into a stateless HMAC-signed session cookie so a
browser can navigate the operator pages. Only the key's SHA-256 hash is
stored in the cookie — never the plaintext key. ``AuthMiddleware``
re-validates the hash against ``security.api_keys`` on every request
(shared TTL cache), so disabling/expiring a key kills its sessions
within one cache TTL.

Env:
- ``TTS_ERP_SESSION_SECRET``   required — generate once with
  ``openssl rand -hex 32`` and store 0600 in .env
- ``TTS_ERP_SESSION_TTL``      seconds; default 43200 (12 h, fixed)
- ``TTS_ERP_SESSION_SECURE``   ``1`` default; set ``0`` for local http dev
- ``TTS_ERP_LOGIN_RATE_LIMIT`` login attempts/min per client; default 10

Design: tech-doc/browser-login-design.md
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

from tts_erp_v2.middleware.rate_limit import SlidingWindow

SESSION_COOKIE_NAME = "tts_session"
SESSION_TTL_DEFAULT_S = 12 * 3600
LOGIN_RATE_LIMIT_DEFAULT = 10

_login_limiter: SlidingWindow | None = None


def _env_session_secret() -> str | None:
    return os.environ.get("TTS_ERP_SESSION_SECRET") or None


def session_secret_configured() -> bool:
    """True when a signing secret is configured (login can mint cookies)."""
    return _env_session_secret() is not None


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


def _encode_payload(payload: dict) -> str:
    return (
        base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode())
        .decode()
        .rstrip("=")
    )


def _sign(raw: str) -> str:
    secret = _env_session_secret()
    if not secret:
        raise RuntimeError("TTS_ERP_SESSION_SECRET not configured")
    return hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()


def mint_session_cookie(key: str, role: str, now: float | None = None) -> str:
    """Return the signed session-cookie value for an API key."""
    payload = {
        "kh": hashlib.sha256(key.encode()).hexdigest(),
        "role": role,
        "exp": int(now if now is not None else time.time()) + session_ttl_seconds(),
    }
    raw = _encode_payload(payload)
    return f"{raw}.{_sign(raw)}"


def verify_session_cookie(value: str) -> dict | None:
    """Verify HMAC signature + expiry. Returns ``{kh, role, exp}`` or None."""
    secret = _env_session_secret()
    if not secret:
        return None
    raw, dot, sig = value.partition(".")
    if not dot or not raw or not sig:
        return None
    expected = hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return None
    try:
        payload = json.loads(
            base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode()
        )
    except Exception:  # noqa: BLE001 — malformed cookie bytes; treat as invalid
        return None
    if not isinstance(payload, dict):
        return None
    kh = payload.get("kh")
    role = payload.get("role")
    if not isinstance(kh, str) or len(kh) != 64:
        return None
    # Lazy import: middleware.auth imports this module, so a module-level
    # import would be circular.
    from tts_erp_v2.middleware.auth import ROLE_LEVEL

    if not isinstance(role, str) or role not in ROLE_LEVEL:
        return None
    try:
        exp = int(payload.get("exp", 0))
    except (TypeError, ValueError):
        return None
    if exp <= time.time():
        return None
    return {"kh": kh, "role": role, "exp": exp}


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
