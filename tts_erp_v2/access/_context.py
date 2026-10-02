"""请求级授权上下文（ContextVar，供服务端渲染的侧边栏过滤用）。

``AuthMiddleware`` 每请求写入；默认 ``None`` = 不过滤（API key 访问 / auth off）。
"""

from __future__ import annotations

from contextvars import ContextVar

# None = 无用户会话（API key / auth off）→ 侧边栏不过滤；
# frozenset = 会话用户的页面权限点集合 → 侧边栏按此过滤。
user_pages_var: ContextVar[frozenset[str] | None] = ContextVar(
    "tts_erp_user_pages", default=None
)
