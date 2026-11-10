"""上游错误文本的安全处理：只取 reason、剥离凭据、给出稳定的过期标记。

2026-11-10 生产事实（只读核查，``integration.sync_jobs``）：
``miaoshou.purchase_price_clean`` 从 2026-10-09 14:42 起连续失败 776 次，
而 ``sync_jobs.error_message`` 只有一句::

    RuntimeError: Miaoshou purchase business failure: result='fail' code=50001

上游其实给了人类可读的原因（``您的授权已经过期，请重新登录``）——是我们丢掉了，
于是运维只看到一个无法自解释的 ``code=50001``，31 天没人处理。

安全纪律（**代码里原有注释，必须遵守**）：上游错误体是「不可信的认证错误体，
可能回显 Cookie/token」。所以：

1. **只取 ``reason`` 字段**，绝不把整个 payload 放进异常消息 / 日志 / sync_jobs；
2. ``reason`` 本身也要先剥离疑似凭据形态的片段，再出去；
3. 「登录态过期」用**我们自己产生的稳定标记** ``credential_expired`` 表示，
   不把上游中文措辞当作判据（上游改一次文案，就会再次"看不见"）。
"""

from __future__ import annotations

import re
from typing import Any

#: 「登录态过期」的稳定机器可读标记。
#:
#: 下游（``scripts/watchdog_sync.py`` 的规则 c）按**这个**匹配，不按上游中文匹配。
#: ⚠️ watchdog 侧刻意**不 import** 本模块：watchdog 以
#: ``python scripts/watchdog_sync.py`` 运行，``sys.path[0]`` 是 ``scripts/``，
#: ``import tts_erp_v2`` 会解析到 editable 安装的那棵树，而不是运行态那棵树。
#: ⇒ 同一份字面量在两处各存一份，改动必须同步（watchdog 侧有交叉引用注释）。
CREDENTIAL_EXPIRED_MARKER = "credential_expired"

_REDACTED = "[REDACTED]"

#: reason 长度上限。上游 reason 是给人看的一句话，没必要整段带进 sync_jobs。
MAX_REASON_CHARS = 200

# 疑似凭据形态。顺序有意义：先整体（JWT / Authorization 头），再 key=value，
# 再 URL 查询参数，最后长 hex。
_CREDENTIAL_PATTERNS: tuple[re.Pattern[str], ...] = (
    # JWT：aaa.bbb.ccc
    re.compile(r"\b[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    # Authorization: Bearer / Basic
    re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"),
    # key=value / key: value 形态的凭据
    re.compile(
        r"(?i)\b(cookie|set-cookie|token|access[_-]?token|refresh[_-]?token"
        r"|session|sessionid|sid|zebra|x-app-zebra|authorization|password|passwd"
        r"|secret|api[_-]?key|app[_-]?key|appsecret|signature|sign)\b\s*[:=]\s*"
        r"[^,;\s]+"
    ),
    # URL 查询参数里的凭据
    re.compile(r"(?i)([?&](?:token|cookie|zebra|session|auth|key|sign)=)[^&\s]+"),
    # 长 hex（凭据指纹 / 签名）
    re.compile(r"\b[0-9a-fA-F]{24,}\b"),
)

#: 判定「reason 表达登录态过期」的措辞。
#: 只用来**生成**稳定标记，不直接进 watchdog 的匹配表（理由见模块 docstring 第 3 条）。
_EXPIRY_SUBSTRINGS: tuple[str, ...] = (
    "授权已经过期",
    "授权已过期",
    "授权过期",
    "授权失效",
    "登录已经过期",
    "登录已过期",
    "登录过期",
    "登录已失效",
    "登录失效",
    "请重新登录",
    "重新登录",
    "会话已经过期",
    "会话已过期",
    "会话过期",
    "会话失效",
    "cookie失效",
    "cookie已失效",
    "credential expired",
    "session expired",
    "re-login",
    "relogin",
    "unauthorized",
)


def sanitize_reason(reason: Any, *, limit: int = MAX_REASON_CHARS) -> str:
    """把上游 ``reason`` 清洗成可以外发的短文本。

    剥离疑似凭据片段（长 hex / JWT / Authorization 头 / cookie 串 / URL 里的
    token 参数），压掉多余空白，并截断长度。非字符串一律当作空串。

    >>> sanitize_reason("您的授权已经过期，请重新登录")
    '您的授权已经过期，请重新登录'
    >>> sanitize_reason("cookie=abc123def456")
    'cookie=[REDACTED]'
    """
    if reason is None:
        return ""
    text = str(reason)
    if not text:
        return ""
    for pattern in _CREDENTIAL_PATTERNS:
        text = pattern.sub(_REDACTED, text)
    text = " ".join(text.split())
    if limit > 0 and len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


def credential_expiry_marker(reason: Any) -> str | None:
    """reason 是否表达「登录态过期」。

    是则返回 :data:`CREDENTIAL_EXPIRED_MARKER`（稳定标记），否则 ``None``。

    >>> credential_expiry_marker("您的授权已经过期，请重新登录")
    'credential_expired'
    >>> credential_expiry_marker("参数错误") is None
    True
    """
    if reason is None:
        return None
    text = str(reason).lower()
    if any(word in text for word in _EXPIRY_SUBSTRINGS):
        return CREDENTIAL_EXPIRED_MARKER
    return None


__all__ = [
    "CREDENTIAL_EXPIRED_MARKER",
    "MAX_REASON_CHARS",
    "credential_expiry_marker",
    "sanitize_reason",
]
