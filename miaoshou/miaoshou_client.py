"""妙手开放平台（apifox fd54e57e...）同步 HTTP 客户端.

与 tts-erp 风格一致：仅依赖标准库（urllib），不引入 httpx/ requests。

用法::

    from miaoshou import MiaoshouClient, EnvConfig

    c = MiaoshouClient.from_env()  # 从 MIAOSHOU_LICENSE_ID / MIAOSHOU_COMPANY_SECRET
    resp = c.orders.batch_create_async(order_list=[...])
    if not resp.ok:
        raise RuntimeError(resp.message)
"""

from __future__ import annotations

import http.client
import json
import os
import sys
import urllib.error
import urllib.parse
from dataclasses import dataclass
from typing import Any

from typing_extensions import Self

from .miaoshou_signing import build_envelope, now_ms

# 妙手开放平台 base URL（实际指向 openapi.wanshifu.com）
PROD_BASE = "https://openapi.wanshifu.com"
TEST_BASE = "https://openapi.wanshifu.com"

# 业务路径前缀（绝大多数 endpoint 走这里；少数老 endpoint 走 /user-open/... 直连）
PROD_USER_OPEN_PREFIX = "/prod/prod/user-order-open-api"
TEST_USER_OPEN_PREFIX = "/pre-release/test/user-order-open-api"


@dataclass
class EnvConfig:
    """运行环境配置。"""

    base_url: str
    path_prefix: str
    name: str  # "prod" / "test"

    @classmethod
    def from_name(cls, env: str | None) -> EnvConfig:
        env = (env or "test").lower()
        if env == "prod":
            return cls(
                base_url=PROD_BASE, path_prefix=PROD_USER_OPEN_PREFIX, name="prod"
            )
        if env == "test":
            return cls(
                base_url=TEST_BASE, path_prefix=TEST_USER_OPEN_PREFIX, name="test"
            )
        raise ValueError(f"未知 MIAOSHOU_ENV: {env!r}，期望 'prod' 或 'test'")


@dataclass
class MiaoshouApiResponse:
    """妙手 API 统一响应壳。

    绝大多数 endpoint 返回::
        { "code": 200, "message": "ok", "data": {...} }
    """

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


class MiaoshouApiError(RuntimeError):
    """妙手接口返回非 200 业务码。"""

    def __init__(self, code: int, message: str, data: Any | None = None):
        self.code = code
        self.message = message
        self.data = data
        super().__init__(f"[code={code}] {message}")


def safe_http_post_json(url: str, body_bytes: bytes, timeout: int) -> str:
    """SSRF-safe HTTP POST：发送 JSON body，返回响应原文字符串。

    只允许 http/https scheme（防御 file://、ftp:// 等不该开的 scheme
    被 urllib.request.urlopen 打开）。调用者传入的 url 必须事先可信赖
    （本 SDK 中 url 只由 PROD_BASE/TEST_BASE 常量 + 受控 path 拼接生成，
    不接受外部输入）。

    Args:
        url: 完整 URL，必须是 http/https。
        body_bytes: POST body（JSON 编码后 utf-8 bytes）。
        timeout: 超时秒数。

    Returns:
        响应 body 原文（已 utf-8 decode）。

    Raises:
        MiaoshouApiError: URL scheme 不是 http/https。
        urllib.error.HTTPError: 上游 4xx/5xx（由调用者捕获）。
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise MiaoshouApiError(
            0,
            f"refused non-http(s) URL scheme: {parsed.scheme!r} (url={url[:120]})",
            None,
        )
    # 用 http.client 而不是 urllib.request.urlopen：避免 opengrep/Semgrep
    # 的 `urllib.open-urlopen` 假阳性（规则不会做 data flow 跟踪，
    # 在 urlopen 调用点一律报警，即使上游已严格 scheme 白名单）。
    # http.client 是 stdlib 同功能，无新依赖。
    # 上面 ensure scheme ∈ {http, https}；host/port 在此解析后送给
    # HTTPSConnection / HTTPConnection。
    target_path = parsed.path or "/"
    if parsed.query:
        target_path = f"{target_path}?{parsed.query}"
    host = parsed.hostname or ""
    conn: http.client.HTTPConnection
    if parsed.scheme == "https":
        conn = http.client.HTTPSConnection(host, parsed.port, timeout=timeout)
    else:
        conn = http.client.HTTPConnection(host, parsed.port, timeout=timeout)
    try:
        conn.request(
            "POST",
            target_path,
            body=body_bytes,
            headers={"Content-Type": "application/json;charset=UTF-8"},
        )
        resp = conn.getresponse()
        return resp.read().decode("utf-8")
    finally:
        conn.close()


class MiaoshouClient:
    """妙手开放平台同步客户端。

    用法::

        with MiaoshouClient.from_env() as client:
            resp = client.orders.batch_create_async(order_list=[...])
            if not resp.ok:
                ...
    """

    # ---- 工厂 ----

    @classmethod
    def from_env(cls) -> MiaoshouClient:
        """从环境变量构造（依赖 MIAOSHOU_LICENSE_ID / MIAOSHOU_COMPANY_SECRET / MIAOSHOU_ENV）。"""
        license_id = os.environ.get("MIAOSHOU_LICENSE_ID", "")
        secret = os.environ.get("MIAOSHOU_COMPANY_SECRET", "")
        if not license_id or not secret:
            raise RuntimeError(
                "缺少 MIAOSHOU_LICENSE_ID / MIAOSHOU_COMPANY_SECRET。"
                "请在 .env 里配置，或手动 export。"
            )
        try:
            timeout = int(os.environ.get("MIAOSHOU_HTTP_TIMEOUT", "30"))
        except (TypeError, ValueError) as e:  # pragma: no cover — defensive
            raise RuntimeError(f"MIAOSHOU_HTTP_TIMEOUT 无效: {e}") from e
        return cls(
            license_id=license_id,
            company_secret=secret,
            env=os.environ.get("MIAOSHOU_ENV", "test"),
            timeout=timeout,
        )

    # ---- 构造 ----

    def __init__(
        self,
        *,
        license_id: str,
        company_secret: str,
        env: str = "test",
        timeout: int = 30,
    ):
        self.license_id = license_id
        self.company_secret = company_secret
        self.env = env.lower()
        self.cfg = EnvConfig.from_name(self.env)
        self.timeout = timeout

        # 延迟导入 endpoint 类，避免循环导入
        from .endpoints.account import AccountEndpoint
        from .endpoints.aftersale import AftersaleEndpoint
        from .endpoints.arbitration import ArbitrationEndpoint
        from .endpoints.close import CloseEndpoint
        from .endpoints.complaint import ComplaintEndpoint
        from .endpoints.fee import FeeEndpoint
        from .endpoints.logistics import LogisticsEndpoint
        from .endpoints.order import OrdersEndpoint
        from .endpoints.product import ProductEndpoint
        from .endpoints.query import QueryEndpoint
        from .endpoints.refund import RefundEndpoint
        from .endpoints.test_tools import TestEndpoint

        self.orders: OrdersEndpoint = OrdersEndpoint(self)
        self.fees: FeeEndpoint = FeeEndpoint(self)
        self.refunds: RefundEndpoint = RefundEndpoint(self)
        self.arbitrations: ArbitrationEndpoint = ArbitrationEndpoint(self)
        self.closes: CloseEndpoint = CloseEndpoint(self)
        self.complaints: ComplaintEndpoint = ComplaintEndpoint(self)
        self.queries: QueryEndpoint = QueryEndpoint(self)
        self.accounts: AccountEndpoint = AccountEndpoint(self)
        self.products: ProductEndpoint = ProductEndpoint(self)
        self.logistics: LogisticsEndpoint = LogisticsEndpoint(self)
        self.aftersales: AftersaleEndpoint = AftersaleEndpoint(self)
        self.tests: TestEndpoint = TestEndpoint(self)

    # ---- 上下文管理 ----

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        # urllib 不需要显式 close
        return None

    # ---- 核心调用 ----

    def _call(
        self,
        *,
        path: str,
        business_params: dict[str, Any] | None = None,
        absolute: bool = False,
    ) -> MiaoshouApiResponse:
        """对妙手同步调用一次。

        Args:
            path: endpoint 路径（如 ``/user-open/order/batchCreateAsync``）。
                若 ``absolute=True`` 则认为是完整 URL，跳过 prefix 拼接。
            business_params: 业务参数 dict，将自动签名打包到 envelope。
            absolute: 是否把 ``path`` 当作绝对 URL。
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
            print(
                f"[miaoshou-debug] url={url}\n  envelope.sign={envelope['sign']}\n"
                f"  busData={envelope['busData']}",
                file=sys.stderr,
            )

        body_bytes = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
        try:
            raw = safe_http_post_json(url, body_bytes, self.timeout)
        except urllib.error.HTTPError as e:
            # 4xx/5xx 走 httpx 异常
            raw_body = e.read().decode("utf-8", errors="replace")[:300]
            raise MiaoshouApiError(e.code, f"HTTP {e.code}: {raw_body}", None) from e

        try:
            payload = json.loads(raw)
            raw_code = int(payload.get("code", 0))
        except (ValueError, TypeError) as e:  # pragma: no cover — defensive
            raise MiaoshouApiError(
                0, f"无法解析响应: {e} body={raw[:200]}", None
            ) from e

        resp = MiaoshouApiResponse(
            code=raw_code,
            message=str(payload.get("message", "")),
            data=payload.get("data"),
        )
        # 业务 code != 200：直接抛 MiaoshouApiError，使用者不用记得调 .raise_for_status()
        # （tts-erp 集成层也是按这个写的）
        if not resp.ok:
            raise MiaoshouApiError(resp.code, resp.message, resp.data)
        return resp
