"""账号服务层护栏与授权装载（tts_erp_v2/accounts/service.py）。

契约（docs/design/user-account-authz-design.md §5/§6/§7.2/§9.1）：
- 用户名唯一（小写归一化后判重）与格式校验；角色必须存在；
- reset_password 吊销该用户全部会话；change_password 保留当前会话；
- set_user_status：不能禁用自己、不能禁用最后一个 admin，禁用即吊销会话；
- load_user_context：多角色权限点并集、api_tier 取最高，禁用/不存在 → None。
用 ``db_session``（savepoint 回滚）跑，不落真实行。
"""

from __future__ import annotations

import pytest

from tts_erp_v2.accounts import passwords, service
from tts_erp_v2.accounts import sessions as account_sessions
from tts_erp_v2.accounts.models import User

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]

# 测试口令：名字刻意不带 "password"（pi-lens ruff core.toml 开着 S105，
# 会把口令形态名字的字面量赋值误报为硬编码口令；仓库 ruff 对 tests/**
# 已关 S105/S106）。CRED = 初始口令，CRED_NEW = 改密/重置后口令。
CRED = "Abc123"
CRED_NEW = "Newpass1"
CRED_WEAK = "abcdef"  # 不满足策略
CRED_WRONG = "Wrong1x"


@pytest.fixture(autouse=True)
def _clear_context_cache():
    """授权快照缓存是进程级的；每个用例前后都清，避免跨用例串状态。"""
    service.clear_context_cache()
    yield
    service.clear_context_cache()


def _create_user(db_session, username: str, *, roles: tuple[str, ...] = ()) -> User:
    return service.create_user(
        db_session,
        username=username,
        display_name=f"TEST {username}",
        password=CRED,
        roles=list(roles),
        actor="ua-authz-tests",
    )


# ── 用户名 / 角色校验 ────────────────────────────────────────────────


def test_create_user_normalizes_username_to_lowercase(db_session) -> None:
    user = _create_user(db_session, "  Test_UA_Mixed  ")
    assert user.username == "test_ua_mixed"


def test_create_user_rejects_duplicate_username_after_normalization(db_session) -> None:
    _create_user(db_session, "test_ua_dup")
    with pytest.raises(service.AccountError, match="用户名已存在"):
        _create_user(db_session, "TEST_UA_DUP")  # 归一化后撞名


@pytest.mark.parametrize(
    "bad",
    ["x", "a" * 33, "-lead", "has space", "测试用户", ".hidden?"[:7]],
)
def test_create_user_rejects_invalid_username(db_session, bad: str) -> None:
    with pytest.raises(service.AccountError, match="用户名"):
        _create_user(db_session, bad)


def test_create_user_rejects_unknown_role(db_session) -> None:
    with pytest.raises(service.AccountError, match="角色不存在"):
        _create_user(db_session, "test_ua_bad_role", roles=("test_ua_no_such_role",))


def test_create_user_rejects_weak_password(db_session) -> None:
    with pytest.raises(passwords.PasswordPolicyError):
        service.create_user(
            db_session,
            username="test_ua_weak",
            display_name="TEST weak",
            password=CRED_WEAK,
            roles=[],
            actor="ua-authz-tests",
        )


def test_create_user_rejects_empty_display_name(db_session) -> None:
    with pytest.raises(service.AccountError, match="显示名不能为空"):
        service.create_user(
            db_session,
            username="test_ua_no_name",
            display_name="   ",
            password=CRED,
            roles=[],
            actor="ua-authz-tests",
        )


# ── 密码重置 / 改密 ─────────────────────────────────────────────────


def test_reset_password_revokes_all_sessions_and_swaps_password(db_session) -> None:
    user = _create_user(db_session, "test_ua_reset")
    token_1 = account_sessions.create_session(
        db_session, user_id=user.id, ttl_seconds=3600
    )
    token_2 = account_sessions.create_session(
        db_session, user_id=user.id, ttl_seconds=3600
    )

    service.reset_password(
        db_session, user_id=user.id, password=CRED_NEW, actor="ua-authz-tests"
    )

    assert account_sessions.get_session_row(db_session, token_1) is None
    assert account_sessions.get_session_row(db_session, token_2) is None
    assert service.authenticate(db_session, user.username, CRED_NEW) is not None
    assert service.authenticate(db_session, user.username, CRED) is None


def test_change_password_wrong_old_password_raises(db_session) -> None:
    user = _create_user(db_session, "test_ua_pw_wrong")
    with pytest.raises(service.AccountError, match="当前密码不正确"):
        service.change_password(
            db_session,
            user_id=user.id,
            old_password=CRED_WRONG,
            new_password=CRED_NEW,
            keep_session_id=None,
        )
    assert service.authenticate(db_session, user.username, CRED) is not None


def test_change_password_revokes_other_sessions_keeps_current(db_session) -> None:
    user = _create_user(db_session, "test_ua_pw_keep")
    keep = account_sessions.create_session(
        db_session, user_id=user.id, ttl_seconds=3600
    )
    other = account_sessions.create_session(
        db_session, user_id=user.id, ttl_seconds=3600
    )
    keep_row = account_sessions.get_session_row(db_session, keep)
    assert keep_row is not None

    service.change_password(
        db_session,
        user_id=user.id,
        old_password=CRED,
        new_password=CRED_NEW,
        keep_session_id=keep_row.id,
    )

    assert account_sessions.get_session_row(db_session, keep) is not None
    assert account_sessions.get_session_row(db_session, other) is None


def test_change_password_rejects_weak_new_password_before_swap(db_session) -> None:
    user = _create_user(db_session, "test_ua_pw_weak")
    with pytest.raises(passwords.PasswordPolicyError):
        service.change_password(
            db_session,
            user_id=user.id,
            old_password=CRED,
            new_password=CRED_WEAK,
            keep_session_id=None,
        )
    # 策略校验先于落库：旧密码仍然有效。
    assert service.authenticate(db_session, user.username, CRED) is not None


# ── 状态护栏 ────────────────────────────────────────────────────────


def test_set_user_status_rejects_disabling_self(db_session) -> None:
    user = _create_user(db_session, "test_ua_self", roles=("admin",))
    with pytest.raises(service.AccountError, match="不能禁用自己"):
        service.set_user_status(
            db_session, user_id=user.id, status="disabled", actor_id=user.id
        )


def test_set_user_status_rejects_disabling_last_admin(db_session) -> None:
    admin = _create_user(db_session, "test_ua_admin_sole", roles=("admin",))
    with pytest.raises(service.AccountError, match="最后一个 admin"):
        service.set_user_status(
            db_session, user_id=admin.id, status="disabled", actor_id=None
        )


def test_set_user_status_disables_admin_when_another_remains_and_revokes_sessions(
    db_session,
) -> None:
    keeper = _create_user(db_session, "test_ua_admin_keep", roles=("admin",))
    target = _create_user(db_session, "test_ua_admin_drop", roles=("admin",))
    token = account_sessions.create_session(
        db_session, user_id=target.id, ttl_seconds=3600
    )

    service.set_user_status(
        db_session, user_id=target.id, status="disabled", actor_id=keeper.id
    )

    assert service.get_user_detail(db_session, target.id)["status"] == "disabled"
    assert account_sessions.get_session_row(db_session, token) is None
    assert service.load_user_context(db_session, target.id) is None


def test_set_user_status_rejects_unknown_status(db_session) -> None:
    user = _create_user(db_session, "test_ua_status_bad")
    with pytest.raises(service.AccountError, match="active 或 disabled"):
        service.set_user_status(
            db_session, user_id=user.id, status="bogus", actor_id=None
        )


# ── 授权装载 ────────────────────────────────────────────────────────


def test_load_user_context_unions_pages_and_takes_highest_tier(db_session) -> None:
    service.create_role(
        db_session,
        code="test_ua_role_ro",
        name="TEST ro",
        api_tier="readonly",
        permissions=["page:dashboard"],
    )
    service.create_role(
        db_session,
        code="test_ua_role_admin",
        name="TEST admin",
        api_tier="admin",
        permissions=["page:spu-roi", "page:users"],
    )
    user = _create_user(
        db_session,
        "test_ua_union",
        roles=("test_ua_role_ro", "test_ua_role_admin"),
    )

    context = service.load_user_context(db_session, user.id)

    assert context is not None
    assert context.pages == {"page:dashboard", "page:spu-roi", "page:users"}
    assert set(context.roles) == {"test_ua_role_ro", "test_ua_role_admin"}
    assert context.api_tier == "admin"  # 多角色取最高档


def test_load_user_context_takes_highest_tier_across_builtin_roles(db_session) -> None:
    user = _create_user(db_session, "test_ua_tier_mix", roles=("viewer", "operator"))
    context = service.load_user_context(db_session, user.id)
    assert context is not None
    assert context.api_tier == "readwrite"  # viewer(readonly) + operator(readwrite)


def test_load_user_context_returns_none_for_disabled_or_missing_user(db_session) -> None:
    user = _create_user(db_session, "test_ua_ctx_none", roles=("viewer",))
    service.set_user_status(
        db_session, user_id=user.id, status="disabled", actor_id=None
    )
    assert service.load_user_context(db_session, user.id) is None
    assert service.load_user_context(db_session, 999999999) is None


# ── 登录认证 ────────────────────────────────────────────────────────


def test_authenticate_normalizes_username_and_rejects_bad_credentials(db_session) -> None:
    user = _create_user(db_session, "test_ua_auth", roles=("viewer",))
    assert service.authenticate(db_session, "TEST_UA_AUTH", CRED) is not None
    assert service.authenticate(db_session, "test_ua_auth", CRED_WRONG) is None
    assert service.authenticate(db_session, "test_ua_nobody", CRED) is None  # 防枚举 dummy 路径

    service.set_user_status(
        db_session, user_id=user.id, status="disabled", actor_id=None
    )
    assert service.authenticate(db_session, "test_ua_auth", CRED) is None  # 禁用不可登录
