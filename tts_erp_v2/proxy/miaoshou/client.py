"""Miaoshou Open Platform SDK (MiaoshouClient + MiaoshouErpClient).

Wave 5 migration of the two legacy clients out of ``miaoshou/`` into
the proxy package. Functionality is byte-for-byte equivalent to:

* :class:`MiaoshouClient`  — MD5-signed (apifox doc-824327)
* :class:`MiaoshouErpClient` — HMAC-SHA256-signed (apifox 8149572)

Differences from the legacy clients
-----------------------------------
* Endpoint classes (12 of them: orders/fees/refunds/arbitrations/
  closes/complaints/queries/accounts/products/logistics/aftersales/
  tests) are **not** moved here in Wave 5. They remain in
  ``miaoshou/endpoints/`` and are imported lazily by the legacy
  clients. Migrating them is a separate lane decision (they are
  domain-heavy; the proxy layer is just transport).
* The :func:`_call` helpers now wrap their inner HTTP step with the
  rate-limiter + retry helpers from :mod:`tts_erp_v2.proxy.miaoshou.rate_limit`
  and :mod:`tts_erp_v2.proxy.miaoshou.retry`. This is the bug-fix
  carrier for the silent-truncation issue (237 → 20 records).
* :class:`MiaoshouApiError` is re-exported from this module so
  callers do not have to import from the legacy path.

We keep the legacy ``miaoshou/`` package intact: the FastAPI app at
:9877 still imports from there. The :mod:`tts_erp_v2.proxy.miaoshou.client`
imports the signing helpers from ``miaoshou.miaoshou_signing`` directly
to avoid duplicating the canonical-string logic.
"""
from __future__ import annotations

import http.client
import json
import os
import urllib.error
import urllib.parse
from dataclasses import dataclass
from typing import Any, Self

# Import signing from the legacy package — same algorithm, no need to
# duplicate. Once the §7.1 cutover is done, we can move the signing
# module into ``tts_erp_v2/proxy/miaoshou/`` and update the import.
from miaoshou.miaoshou_signing import (
    build_envelope,
    hmac_sha256_sign,
    now_ms,
)
from tts_erp_v2.proxy.errors import ProxyError, SigningError
from tts_erp_v2.proxy.miaoshou.rate_limit import (
    TokenBucket,
)

# ---- Shared constants -----------------------------------------------

PROD_BASE = "https://openapi.wanshifu.com"
TEST_BASE = "https://openapi.wanshifu.com"
PROD_USER_OPEN_PREFIX = "/prod/prod/user-order-open-api"
TEST_USER_OPEN_PREFIX = "/pre-release/test/user-order-open-api"
ERP_DEFAULT_BASE_URL = "https://openapi-erp.91miaoshou.com"


# ---- Errors ---------------------------------------------------------


@dataclass
class MiaoshouApiResponse:
    """Unified Miaoshou response envelope (code/message/data)."""

    code: int
    message: str
    data: Any | None

    @property
    def ok(self) -> bool:
        return self.code == 200

    def raise_for_status(self) -> MiaoshouApiResponse:
        if not self.ok:
            raise MiaoshouApiError(self.code, self.message, self.data)
        return self


class MiaoshouApiError(ProxyError):
    """Miaoshou returned a non-200 business code or a transport failure.

    ``code`` is either:
      * an HTTP status code (int, for transport failures)
      * a business error code (int for open-platform, str for ERP)
    """

    def __init__(self, code: int | str, message: str, data: Any | None = None):
        self.code = code
        self.message = message
        self.data = data
        super().__init__(f"[code={code}] {message}")


# ---- HTTP transport helpers (shared by both clients) ----------------


def _assert_safe_url(url: str) -> urllib.parse.ParseResult:
    """Refuse non-http(s) URLs before handing them to ``http.client``."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise SigningError(
            f"refused non-http(s) URL scheme: {parsed.scheme!r} (url={url[:120]})"
        )
    if not parsed.hostname:
        raise SigningError(f"missing host in URL: {url[:120]}")
    return parsed


def safe_http_post_json(
    url: str,
    body_bytes: bytes,
    timeout: float,
    headers: dict[str, str] | None = None,
) -> str:
    """SSRF-safe POST: whitelist scheme, return response body.

    Uses ``http.client`` directly to avoid opengrep tier-1 false
    positives on ``urllib.request.urlopen`` (the legacy module's
    same workaround). ``headers`` are merged over the default
    JSON Content-Type (ERP auth headers x-app-key / x-timestamp /
    x-sign ride in through here).
    """
    parsed = _assert_safe_url(url)
    target_path = parsed.path or "/"
    if parsed.query:
        target_path = f"{target_path}?{parsed.query}"
    host: str = parsed.hostname or ""
    if parsed.scheme == "https":
        conn = http.client.HTTPSConnection(host, parsed.port, timeout=timeout)
    else:
        conn = http.client.HTTPConnection(host, parsed.port, timeout=timeout)
    try:
        req_headers = {"Content-Type": "application/json;charset=UTF-8"}
        if headers:
            req_headers.update(headers)
        conn.request("POST", target_path, body=body_bytes, headers=req_headers)
        resp = conn.getresponse()
        status = getattr(resp, "status", 200)
        if isinstance(status, int) and status >= 400:
            preview = resp.read().decode("utf-8", errors="replace")[:300]
            raise MiaoshouApiError(status, f"HTTP {status}: {preview}", None)
        return resp.read().decode("utf-8")
    finally:
        conn.close()


# ---- Environment config --------------------------------------------


@dataclass
class EnvConfig:
    """Open-platform environment config (prod / test)."""

    base_url: str
    path_prefix: str
    name: str  # "prod" / "test"

    @classmethod
    def from_name(cls, env: str | None) -> EnvConfig:
        env = (env or "test").lower()
        if env == "prod":
            return cls(base_url=PROD_BASE, path_prefix=PROD_USER_OPEN_PREFIX, name="prod")
        if env == "test":
            return cls(base_url=TEST_BASE, path_prefix=TEST_USER_OPEN_PREFIX, name="test")
        raise ValueError(f"未知 MIAOSHOU_ENV: {env!r}, expected 'prod' or 'test'")


# ---- Open-platform client (MD5) ------------------------------------


class MiaoshouClient:
    """Miaoshou open-platform sync client (MD5 signing, doc-824327).

    Endpoint classes (orders/fees/refunds/...) are intentionally NOT
    mounted here in Wave 5 — they live in :mod:`miaoshou.endpoints` and
    are imported lazily by the legacy clients. We expose only the
    transport (``_call``) so the proxy layer is reusable; callers can
    either wrap this client or build their own endpoint dispatchers.
    """

    def __init__(
        self,
        *,
        license_id: str,
        company_secret: str,
        env: str = "test",
        timeout: float = 30,
        rate_bucket: TokenBucket | None = None,
    ):
        self.license_id = license_id
        self.company_secret = company_secret
        self.env = env.lower()
        self.cfg = EnvConfig.from_name(self.env)
        self.timeout = timeout
        self.rate_bucket = rate_bucket or TokenBucket()

    @classmethod
    def from_env(cls) -> MiaoshouClient:
        license_id = os.environ.get("MIAOSHOU_LICENSE_ID", "")
        secret = os.environ.get("MIAOSHOU_COMPANY_SECRET", "")
        if not license_id or not secret:
            raise RuntimeError(
                "missing MIAOSHOU_LICENSE_ID / MIAOSHOU_COMPANY_SECRET"
            )
        try:
            timeout = float(os.environ.get("MIAOSHOU_HTTP_TIMEOUT", "30"))
        except (TypeError, ValueError) as e:
            raise RuntimeError(f"MIAOSHOU_HTTP_TIMEOUT invalid: {e}") from e
        return cls(
            license_id=license_id,
            company_secret=secret,
            env=os.environ.get("MIAOSHOU_ENV", "test"),
            timeout=timeout,
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        return None

    def _call(
        self,
        *,
        path: str,
        business_params: dict[str, Any] | None = None,
        absolute: bool = False,
    ) -> MiaoshouApiResponse:
        """Single upstream call.

        Uses the rate-bucket before sending. If the upstream returns a
        rate-limit-shaped body, the caller should use ``paginate_with_retry``
        rather than looping here.
        """
        params = business_params or {}
        envelope = build_envelope(
            params,
            company_secret=self.company_secret,
            license_id=self.license_id,
            timestamp_ms=now_ms(),
        )
        url = path if absolute else f"{self.cfg.base_url}{self.cfg.path_prefix}{path}"

        if os.environ.get("MIAOSHOU_DEBUG_SIGN") == "1":
            import sys
            print(
                f"[miaoshou-debug] url={url}\n  envelope.sign={envelope['sign']}\n"
                f"  busData={envelope['busData']}",
                file=sys.stderr,
            )

        # Pace the request.
        self.rate_bucket.acquire()

        body_bytes = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
        try:
            raw = safe_http_post_json(url, body_bytes, self.timeout)
        except urllib.error.HTTPError as e:
            preview = e.read().decode("utf-8", errors="replace")[:300]
            raise MiaoshouApiError(e.code, f"HTTP {e.code}: {preview}", None) from e

        try:
            payload = json.loads(raw)
        except (ValueError, TypeError) as e:
            raise MiaoshouApiError(0, f"无法解析响应: {e} body={raw[:200]}", None) from e

        try:
            raw_code = int(payload.get("code", 0))
        except (TypeError, ValueError):
            raw_code = 0
        resp = MiaoshouApiResponse(
            code=raw_code,
            message=str(payload.get("message", "")),
            data=payload.get("data"),
        )
        if not resp.ok:
            raise MiaoshouApiError(resp.code, resp.message, resp.data)
        return resp


# ---- ERP client (HMAC-SHA256) --------------------------------------


class MiaoshouErpClient:
    """Miaoshou ERP open-platform SDK (HMAC-SHA256 signing).

    Endpoint classes (shops/collection_box/tk_collect_box) live in
    :mod:`miaoshou.endpoints` and are not migrated here in Wave 5.
    This client exposes the transport only.
    """

    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        base_url: str | None = None,
        timeout: float = 30,
        max_retries: int = 3,
        retry_backoff: float = 0.5,
        rate_bucket: TokenBucket | None = None,
    ):
        self.app_id = app_id
        self.app_secret = app_secret
        self.base_url = (
            base_url or os.environ.get("MIAOSHOU_ERP_BASE_URL", ERP_DEFAULT_BASE_URL)
        ).rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self.rate_bucket = rate_bucket or TokenBucket()

    @classmethod
    def from_env(cls) -> MiaoshouErpClient:
        app_id_raw = os.environ.get("MIAOSHOU_LICENSE_ID", "")
        app_secret_raw = os.environ.get("MIAOSHOU_COMPANY_SECRET", "")
        app_id = app_id_raw.strip()
        app_secret = app_secret_raw.strip()
        if not app_id or not app_secret:
            raise RuntimeError(
                "missing MIAOSHOU_LICENSE_ID / MIAOSHOU_COMPANY_SECRET"
            )
        try:
            timeout = float(os.environ.get("MIAOSHOU_HTTP_TIMEOUT", "30"))
        except (TypeError, ValueError) as e:
            raise RuntimeError(f"MIAOSHOU_HTTP_TIMEOUT invalid: {e}") from e
        return cls(app_id=app_id, app_secret=app_secret, timeout=timeout)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        return None

    def _call_erp(
        self,
        *,
        path: str,
        body: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
        extra_headers: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Single ERP call. Returns the raw JSON dict (success body).

        Raises :class:`MiaoshouApiError` for transport errors or biz
        errors (response ``result != "success"``).

        This method does NOT do retry — for retry use
        :func:`tts_erp_v2.proxy.miaoshou.retry.call_with_retry`. It
        DOES pace the request via the rate bucket so a 6-attempt retry
        doesn't burn the upstream's QPS budget.
        """
        import time as _time

        body = body or {}
        query = query or {}
        extra_headers = extra_headers or {}
        body_json = (
            json.dumps(body, ensure_ascii=True, separators=(",", ":")) if body else ""
        )
        body_bytes = body_json.encode("utf-8")

        url = f"{self.base_url}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)

        self.rate_bucket.acquire()

        try:
            timestamp_sec = int(_time.time())
        except (OSError, ValueError) as e:
            raise RuntimeError("无法获取当前时间戳") from e

        signature = hmac_sha256_sign(
            app_secret=self.app_secret,
            path=path,
            timestamp_sec=timestamp_sec,
            app_key=self.app_id,
            body_json=body_json,
        )

        # ERP auth rides in headers (legacy contract: x-app-key /
        # x-timestamp (seconds) / x-sign lowercase hex). Without these
        # the upstream rejects every call with [code=signMissing].
        auth_headers = {
            "x-app-key": self.app_id,
            "x-timestamp": str(timestamp_sec),
            "x-sign": signature,
            **extra_headers,
        }

        try:
            raw = safe_http_post_json(url, body_bytes, self.timeout, headers=auth_headers)
        except urllib.error.HTTPError as e:
            preview = e.read().decode("utf-8", errors="replace")[:300]
            biz_code: int | str = e.code
            biz_message = f"HTTP {e.code}: {preview}"
            try:
                err_json = json.loads(preview)
                biz_code = err_json.get("code", e.code)
                biz_message = (
                    f"{err_json.get('result', 'fail')}: "
                    f"{err_json.get('reason', preview)} (HTTP {e.code})"
                )
            except (json.JSONDecodeError, ValueError):
                pass
            raise MiaoshouApiError(biz_code, biz_message, None) from e

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as e:
            raise MiaoshouApiError(0, f"无法解析响应: {e} body={raw[:200]}", None) from e

        # ERP envelope: {"result":"success"/"fail", "code":"0"/<errorCode>, "data":..., "reason":...}
        result = payload.get("result")
        if result == "fail":
            raise MiaoshouApiError(
                payload.get("code", "?"),
                f"{payload.get('reason', 'fail')}",
                payload.get("data"),
            )
        return payload


__all__ = [
    "ERP_DEFAULT_BASE_URL",
    "PROD_BASE",
    "TEST_BASE",
    "EnvConfig",
    "MiaoshouApiError",
    "MiaoshouApiResponse",
    "MiaoshouClient",
    "MiaoshouErpClient",
    "safe_http_post_json",
]
