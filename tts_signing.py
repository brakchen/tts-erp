"""HMAC-SHA256 signing + TikTok Shop HTTP client for tts-erp.

Reusable: any service that needs to call TikTok Shop Partner API can import
this and get signed requests without re-implementing the canonical-string
rules.

Canonical string format (TikTok Partner API spec):
    canonical = f"{secret}{path}{k1}{v1}{k2}{v2}...{secret}"
where k1, k2, ... are sorted alphabetically by key name.
Then sign = HMAC-SHA256(secret, canonical) -> hex.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


def sign_request(app_secret: str, path: str, query_params: dict[str, str],
                 body: str | None = None) -> str:
    """Compute HMAC-SHA256 signature for a TikTok Partner API call.

    Args:
        app_secret: app secret (used as both HMAC key and signature prefix/suffix)
        path: API path with leading slash, e.g. "/order/202309/orders/search"
        query_params: query string params (must include app_key + timestamp at minimum)
        body: for POST/PUT endpoints, the raw JSON body string (must be the EXACT bytes
              sent on the wire, no extra whitespace). Appended to canonical AFTER kv params.

    Returns:
        hex signature string
    """
    kv_concat = "".join(f"{k}{query_params[k]}" for k in sorted(query_params))
    # TikTok Partner API canonical (verified empirically):
    #   {secret}{path}{kv}{body}{secret}   for POST
    #   {secret}{path}{kv}{secret}        for GET (body=None)
    canonical = f"{app_secret}{path}{kv_concat}{app_secret}"
    if body:
        canonical = f"{app_secret}{path}{kv_concat}{body}{app_secret}"
    sig = hmac.new(
        app_secret.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if os.environ.get("TTS_DEBUG_SIGN") == "1":
        body_repr = repr(body) if body else "None"
        sys.stderr.write(
            f"[tts-erp-debug] path={path}\n"
            f"  kv={kv_concat}\n"
            f"  body={body_repr}\n"
            f"  canonical={canonical!r}\n"
            f"  sig={sig}\n"
        )
    return sig


def build_signed_url(
    api_host: str,
    path: str,
    app_key: str,
    app_secret: str,
    extra_params: dict[str, str] | None = None,
    body: str | None = None,
    timeout: int = 30,
) -> tuple[str, str]:
    """Build a fully-signed URL with timestamp + signature.

    Returns (url, timestamp) so callers can log/debug.
    """
    timestamp = str(int(time.time()))
    params: dict[str, str] = {"app_key": app_key, "timestamp": timestamp}
    if extra_params:
        params.update({k: str(v) for k, v in extra_params.items()})
    sign = sign_request(app_secret, path, params, body=body)
    params["sign"] = sign
    qs = "&".join(f"{k}={urllib.parse.quote(str(v), safe='')}" for k, v in params.items())
    return f"{api_host}{path}?{qs}", timestamp


def tiktok_request(
    method: str,
    api_host: str,
    path: str,
    access_token: str,
    app_key: str,
    app_secret: str,
    body: dict | None = None,
    extra_params: dict[str, str] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    """Call a TikTok Shop API endpoint with full signing + auth headers.

    For GET: body is ignored, extra_params go in query string.
    For POST: body is JSON-encoded, extra_params (like shop_cipher) go in query string.

    Returns parsed JSON response.
    """
    url, ts = build_signed_url(
        api_host, path, app_key, app_secret,
        extra_params=extra_params, timeout=timeout,
    )
    headers = {
        "x-tts-access-token": access_token,
        "Content-Type": "application/json",
        "User-Agent": "tts-erp/1.0 (schan)",
    }

    data = None
    body_str = None
    if method.upper() in ("POST", "PUT", "PATCH"):
        if body is not None:
            body_str = json.dumps(body, ensure_ascii=False)
            data = body_str.encode("utf-8")
        else:
            body_str = ""
            data = b""

    # Re-sign including body if we have a body string to fold in
    if method.upper() in ("POST", "PUT", "PATCH") and body_str is not None:
        url, _ = build_signed_url(
            api_host, path, app_key, app_secret,
            extra_params=extra_params, body=body_str, timeout=timeout,
        )
        req = urllib.request.Request(url, method=method, data=data, headers=headers)
    else:
        req = urllib.request.Request(url, method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8")
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        try:
            return json.loads(body_text)
        except json.JSONDecodeError:
            return {"code": e.code, "message": f"HTTP {e.code}", "_raw": body_text[:500]}
    except urllib.error.URLError as e:
        return {"code": -1, "message": f"network error: {e.reason}"}
    except Exception as e:  # noqa: BLE001
        return {"code": -1, "message": f"{type(e).__name__}: {e}"}


# Convenience: known endpoint paths (so we can validate they're implemented)
ORDER_ENDPOINTS = {
    # Search (the only order-list endpoint in 202309)
    "search":      ("POST", "/order/202309/orders/search"),
    # Detail (note: 202309 uses ?ids= in query, NOT /{order_id} in path)
    "detail":      ("GET",  "/order/202309/orders"),
    # 202309 Order module is READ-ONLY. All write actions (confirm/cancel/ship/etc.)
    # live in Fulfillment or Reverse Logistics modules and return 36009009 here.
}

# Finance / Statement / Payment endpoints (get-statements-202309).
# Confirmed 2026-08-16: only list endpoints work; no detail/sub-records
# endpoints exposed in 202309 (e.g. /statements/{id}/transactions → 36009009).
# sort_field is REQUIRED for both list endpoints.
FINANCE_ENDPOINTS = {
    "statements":   ("GET",  "/finance/202309/statements"),
    "payments":     ("GET",  "/finance/202309/payments"),
}


def resolve_path(template: str, **kwargs) -> str:
    """Resolve {order_id} etc. in a path template."""
    return template.format(**kwargs)
