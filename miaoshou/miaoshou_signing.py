"""妙手开放平台签名算法.

两种签名：
- MD5（妙手开放平台 doc-824327）：``base64(json)+secret`` → MD5 hex upper
- HMAC-SHA256（妙手 ERP 开放平台）：见 ``hmac_sha256_sign`` 文档
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

# doc-824327 协议强制使用 MD5（非口令/凭据用途，仅用于服务端匹配签名）
_md5 = hashlib.md5  # nosec B324 — protocol-mandated MD5 signature


def build_sign(business_params: dict[str, Any], company_secret: str) -> str:
    """计算妙手开放平台的 MD5 签名字符串（apifox doc-824327）。"""
    bus_data = base64.b64encode(
        json.dumps(business_params, ensure_ascii=False).encode("utf-8")
    ).decode("ascii")
    return md5_upper(bus_data + company_secret)


def md5_upper(s: str) -> str:
    """对一个字符串做 md5 并返回大写十六进制（32 字符）。

    MD5 在此用作协议级签名校验（与远端算法一致即可），并非用于口令/凭据存储。
    """
    return _md5(s.encode("utf-8")).hexdigest().upper()


def now_ms() -> int:
    """当前毫秒时间戳。"""
    try:
        return int(time.time() * 1000)
    except (OSError, OverflowError, ValueError) as e:  # pragma: no cover — defensive
        raise RuntimeError(f"无法获取当前时间戳: {e}") from e


def build_envelope(
    business_params: dict[str, Any],
    *,
    company_secret: str,
    license_id: str,
    timestamp_ms: int | None = None,
) -> dict[str, Any]:
    """构造发送给妙手的 envelope。"""
    bus_data = base64.b64encode(
        json.dumps(business_params, ensure_ascii=False).encode("utf-8")
    ).decode("ascii")
    sign = md5_upper(bus_data + company_secret)
    return {
        "licenseId": license_id,
        "companySecret": company_secret,
        "sign": sign,
        "busData": bus_data,
        "timestamp": timestamp_ms if timestamp_ms is not None else now_ms(),
    }


# ---- HMAC-SHA256（妙手 ERP 开放平台）----


def hmac_sha256_sign(
    *,
    app_secret: str,
    path: str,
    timestamp_sec: int,
    app_key: str,
    body_json: str = "",
) -> str:
    """妙手 ERP HMAC-SHA256 签名（apifox project 8149572 开放平台对接文档官方算法）.

    Canonical 拼接:
        ``appSecret + path + str(timestamp_sec) + appKey + (bodyJson if any) + appSecret``

    HMAC: key=``appSecret``, message=canonical, algo=SHA256, output=小写 hex

    Headers 配套:
        - ``x-app-key``: appKey
        - ``x-timestamp``: str(timestamp_sec)（秒级 Unix 时间戳）
        - ``x-sign``: 本函数返回值（小写 hex）
        - ``Content-Type: application/json``

    响应失败常见原因：
        - signExpired → timestamp 与服务器偏差 > 300 秒
        - signInvalid → 拼接顺序错了 / AppSecret 错了
    """
    content = app_secret + path + str(timestamp_sec) + app_key
    if body_json:
        content += body_json
    content += app_secret
    return hmac.new(
        app_secret.encode("utf-8"),
        content.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
