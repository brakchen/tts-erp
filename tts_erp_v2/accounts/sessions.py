"""服务端会话（设计：tech-doc/user-account-authz-design.md §5）。

会话凭证 = 不透明随机 token（cookie 明文携带，``v2.<token>`` 格式），
库里 ``security.user_sessions`` 只存 ``sha256(token)``——库泄露无法冒用会话。
登出/禁用/改密走 ``revoked_at`` 即时吊销；过期走 ``expires_at``。
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from tts_erp_v2.accounts.models import UserSession

TOKEN_PREFIX = "v2."


def generate_token() -> str:
    """Return one opaque session token（明文仅出现在 cookie 与创建响应里）."""
    return f"{TOKEN_PREFIX}{secrets.token_urlsafe(32)}"


def token_hash(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def parse_token(raw: str | None) -> str | None:
    """Validate cookie-format and return the token, or None（旧格式一律无效）."""
    if not raw or not raw.startswith(TOKEN_PREFIX):
        return None
    token = raw[len(TOKEN_PREFIX) :]
    if not (32 <= len(token) <= 128):
        return None
    return raw


def create_session(
    session,
    *,
    user_id: int,
    ttl_seconds: int,
    ip: str | None = None,
    user_agent: str | None = None,
    now: datetime | None = None,
) -> str:
    """Insert one session row and return the plaintext token for the cookie."""
    now = now or datetime.now(UTC)
    token = generate_token()
    session.add(
        UserSession(
            token_hash=token_hash(token),
            user_id=user_id,
            expires_at=now + timedelta(seconds=ttl_seconds),
            last_seen_at=now,
            ip=ip,
            user_agent=user_agent,
        )
    )
    session.flush()
    return token


def get_session_row(session, token: str) -> UserSession | None:
    """Return the live session row（未吊销且未过期），else None。"""
    row = session.execute(
        select(UserSession).where(UserSession.token_hash == token_hash(token))
    ).scalar_one_or_none()
    if row is None:
        return None
    if row.revoked_at is not None:
        return None
    if row.expires_at <= datetime.now(UTC):
        return None
    return row


def touch_session(session, row: UserSession, *, min_interval_s: float = 300.0) -> None:
    """Best-effort low-frequency ``last_seen_at`` update（≥5 分钟才写一次）."""
    now = datetime.now(UTC)
    if row.last_seen_at is not None:
        last = row.last_seen_at
        if last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        if (now - last).total_seconds() < min_interval_s:
            return
    try:
        session.execute(
            update(UserSession)
            .where(UserSession.id == row.id)
            .values(last_seen_at=now)
        )
        session.commit()
    except Exception:  # noqa: BLE001 — observability only; never fail the request
        session.rollback()


def revoke_session(session, token: str) -> bool:
    """Revoke one session by token; True when a live session was revoked."""
    return _revoke_by_hash(session, token_hash(token))


def revoke_session_by_id(session, *, user_id: int, session_id: int) -> bool:
    return (
        _revoke_where(
            session,
            (UserSession.user_id == user_id)
            & (UserSession.id == session_id)
            & (UserSession.revoked_at.is_(None)),
        )
        > 0
    )


def revoke_user_sessions(
    session, *, user_id: int, keep_session_id: int | None = None
) -> int:
    """Revoke all live sessions of one user（改密可保留当前会话）; return count."""
    condition = (UserSession.user_id == user_id) & (UserSession.revoked_at.is_(None))
    if keep_session_id is not None:
        condition &= UserSession.id != keep_session_id
    return _revoke_where(session, condition)


def _revoke_by_hash(session, digest: bytes) -> bool:
    return (
        _revoke_where(
            session,
            (UserSession.token_hash == digest) & (UserSession.revoked_at.is_(None)),
        )
        > 0
    )


def _revoke_where(session, condition) -> int:
    result = session.execute(
        update(UserSession)
        .where(condition)
        .values(revoked_at=datetime.now(UTC))
        .execution_options(synchronize_session=False)
    )
    session.commit()
    return result.rowcount or 0
