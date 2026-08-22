"""妙手 ERP 开放平台（HMAC-SHA256 签名）HTTP 客户端.

底层 endpoint 全部来自 apifox project 8149572（妙手开放平台）。
Base URL 默认 ``https://openapi-erp.91miaoshou.com``，可通过环境变量 ``MIAOSHOU_ERP_BASE_URL`` 覆盖。

用法::

    from miaoshou import MiaoshouErpClient

    with MiaoshouErpClient.from_env() as client:
        result = client.shops.list(platform="tiktok", site="VN", page_no=1, page_size=20)
        for shop in result.data.shopList:
            print(shop.platformShopName, shop.shopId)
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from typing_extensions import Self

from .miaoshou_client import MiaoshouApiError
from .miaoshou_signing import hmac_sha256_sign

# 妙手 ERP 开放平台 base URL（apifox project 8149572 推测；实际从 .env 覆盖）
ERP_DEFAULT_BASE_URL = "https://openapi-erp.91miaoshou.com"


class MiaoshouErpClient:
    """妙手 ERP 开放平台 SDK（HMAC-SHA256 签名，api.91miaoshou.com）。"""

    # ---- 工厂 ----

    @classmethod
    def from_env(cls) -> MiaoshouErpClient:
        """从环境变量构造（依赖 MIAOSHOU_LICENSE_ID / MIAOSHOU_COMPANY_SECRET / MIAOSHOU_HTTP_TIMEOUT）。"""
        app_id_raw = os.environ.get("MIAOSHOU_LICENSE_ID", "")
        app_secret_raw = os.environ.get("MIAOSHOU_COMPANY_SECRET", "")
        # 同时过滤空白字符（空格 / tab / 换行）
        app_id = app_id_raw.strip()
        app_secret = app_secret_raw.strip()
        if not app_id or not app_secret:
            raise RuntimeError(
                "缺少 MIAOSHOU_LICENSE_ID / MIAOSHOU_COMPANY_SECRET（不能是空字符串或纯空白）。请在 .env 里配置，或手动 export。"
            )
        try:
            timeout = int(os.environ.get("MIAOSHOU_HTTP_TIMEOUT", "30"))
        except (TypeError, ValueError) as e:  # pragma: no cover — defensive
            raise RuntimeError(f"MIAOSHOU_HTTP_TIMEOUT 无效: {e}") from e
        return cls(app_id=app_id, app_secret=app_secret, timeout=timeout)

    # ---- 构造 ----

    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        base_url: str | None = None,
        timeout: int = 30,
        max_retries: int = 3,
        retry_backoff: float = 0.5,
    ):
        self.app_id = app_id
        self.app_secret = app_secret
        self.base_url = (
            base_url or os.environ.get("MIAOSHOU_ERP_BASE_URL", ERP_DEFAULT_BASE_URL)
        ).rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff

        # 延迟导入 endpoint 类
        from .endpoints.collection_box import CollectionBoxEndpoint
        from .endpoints.shop import ShopEndpoint
        from .endpoints.tk_collect_box import TkCollectBoxEndpoint

        self.shops: ShopEndpoint = ShopEndpoint(self)
        self.collection_box: CollectionBoxEndpoint = CollectionBoxEndpoint(self)
        self.tk_collect_box: TkCollectBoxEndpoint = TkCollectBoxEndpoint(self)

    # ---- 上下文管理 ----

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        return None

    # ---- 核心调用 ----

    def _call_erp(
        self,
        *,
        path: str,
        body: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
        extra_headers: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """对妙手 ERP 同步调用一次，返回原始 JSON dict.

        签名（apifox project 8149572 开放平台对接文档）:
            canonical = appSecret + path + str(timestamp_sec) + appKey + (bodyJson) + appSecret
            sign = HMAC-SHA256(appSecret, canonical).hexdigest()
            headers: x-app-key / x-timestamp（秒级） / x-sign（小写 hex）

        响应壳（success）: {"result": "success", "code": "0", "data": {...}}
        响应壳（error）:   {"result": "fail", "code": "<errorCode>", "reason": "..."}

        出错时抛 MiaoshouApiError。

        Retry 策略：
        - 网络错误（URLError / ConnectionError / TimeoutError / OSError）→ 指数退避重试 max_retries 次
        - HTTPError（4xx/5xx 业务错误）→ 不重试（重试也没意义）
        """
        body = body or {}
        query = query or {}
        extra_headers = extra_headers or {}

        # body JSON 字符串（无 body 时拼空串）— body 不变（与签名相关）
        body_json = (
            json.dumps(body, ensure_ascii=True, separators=(",", ":")) if body else ""
        )
        body_bytes = body_json.encode("utf-8")

        url = f"{self.base_url}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)

        # 网络错误自动重试，HTTPError（业务错误）不重试
        # 关键：每次 attempt 重新生成 timestamp + signature（否则跨 5 分钟会 signExpired）
        last_network_err: Exception | None = None
        http_err: urllib.error.HTTPError | None = None
        raw: str | None = None

        for attempt in range(1, max(self.max_retries, 1) + 1):
            try:
                timestamp_sec = int(time.time())
            except (OSError, ValueError) as e:
                raise RuntimeError("无法获取当前时间戳") from e

            signature = hmac_sha256_sign(
                app_secret=self.app_secret,
                path=path,
                timestamp_sec=timestamp_sec,
                app_key=self.app_id,
                body_json=body_json,
            )

            headers = {
                "Content-Type": "application/json;charset=UTF-8",
                "x-app-key": self.app_id,
                "x-timestamp": str(timestamp_sec),
                "x-sign": signature,
                **extra_headers,
            }
            req = urllib.request.Request(url, data=body_bytes, method="POST", headers=headers)

            if os.environ.get("MIAOSHOU_DEBUG_SIGN") == "1":
                print(
                    f"[miaoshou-erp-debug] url={url}",
                    f"  attempt={attempt}",
                    f"  x-app-key={self.app_id}",
                    f"  x-timestamp={timestamp_sec}",
                    f"  x-sign={signature}",
                    f"  body={body_json[:200]}",
                    file=sys.stderr,
                )

            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as http_resp:
                    raw = http_resp.read().decode("utf-8")
                last_network_err = None
                http_err = None
                break
            except urllib.error.HTTPError as e:
                # 业务错误，不重试
                http_err = e
                raw = None
                break
            except (
                urllib.error.URLError,
                ConnectionError,
                TimeoutError,
                OSError,
            ) as e:
                last_network_err = e
                if attempt >= self.max_retries:
                    break
                # 指数退避（retry_backoff=0 关闭）
                if self.retry_backoff > 0:
                    time.sleep(self.retry_backoff * (2 ** (attempt - 1)))
                continue
        if last_network_err is not None:
            raise MiaoshouApiError(
                0,
                f"网络错误，已重试 {self.max_retries} 次仍失败: {last_network_err!r}",
                None,
            ) from last_network_err

        if http_err is not None:
            e = http_err
            raw_body = e.read().decode("utf-8", errors="replace")[:300]
            biz_code: int | str = e.code
            biz_message = f"HTTP {e.code}: {raw_body}"
            try:
                err_json = json.loads(raw_body)
                biz_code = err_json.get("code", e.code)
                biz_message = (
                    f"{err_json.get('result', 'fail')}: "
                    f"{err_json.get('reason', raw_body)} "
                    f"(HTTP {e.code})"
                )
            except (json.JSONDecodeError, ValueError):
                pass  # 响应不是 JSON，回退到 HTTP code
            raise MiaoshouApiError(biz_code, biz_message, None) from e  # type: ignore[name-defined]

        assert raw is not None
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            raise MiaoshouApiError(
                0, f"无法解析响应: {e} body={raw[:200]}", None
            ) from e  # type: ignore[name-defined]


from pydantic import BaseModel as _BaseModel
from pydantic import ValidationError as _ValidationError

_T = __import__("typing").TypeVar("_T", bound=_BaseModel)


def _safe_validate(payload: dict, model_cls: type[_T]) -> _T:
    """包 model_validate：失败时抛 MiaoshouApiError（500）."""
    try:
        return model_cls.model_validate(payload)
    except _ValidationError as e:
        raise MiaoshouApiError(
            500, f"妙手响应 schema 不匹配 {model_cls.__name__}: {e}", payload
        ) from e
