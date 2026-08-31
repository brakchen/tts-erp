"""TikTok Shop auth-token refresh (refresh_token grant).

This module owns the **outbound** call to TikTok's
``/api/v2/token/refresh`` endpoint. It is the single seam through
which ``tts_erp_v2.proxy.token_service.refresh_if_needed`` reaches the
upstream OAuth provider for ``provider='tiktok'``.

Why this lives in its own module
--------------------------------
* The refresh path is provider-specific (different URL, different
  envelope, different error codes from the data API endpoints).
* Concentrating the HTTP plumbing here means
  :mod:`tts_erp_v2.proxy.token_service` stays provider-agnostic and
  the proxy_call / sync-worker layers never have to import urllib or
  http.client.
* The Miaoshou refresh path is intentionally NOT wired here (per
  AGENTS.md §10.2, v2 has no Miaoshou refresh implementation yet).
  ``build_token_registry`` returns a no-op refresher for the
  ``"miaoshou"`` provider so the per-row loop in
  :mod:`tts_erp_v2.jobs.token_refresh` still completes without
  crashing the whole tick.

Contract
--------
* :func:`refresh_tiktok_token` performs one GET to the upstream
  refresh endpoint and returns the new envelope as a dict.
* :func:`build_token_registry` returns a callable of shape
  ``(provider, external_account_id) -> refresher`` so the scheduler
  can wire it into ``sync_token_refresh(registry=...)``.
"""

from __future__ import annotations

import http.client
import json
import logging
import os
import urllib.parse
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from tts_erp_v2.proxy.errors import SigningError, UpstreamHttpError
from tts_erp_v2.proxy.token_service import load_credentials

log = logging.getLogger("tts_erp_v2.proxy.tiktok_auth")

# Default refresh endpoint host. Overridable for tests.
DEFAULT_TIKTOK_AUTH_HOST = "https://auth.tiktok-shops.com"
REFRESH_PATH = "/api/v2/token/refresh"

# Cap on the upstream response body we keep for diagnostics.
_BODY_PREVIEW_CHARS = 300

RefresherFn = Callable[[str, str], dict]


def _resolve_app_credentials() -> tuple[str, str, str]:
    """Read TIKTOK_APP_KEY / TIKTOK_APP_SECRET / TIKTOK_AUTH_HOST.

    Returns ``(app_key, app_secret, auth_host)``. Raises
    :class:`SigningError` if either of the secret-bearing vars is
    missing — we never fall back to empty strings (which would produce
    bogus ``98001004 invalid params`` upstream).
    """
    app_key = os.environ.get("TIKTOK_APP_KEY", "").strip()
    app_secret = os.environ.get("TIKTOK_APP_SECRET", "").strip()
    auth_host = (
        os.environ.get("TIKTOK_AUTH_HOST", "").strip() or DEFAULT_TIKTOK_AUTH_HOST
    )
    if not app_key or not app_secret:
        raise SigningError(
            "TIKTOK_APP_KEY and TIKTOK_APP_SECRET are not configured in the "
            "sync-worker environment (set them in /home/schan/tts-erp/.env; "
            "the systemd unit's EnvironmentFile= forwards them at start)."
        )
    return app_key, app_secret, auth_host


def _validate_scheme(url: str) -> tuple[str, str]:
    """Split ``url`` into ``(host, target_path)``.

    Refuses non-http(s) URLs before http.client touches them — the
    same defense the rest of the proxy layer uses (see
    :mod:`tts_erp_v2.proxy.tts_shop.client`).
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise SigningError(
            f"refused non-http(s) URL scheme for token refresh: "
            f"scheme={parsed.scheme!r} (url={url[:120]!r})"
        )
    host = parsed.hostname or ""
    if not host:
        raise SigningError(f"missing host in refresh URL: {url[:120]!r}")
    return host, parsed.hostname and (parsed.path or "/") or "/"


def refresh_tiktok_token(*, refresh_token: str) -> dict[str, Any]:
    """Exchange a TikTok refresh_token for a fresh access_token.

    Args:
        refresh_token: the *current* ``refresh_token`` for the shop.
            The caller (typically the registry built by
            :func:`build_token_registry`) looks this up from the
            encrypted :class:`integration.credentials` row.

    Returns:
        Dict with at minimum ``access_token``; usually also
        ``refresh_token`` (rotated), ``shop_cipher`` (rotated), and
        ``expires_at`` (UTC datetime derived from ``expires_in``).

    Raises:
        SigningError: missing app_key/app_secret or non-http(s) URL.
        UpstreamHttpError: HTTP 4xx/5xx, or ``code != 0`` in the body.
    """
    app_key, app_secret, auth_host = _resolve_app_credentials()

    query = {
        "app_key": app_key,
        "app_secret": app_secret,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    qs = urllib.parse.urlencode(query)
    full_url = f"{auth_host.rstrip('/')}{REFRESH_PATH}?{qs}"

    # Scheme allowlist (must come BEFORE http.client is invoked).
    parsed = urllib.parse.urlparse(full_url)
    if parsed.scheme not in ("http", "https"):
        raise SigningError(
            f"refused non-http(s) URL scheme for token refresh: "
            f"scheme={parsed.scheme!r}"
        )
    host = parsed.hostname or ""
    if not host:
        raise SigningError(f"missing host in refresh URL: {full_url[:120]!r}")

    conn_factory = (
        http.client.HTTPSConnection
        if parsed.scheme == "https"
        else http.client.HTTPConnection
    )
    conn = conn_factory(host, parsed.port, timeout=30)
    try:
        target_path = parsed.path or "/"
        if parsed.query:
            target_path = f"{target_path}?{parsed.query}"
        conn.request(
            "GET", target_path, body=None, headers={"User-Agent": "tts-erp-v2/1.0"}
        )
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8", errors="replace")
        status = resp.status
    finally:
        conn.close()

    if not (200 <= status < 300):
        raise UpstreamHttpError(
            status,
            f"HTTP {status} from token refresh endpoint",
            body_preview=raw[:_BODY_PREVIEW_CHARS],
        )

    try:
        body = json.loads(raw)
    except json.JSONDecodeError as e:
        raise UpstreamHttpError(
            status,
            f"invalid JSON from token refresh endpoint: {e}",
            body_preview=raw[:_BODY_PREVIEW_CHARS],
        ) from e

    code = body.get("code", -1)
    if code != 0:
        raise UpstreamHttpError(
            status,
            f"tiktok refresh code={code} message={body.get('message')!r}",
            body_preview=str(body)[:_BODY_PREVIEW_CHARS],
            upstream_code=code,
        )

    data = body.get("data") or {}
    access_token = data.get("access_token") or ""
    if not access_token:
        raise UpstreamHttpError(
            status,
            "tiktok refresh returned data without access_token",
            body_preview=str(body)[:_BODY_PREVIEW_CHARS],
            upstream_code=code,
        )

    expires_in = data.get("expires_in")
    expires_at: datetime | None = None
    if isinstance(expires_in, (int, float)) and expires_in > 0:
        expires_at = datetime.now(timezone.utc).fromtimestamp(
            datetime.now(timezone.utc).timestamp() + float(expires_in),
            tz=timezone.utc,
        )

    return {
        "access_token": access_token,
        "refresh_token": data.get("refresh_token"),
        "shop_cipher": data.get("shop_cipher"),
        "expires_at": expires_at,
    }


# ─── Refresher registry ──────────────────────────────────────────────


def _tiktok_refresher(
    session_factory: sessionmaker[Session],
) -> Callable[[str, str], RefresherFn]:
    """Build a registry that wires the real TikTok refresher for
    ``provider='tiktok'``.

    The returned callable has signature
    ``(provider, external_account_id) -> refresher`` where the inner
    ``refresher(provider, external_account_id)`` does the actual
    HTTP call. We need the closure shape (vs. a flat dict) so the
    scheduler can call ``registry(provider, eid)`` per row in
    :mod:`tts_erp_v2.jobs.token_refresh`.
    """

    def registry(provider: str, external_account_id: str) -> RefresherFn:
        if provider != "tiktok":
            # Miaoshou + anything else: return a no-op refresher that
            # the caller treats as 'skip me' (empty access_token).
            def _noop(p: str, eid: str) -> dict[str, Any]:
                log.warning(
                    "token_refresh: no refresher for provider=%s eid=%s; "
                    "leaving token unchanged",
                    p,
                    eid,
                )
                return {"access_token": ""}

            return _noop

        def _refresher(p: str, eid: str) -> dict[str, Any]:
            # Read the current refresh_token from the encrypted row.
            session = session_factory()
            try:
                view = load_credentials(session, p, eid)
                if view is None or not view.refresh_token:
                    log.warning(
                        "token_refresh: tiktok eid=%s has no refresh_token; skipping",
                        eid,
                    )
                    return {"access_token": ""}
                current_rt = view.refresh_token
            finally:
                session.close()
            return refresh_tiktok_token(refresh_token=current_rt)

        return _refresher

    return registry


def build_token_registry(
    *,
    session_factory: sessionmaker[Session] | None = None,
) -> Callable[[str, str], RefresherFn]:
    """Build the per-provider refresher registry used by the scheduler.

    Args:
        session_factory: SQLAlchemy ``sessionmaker`` used to read the
            current ``refresh_token`` from the encrypted credentials
            row. If None, defers to
            :func:`tts_erp_v2.db.base.get_session_factory` (so daemon
            mode picks up the production sessionmaker).

    Returns:
        Callable ``(provider, external_account_id) -> refresher``,
        where ``refresher(provider, external_account_id) -> dict``
        is what ``refresh_if_needed`` invokes.

    Provider dispatch:
        * ``"tiktok"``  → real HTTP refresh via
          :func:`refresh_tiktok_token`
        * ``"miaoshou"`` (and anything else) → no-op refresher that
          returns ``{"access_token": ""}`` (signals 'skip me' to
          :func:`refresh_if_needed`)
    """
    if session_factory is None:
        from tts_erp_v2.db.base import get_session_factory

        session_factory = get_session_factory()
    return _tiktok_refresher(session_factory)


__all__ = [
    "DEFAULT_TIKTOK_AUTH_HOST",
    "REFRESH_PATH",
    "build_token_registry",
    "refresh_tiktok_token",
]
