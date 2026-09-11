"""FastAPI dependencies shared by /v2 routers.

- ``get_session`` yields a SQLAlchemy Session whose transaction is
  rolled back when the request finishes (handler-agnostic; works for
  read paths too — saving cost of needless commits).
- ``caller_key_hash`` reads the SHA-256 key hash that ``AuthMiddleware``
  stashed in ``request.scope`` (it persists on ``request.scope`` because
  Starlette forwards the same scope into dependencies).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session


def get_session() -> Session:
    """Yield a request-scoped ORM session; rollback at end of request."""
    from tts_erp_v2.db.base import get_session_factory

    SessionLocal = get_session_factory()
    sess = SessionLocal()
    try:
        yield sess
    finally:
        try:
            sess.rollback()
        finally:
            sess.close()


SessionDep = Annotated[Session, Depends(get_session)]


def caller_key_hash(request: Request) -> str | None:
    """Return the authenticated key hash (or None if exempt)."""
    return request.scope.get("api_key_hash")


def shop_is_api_managed(sess: Session, *, shop_id: str) -> bool:
    """True 当 commerce.shops 里 (platform='tiktok', shop_id) 行的
    ``data_source='api'``。

    插件 ingest 守卫（2026-09-11 拍板）：TikTok 店铺授权是整店全 scope
    一次下发，``data_source`` 翻转为 'api' 后该店插件同步**全域停止**
    ——订单/物流/结算/广告一律改由 sync-worker 经 Open API 同步。
    order-sync / analytics 的 dumps 端点用本函数拦截，防止
    plugin.*/analytics.* 与 API 数据双写混合。

    未注册的店铺（shops 无行）返回 False：无行 = 纯插件店。
    """
    from sqlalchemy import select

    from tts_erp_v2.db.models.commerce import ChannelAccount

    ds = sess.execute(
        select(ChannelAccount.data_source).where(
            ChannelAccount.platform == "tiktok",
            ChannelAccount.shop_id == shop_id,
        )
    ).scalar_one_or_none()
    return ds == "api"


def caller_role(request: Request) -> str | None:
    """Return the authenticated role name (or None if exempt)."""
    return request.scope.get("api_key_role")


def require_role_at_least(request: Request, min_role: str) -> None:
    """Raise 403 if the caller's role is below ``min_role``.

    Mirror of the middleware check, but raised from a dependency so
    individual endpoints can be tighter without registering a brand-new
    path class in ``required_role()``.
    """
    from tts_erp_v2.middleware.auth import ROLE_LEVEL

    level = ROLE_LEVEL.get(request.scope.get("api_key_role") or "")
    needed = ROLE_LEVEL[min_role]
    if level is None or level < needed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"requires {min_role}",
        )
