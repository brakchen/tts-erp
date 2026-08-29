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
