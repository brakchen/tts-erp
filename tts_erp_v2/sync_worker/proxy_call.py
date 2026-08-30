"""``proxy_call`` adapter for TikTok incremental jobs.

Each :mod:`tts_erp_v2.jobs.tiktok` module receives a callable of the
shape ``proxy_call(method, path, *, body=None) -> dict`` — this module
is the one place that turns that callable into a real signed HTTPS
request to TikTok Shop Open API.

Why this adapter exists
-----------------------
* :class:`tts_erp_v2.proxy.tts_shop.client.TiktokShopClient` is the
  only HTTP caller we ship — it owns HMAC signing + ``shop_cipher``
  injection + 429/5xx retry. Jobs must NOT re-implement that.
* Jobs receive an open SQLAlchemy ``Session`` (not a request-scoped
  state), so the adapter resolves the long-lived
  ``Credentials`` row + ``TiktokShopClient`` once per call.
* The ``TiktokShopClient`` is **cached per ``app_key``** so we don't
  re-build signing state on every page request. The cache lives in
  process memory only — fine because ``app_key`` is a single-tenant
  constant read from ``.env``.

Failure surface
---------------
* Missing ``TIKTOK_APP_KEY`` / ``TIKTOK_APP_SECRET`` →
  :class:`RuntimeError` with the exact env var name. The job's
  :func:`run_with_sync_job` wrapper turns this into a
  ``sync_jobs.status='failed'`` row.
* No ``Credentials`` row for ``(provider, shop_id)`` →
  :class:`RuntimeError`. Same wrapping.
* TikTok-side errors (``106001 invalid sign`` / 401 / 429) are raised
  by the underlying :class:`TiktokShopClient` /
  :mod:`tts_erp_v2.proxy.errors` and bubble out so the job's
  ``sync_jobs.status='failed'`` row records them.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable

from sqlalchemy.orm import Session

from tts_erp_v2.proxy.token_service import load_credentials
from tts_erp_v2.proxy.tts_shop.client import (
    DEFAULT_API_HOST,
    TiktokShopClient,
)

log = logging.getLogger("tts_erp_v2.sync_worker.proxy_call")

#: Body keys that TikTok 202309 expects in the **query string**, never
#: in the POST body. Sending them in the body yields 36009004
#: ("PageSize is a required field").
QUERY_STRING_KEYS: frozenset[str] = frozenset(
    {
        "page_size",
        "sort_field",
        "sort_order",
        "page_token",
        # v2 job convention: they put ``next_page_token`` in base_body;
        # TikTok wants ``page_token`` in the query string.
        "next_page_token",
    }
)

#: Pagination key v2 jobs use in their body → TikTok's query name.
_TOKEN_ALIAS = {"next_page_token": "page_token"}


def _resolve_app_credentials() -> tuple[str, str, str]:
    """Read ``TIKTOK_APP_KEY`` / ``TIKTOK_APP_SECRET`` / ``TIKTOK_API_HOST``.

    Returns ``(app_key, app_secret, api_host)``. Raises if either of the
    secret-bearing vars is missing — we never fall back to empty strings,
    which would silently sign requests with an empty key and produce
    bogus 106001 errors upstream.
    """
    app_key = os.environ.get("TIKTOK_APP_KEY", "").strip()
    app_secret = os.environ.get("TIKTOK_APP_SECRET", "").strip()
    api_host = os.environ.get("TIKTOK_API_HOST", "").strip() or DEFAULT_API_HOST
    if not app_key or not app_secret:
        raise RuntimeError(
            "TIKTOK_APP_KEY and TIKTOK_APP_SECRET are not configured in "
            "the sync-worker environment (set them in /home/schan/tts-erp/.env; "
            "the systemd unit's EnvironmentFile= forwards them at start)."
        )
    return app_key, app_secret, api_host


# Process-wide cache of TiktokShopClient keyed by app_key. Single-tenant
# today (one TikTok app per deployment), but the keyed shape makes it
# trivial to extend when/if multiple apps appear.
_CLIENT_CACHE: dict[str, TiktokShopClient] = {}


def _get_client(app_key: str, app_secret: str, api_host: str) -> TiktokShopClient:
    cached = _CLIENT_CACHE.get(app_key)
    if cached is not None:
        return cached
    client = TiktokShopClient(app_key=app_key, app_secret=app_secret, api_host=api_host)
    _CLIENT_CACHE[app_key] = client
    return client


def build_proxy_call(
    session: Session,
    *,
    shop_id: str,
) -> Callable[..., dict]:
    """Return a ``proxy_call(method, path, *, body=None) -> dict`` closure.

    The closure resolves the shop's OAuth token via
    :func:`load_credentials` on every call — tokens can refresh
    out-of-band (token.refresh job) and we don't want to keep a stale
    in-process copy. The :class:`TiktokShopClient` itself IS cached
    (signing state only, no per-shop secrets).
    """
    app_key, app_secret, api_host = _resolve_app_credentials()
    client = _get_client(app_key, app_secret, api_host)

    def proxy_call(method: str, path: str, *, body: dict | None = None) -> dict:
        view = load_credentials(session, "tiktok", shop_id)
        if view is None:
            raise RuntimeError(
                f"credentials row not found for provider=tiktok "
                f"external_account_id={shop_id!r}; re-authorize via /callback"
            )

        # AGENTS.md §2.3: shop_cipher is always in the query string.
        extra_params: dict[str, str] = {"shop_cipher": view.shop_cipher or ""}
        # TikTok 202309: page_size / sort_* / page_token live in the
        # QUERY STRING. Lift them out of the job-provided body; everything
        # else (filters / ids) stays in the body.
        request_body: dict[str, object] = {}
        for key, value in (body or {}).items():
            if key in QUERY_STRING_KEYS:
                query_key: str = key
                if key in _TOKEN_ALIAS:
                    query_key = _TOKEN_ALIAS[key]
                extra_params[query_key] = str(value)
            else:
                request_body[key] = value
        access_token = view.access_token

        if method.upper() == "POST":
            result = client.post(
                path=path,
                access_token=access_token,
                body=request_body or None,
                extra_params=extra_params,
            )
        elif method.upper() == "GET":
            result = client.get(
                path=path,
                access_token=access_token,
                extra_params=extra_params,
            )
        else:
            raise ValueError(
                f"unsupported HTTP method for proxy_call: {method!r} "
                f"(path={path!r}, shop_id={shop_id!r})"
            )
        return result.payload

    return proxy_call


def _reset_for_testing() -> None:
    """Drop the client cache (test helper)."""
    _CLIENT_CACHE.clear()


__all__ = ["build_proxy_call"]
