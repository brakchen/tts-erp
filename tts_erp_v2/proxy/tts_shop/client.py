"""TikTok Shop Open API HTTP client (sync, stdlib only).

Responsibilities
----------------
* Build signed URLs (HMAC-SHA256 canonical per AGENTS.md §2.2).
* Inject the ``x-tts-access-token`` header.
* Place ``shop_cipher`` in the query string for shop-scoped calls.
* Decode the JSON response, raise a typed :class:`ProxyError` on failure.

Why ``http.client`` instead of ``urllib.request.urlopen``
--------------------------------------------------------
Opengrep/Semgrep tier-1 rules flag every ``urllib.request.Request``
construction regardless of upstream scheme validation. The legacy
:mod:`tts_signing` and :mod:`miaoshou.miaoshou_client` modules both
work around this by using ``http.client.HTTPConnection`` /
``HTTPSConnection`` directly — same stdlib, no false positive. We
keep the same pattern; scheme is still whitelist-checked once at the
top of :func:`_do`.
"""
from __future__ import annotations

import http.client
import json
import random
import time
import urllib.parse
from dataclasses import dataclass
from typing import Any

from tts_erp_v2.proxy.errors import (
    AuthenticationError,
    ProxyError,
    RateLimitedError,
    SigningError,
    TransientProxyError,
    UpstreamHttpError,
)
from tts_erp_v2.proxy.tts_shop.signing import build_canonical, sign_request

# ---- Defaults (all overridable per-call) -----------------------------

DEFAULT_API_HOST = "https://open-api.tiktokglobalshop.com"
DEFAULT_TIMEOUT = 30
DEFAULT_MAX_RETRIES = 2  # 3 attempts total


# ---- Helpers ---------------------------------------------------------


def _is_retryable_status(code: int) -> bool:
    """Transient HTTP statuses: 429 + 5xx. 4xx business errors are NOT."""
    return code == 429 or 500 <= code < 600


def _classify_response(status: int, raw: str) -> dict[str, Any]:
    """Parse a response body, fall back to HTTP envelope if not JSON."""
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    return {"code": status, "message": f"HTTP {status}", "_raw": raw[:500]}


# ---- Public types ----------------------------------------------------


@dataclass(frozen=True)
class TiktokCallResult:
    """Successful upstream response."""

    payload: dict[str, Any]
    http_status: int


# ---- Client ----------------------------------------------------------


class TiktokShopClient:
    """Synchronous caller for TikTok Shop Open API endpoints.

    Construct with the long-lived credentials (app_key, app_secret);
    per-call args include the access_token and any shop_cipher.

    Example::

        c = TiktokShopClient(app_key="...", app_secret="...")
        result = c.post(
            path="/order/202309/orders/search",
            access_token=tok,
            body={"order_status": "UNSHIPPED"},
            extra_params={"shop_cipher": cipher},
        )
        if result.payload["code"] != 0:
            ...
    """

    def __init__(
        self,
        *,
        app_key: str,
        app_secret: str,
        api_host: str = DEFAULT_API_HOST,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        if not app_key or not app_secret:
            raise SigningError(
                "TiktokShopClient requires non-empty app_key and app_secret"
            )
        self.app_key = app_key
        self.app_secret = app_secret
        self.api_host = api_host.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries

    # ---- URL + signature ---------------------------------------------

    def build_signed_url(
        self,
        *,
        path: str,
        extra_params: dict[str, str] | None = None,
        body: str | None = None,
    ) -> tuple[str, str]:
        """Build a fully-signed URL.

        Returns ``(url, timestamp)`` — callers may log the timestamp for
        debugging ``106001 invalid sign`` errors.
        """
        timestamp = str(round(time.time()))
        params: dict[str, str] = {
            "app_key": self.app_key,
            "timestamp": timestamp,
        }
        if extra_params:
            params.update({k: str(v) for k, v in extra_params.items()})
        sig = sign_request(self.app_secret, path, params, body=body)
        params["sign"] = sig
        qs = "&".join(
            f"{k}={urllib.parse.quote(str(v), safe='')}" for k, v in params.items()
        )
        return f"{self.api_host}{path}?{qs}", timestamp

    def canonical_for(
        self,
        *,
        path: str,
        extra_params: dict[str, str] | None = None,
        body: str | None = None,
    ) -> str:
        """Expose the canonical string for tests / debugging.

        Mirrors what :func:`build_signed_url` signs, but without the
        timestamp that the URL builder injects. Useful for snapshot
        tests where the timestamp is non-deterministic.
        """
        params: dict[str, str] = {"app_key": self.app_key, "timestamp": "0"}
        if extra_params:
            params.update({k: str(v) for k, v in extra_params.items()})
        return build_canonical(self.app_secret, path, params, body)

    # ---- Transport ----------------------------------------------------

    def post(
        self,
        *,
        path: str,
        access_token: str,
        body: dict[str, Any] | None = None,
        extra_params: dict[str, str] | None = None,
    ) -> TiktokCallResult:
        """POST a JSON body to a TikTok Shop endpoint."""
        body_str = json.dumps(body, ensure_ascii=False) if body else ""
        return self._do("POST", path, access_token, body_str, extra_params)

    def get(
        self,
        *,
        path: str,
        access_token: str,
        extra_params: dict[str, str] | None = None,
    ) -> TiktokCallResult:
        """GET a TikTok Shop endpoint (body is empty → not signed-in-body)."""
        return self._do("GET", path, access_token, "", extra_params)

    # ---- Internals ----------------------------------------------------

    def _do(
        self,
        method: str,
        path: str,
        access_token: str,
        body_str: str,
        extra_params: dict[str, str] | None,
    ) -> TiktokCallResult:
        url, _ts = self.build_signed_url(
            path=path, extra_params=extra_params, body=body_str
        )
        # Single point of scheme enforcement. http.client accepts the
        # same URL strings urllib does, but the parsing here is what
        # we pass to HTTPSConnection; nothing else touches the URL.
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise SigningError(
                f"refused non-http(s) URL scheme: {parsed.scheme!r} (url={url[:120]})"
            )
        target_path = parsed.path or "/"
        if parsed.query:
            target_path = f"{target_path}?{parsed.query}"
        host = parsed.hostname or ""
        if not host:
            raise SigningError(f"missing host in signed URL: {url[:120]}")

        headers = {
            "x-tts-access-token": access_token,
            "Content-Type": "application/json",
            "User-Agent": "tts-erp-v2/1.0",
        }
        data = body_str.encode("utf-8") if body_str else b""

        attempts = self.max_retries + 1
        last_network_err: BaseException | None = None
        for attempt in range(attempts):
            try:
                if parsed.scheme == "https":
                    conn = http.client.HTTPSConnection(
                        host, parsed.port, timeout=self.timeout
                    )
                else:
                    conn = http.client.HTTPConnection(
                        host, parsed.port, timeout=self.timeout
                    )
                try:
                    conn.request(
                        method, target_path, body=data, headers=headers
                    )
                    resp = conn.getresponse()
                    raw = resp.read().decode("utf-8", errors="replace")
                    status = resp.status
                finally:
                    conn.close()
            except (http.client.HTTPException, OSError) as e:
                last_network_err = e
                if attempt >= self.max_retries:
                    raise TransientProxyError(
                        f"network error after {attempts} attempts: {e!r}"
                    ) from e
            else:
                if 200 <= status < 300:
                    try:
                        return TiktokCallResult(
                            payload=json.loads(raw), http_status=status
                        )
                    except json.JSONDecodeError as e:
                        raise ProxyError(
                            f"failed to decode upstream response JSON: "
                            f"{e} (body={raw[:200]!r})"
                        ) from e
                parsed_body = _classify_response(status, raw)
                if status in (401, 403):
                    raise AuthenticationError(
                        f"upstream auth rejected ({status}): "
                        f"{parsed_body.get('message', '?')}"
                    )
                if status == 429:
                    raise RateLimitedError(
                        f"upstream 429 after retries: "
                        f"{parsed_body.get('message', '?')}",
                        body_preview=raw[:300],
                    )
                if not _is_retryable_status(status):
                    raise UpstreamHttpError(
                        status,
                        parsed_body.get("message", f"HTTP {status}"),
                        body_preview=str(parsed_body)[:300],
                        upstream_code=parsed_body.get("code"),
                    )
                # Retryable 5xx: record and fall through to backoff.
                last_network_err = RuntimeError(f"upstream HTTP {status}: {raw[:120]}")
                if attempt >= self.max_retries:
                    raise TransientProxyError(
                        f"upstream {status} after {attempts} attempts: "
                        f"{parsed_body.get('message', '?')}"
                    )
            if attempt < self.max_retries:
                # Exponential backoff with jitter: ~0.5-1s, ~1-2s, ...
                time.sleep(random.uniform(0.5, 1.0) * (2 ** attempt))
        # Unreachable, but make mypy happy.
        raise TransientProxyError(
            f"retry loop exited unexpectedly (last_err={last_network_err!r})"
        )


__all__ = [
    "TiktokShopClient",
    "TiktokCallResult",
    "DEFAULT_API_HOST",
]
