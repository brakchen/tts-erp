"""服务端会话 token 生命周期（tts_erp_v2/accounts/sessions.py）。

契约：cookie 明文 token 为 ``v2.<urlsafe>`` 格式；库里只存 sha256(token)；
吊销走 ``revoked_at`` 即时生效，过期走 ``expires_at``；旧格式一律无效。
用 ``db_session``（savepoint 回滚）跑，不落真实行。
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from tts_erp_v2.accounts import sessions as account_sessions
from tts_erp_v2.accounts.models import User, UserSession

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]

# 会话测试不校验密码，用占位 PHC 避免每用例一次 argon2 开销。
_FAKE_HASH = "$argon2id$fake-hash-for-session-tests"


@pytest.fixture()
def user(db_session) -> User:
    row = User(
        username="test_ua_session_owner",
        display_name="TEST session owner",
        password_hash=_FAKE_HASH,
    )
    db_session.add(row)
    db_session.flush()
    return row


@pytest.fixture()
def other_user(db_session) -> User:
    row = User(
        username="test_ua_session_other",
        display_name="TEST session other",
        password_hash=_FAKE_HASH,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _session_row(db_session, user_id: int) -> UserSession:
    return db_session.execute(
        select(UserSession).where(UserSession.user_id == user_id)
    ).scalar_one()


def _live_row(db_session, token: str) -> UserSession:
    row = account_sessions.get_session_row(db_session, token)
    assert row is not None
    return row


def test_generate_token_uses_v2_prefix_and_parse_roundtrips() -> None:
    token = account_sessions.generate_token()
    raw = token[len(account_sessions.TOKEN_PREFIX) :]
    assert token.startswith("v2.")
    assert 32 <= len(raw) <= 128
    assert account_sessions.parse_token(token) == token


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "tts_session_legacy_value",
        "deadbeef.signature",  # 旧 api-key-hmac cookie 形态
        "v2.",
        "v2.tooshort",
        "v2." + "a" * 129,
    ],
)
def test_parse_token_rejects_legacy_and_invalid_formats(raw) -> None:
    assert account_sessions.parse_token(raw) is None


def test_create_session_stores_only_sha256_of_token(db_session, user: User) -> None:
    token = account_sessions.create_session(
        db_session, user_id=user.id, ttl_seconds=3600
    )
    row = _session_row(db_session, user.id)
    assert row.token_hash == hashlib.sha256(token.encode("utf-8")).digest()
    assert len(row.token_hash) == 32
    assert row.token_hash != token.encode("utf-8")  # 明文绝不入库
    assert account_sessions.get_session_row(db_session, token) is not None


def test_revoke_session_invalidates_token_and_is_idempotent(
    db_session, user: User
) -> None:
    token = account_sessions.create_session(
        db_session, user_id=user.id, ttl_seconds=3600
    )
    assert account_sessions.revoke_session(db_session, token) is True
    assert account_sessions.get_session_row(db_session, token) is None
    assert account_sessions.revoke_session(db_session, token) is False  # 已吊销


def test_get_session_row_rejects_expired_session(db_session, user: User) -> None:
    token = account_sessions.create_session(
        db_session,
        user_id=user.id,
        ttl_seconds=3600,
        now=datetime.now(UTC) - timedelta(hours=2),  # 过期时间已过
    )
    assert account_sessions.get_session_row(db_session, token) is None


def test_revoke_user_sessions_keeps_current_session(db_session, user: User) -> None:
    keep = account_sessions.create_session(
        db_session, user_id=user.id, ttl_seconds=3600
    )
    other = account_sessions.create_session(
        db_session, user_id=user.id, ttl_seconds=3600
    )
    keep_id = _live_row(db_session, keep).id

    revoked = account_sessions.revoke_user_sessions(
        db_session, user_id=user.id, keep_session_id=keep_id
    )

    assert revoked == 1
    assert account_sessions.get_session_row(db_session, keep) is not None
    assert account_sessions.get_session_row(db_session, other) is None


def test_revoke_session_by_id_is_scoped_to_owner(
    db_session, user: User, other_user: User
) -> None:
    token = account_sessions.create_session(
        db_session, user_id=user.id, ttl_seconds=3600
    )
    row = _live_row(db_session, token)

    assert (
        account_sessions.revoke_session_by_id(
            db_session, user_id=other_user.id, session_id=row.id
        )
        is False
    )
    assert account_sessions.get_session_row(db_session, token) is not None
    assert (
        account_sessions.revoke_session_by_id(
            db_session, user_id=user.id, session_id=row.id
        )
        is True
    )
    assert account_sessions.get_session_row(db_session, token) is None
