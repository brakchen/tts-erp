"""妙手开放平台 SDK.

来自 apifox 文档 fd54e57e-9b98-4c34-bada-306221c39e68（标题"妙手开放平台"）。

两个客户端：
- ``MiaoshouClient``：妙手开放平台（MD5 签名，``openapi.wanshifu.com``）
- ``MiaoshouErpClient``：妙手 ERP 开放平台（HMAC-SHA256 签名，``api.91miaoshou.com``）

用法::

    # 妙手：服务单（安装/维修/搬运等）
    from miaoshou import MiaoshouClient
    with MiaoshouClient.from_env() as client:
        resp = client.orders.batch_create_async(order_list=[{
            "orderNo": "MY-001", "serviceType": 101, ...
        }])

    # 妙手 ERP：跨境电商（TikTok/Shopee 等店铺 + 采集箱）
    from miaoshou import MiaoshouErpClient
    with MiaoshouErpClient.from_env() as client:
        shops = client.shops.list(platform="tiktok", site="VN")

回调接收方见 :mod:`miaoshou.callbacks.router`。
"""

from __future__ import annotations

from .miaoshou_client import (
    EnvConfig,
    MiaoshouApiError,
    MiaoshouApiResponse,
    MiaoshouClient,
)
from .miaoshou_erp_client import (
    ERP_DEFAULT_BASE_URL,
    MiaoshouErpClient,
)
from .miaoshou_signing import (
    build_envelope,
    build_sign,
    hmac_sha256_sign,
    md5_upper,
    now_ms,
)

__all__ = [  # noqa: RUF022
    # 妙手客户端（MD5）
    "EnvConfig",
    "MiaoshouApiError",
    "MiaoshouApiResponse",
    "MiaoshouClient",
    # 妙手 ERP 客户端（HMAC-SHA256）
    "ERP_DEFAULT_BASE_URL",
    "MiaoshouErpClient",
    # 签名
    "build_envelope",
    "build_sign",
    "hmac_sha256_sign",
    "md5_upper",
    "now_ms",
]  # fmt: skip  # ruff: RUF022（带注释的 __all__ isort 误报）
