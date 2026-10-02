"""用户名+密码登录与用户会话 API 测试（/v2/auth/* + /v2/users 权限方向）。

覆盖（设计 tech-doc/user-account-authz-design.md §5/§6/§7/§10）：
- 登录成功/失败：统一错误文案、防用户枚举、禁用用户、登录限流 429；
- 会话 cookie 属性：专属名 tts_erp_session、v2.* token、HttpOnly/Path/SameSite；
- GET /v2/auth/me 会话状态；POST /v2/auth/logout 后服务端会话即时失效；
- POST /v2/auth/change-password：旧密码错 400、成功后其他会话失效；
- 页面权限：无 page:users 的用户会话访问 /v2/users、/v2/roles、/v2/pages/users 一律 403
  （只测 403 方向；users.html 渲染由前端任务另行覆盖）；
- API key 行为回归：角色矩阵与凭证语义保持不变。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from tts_erp_v2.accounts import service
from tts_erp_v2.accounts import sessions as account_sessions
from tts_erp_v2.middleware import session_auth

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]

# 测试口令：名字刻意不带 "password"（pi-lens ruff core.toml 开着 S105/S106；
# 仓库 ruff 对 tests/** 已关闭）。
CRED_FRESH = "Newpass9"  # 改密后的新口令
CRED_WRONG = "Wrongpass1"


def _login(client, username: str, credential: str, *, expect: int = 200):
    r = client.post(
        "/v2/auth/login", json={"username": username, "password": credential}
    )
    assert r.status_code == expect, r.text
    return r


def _cookie_value(response) -> str:
    """Extract the raw session-cookie value from a Set-Cookie header."""
    pair = response.headers["set-cookie"].split(";", 1)[0]
    return pair.split("=", 1)[1]


def _me(client) -> dict:
    r = client.get("/v2/auth/me")
    assert r.status_code == 200
    return r.json()


# --------------------------------------------------------------- login success


def test_login_success_sets_session_cookie_and_identity(api_client, ua_user_factory):
    user = ua_user_factory("test_ua_login_ok", roles=("operator",))

    r = _login(api_client, user.username, user.password)

    body = r.json()
    assert body["ok"] is True
    assert body["username"] == user.username
    assert body["displayName"] == user.display_name
    assert body["role"] == "readwrite"  # operator 的 api_tier
    assert "page:dashboard" in body["pages"]
    assert "page:users" not in body["pages"]  # operator 无用户管理页

    set_cookie = r.headers["set-cookie"].lower()
    assert "tts_erp_session=" in set_cookie
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie
    assert "path=/" in set_cookie
    assert "secure" not in set_cookie  # TTS_ERP_SESSION_SECURE=0 in test env
    assert _cookie_value(r).startswith("v2.")  # v2.* 不透明 token


def test_login_cookie_secure_flag_follows_env(api_client, ua_user_factory, monkeypatch):
    monkeypatch.setenv("TTS_ERP_SESSION_SECURE", "1")
    user = ua_user_factory("test_ua_login_secure", roles=("viewer",))

    r = _login(api_client, user.username, user.password)

    assert "secure" in r.headers["set-cookie"].lower()


def test_login_cookie_path_follows_external_prefix(prefixed_client, ua_user_factory):
    user = ua_user_factory("test_ua_login_prefix", roles=("viewer",))

    r = _login(prefixed_client, user.username, user.password)

    assert "path=/tts" in r.headers["set-cookie"].lower()


# --------------------------------------------------------------- login failure


def test_login_failures_share_uniform_error_without_enumeration(
    api_client, ua_user_factory
):
    user = ua_user_factory("test_ua_login_fail", roles=("viewer",))

    wrong_password = _login(api_client, user.username, CRED_WRONG, expect=401)
    unknown_user = _login(api_client, "test_ua_ghost", CRED_WRONG, expect=401)

    # 用户存在与否、密码错与否：状态码与文案完全一致（防用户枚举）。
    assert wrong_password.json() == unknown_user.json() == {"detail": "用户名或密码错误"}
    assert "set-cookie" not in wrong_password.headers
    assert "set-cookie" not in unknown_user.headers


def test_login_disabled_user_gets_uniform_error(api_client, ua_user_factory):
    user = ua_user_factory(
        "test_ua_login_disabled", roles=("viewer",), status="disabled"
    )

    r = _login(api_client, user.username, user.password, expect=401)

    assert r.json() == {"detail": "用户名或密码错误"}
    assert "set-cookie" not in r.headers


def test_login_throttle_returns_429(api_client, monkeypatch):
    monkeypatch.setenv("TTS_ERP_LOGIN_RATE_LIMIT", "3")
    session_auth.reset_login_throttle(3)

    statuses = []
    for _ in range(4):
        r = api_client.post(
            "/v2/auth/login",
            json={"username": "test_ua_throttle", "password": CRED_WRONG},
        )
        statuses.append(r.status_code)

    assert statuses == [401, 401, 401, 429], statuses


def test_login_throttled_response_carries_retry_after(api_client, monkeypatch):
    monkeypatch.setenv("TTS_ERP_LOGIN_RATE_LIMIT", "1")
    session_auth.reset_login_throttle(1)
    api_client.post(
        "/v2/auth/login",
        json={"username": "test_ua_throttle_2", "password": CRED_WRONG},
    )

    r = api_client.post(
        "/v2/auth/login",
        json={"username": "test_ua_throttle_2", "password": CRED_WRONG},
    )

    assert r.status_code == 429
    assert r.json()["detail"] == "too many login attempts"
    assert r.json()["retry_after_s"] >= 1
    assert "set-cookie" not in r.headers


# --------------------------------------------------------------------- whoami


def test_me_unauthenticated(api_client):
    assert _me(api_client) == {"authenticated": False}


def test_me_authenticated_reports_user_context(api_client, ua_user_factory):
    user = ua_user_factory("test_ua_me", roles=("viewer",))
    _login(api_client, user.username, user.password)

    body = _me(api_client)

    assert body["authenticated"] is True
    assert body["username"] == user.username
    assert body["displayName"] == user.display_name
    assert body["role"] == "readonly"  # api_tier 文本
    assert body["pages"] == sorted(
        [
            "page:dashboard",
            "page:focused-spus",
            "page:spu-roi",
            "page:ad-daily",
            "page:intercept-stats",
        ]
    )


# --------------------------------------------------------------------- logout


def test_logout_revokes_session_server_side(api_client, ua_user_factory):
    user = ua_user_factory("test_ua_logout", roles=("operator",))
    _login(api_client, user.username, user.password)
    assert _me(api_client)["authenticated"] is True
    assert (
        api_client.get("/v2/reporting/missing-cost-products?limit=5").status_code == 200
    )

    r = api_client.post("/v2/auth/logout")

    assert r.status_code == 204
    assert _me(api_client) == {"authenticated": False}  # 服务端会话已吊销
    assert (
        api_client.get("/v2/reporting/missing-cost-products?limit=5").status_code == 401
    )


def test_logout_without_cookie_is_idempotent(api_client):
    assert api_client.post("/v2/auth/logout").status_code == 204
    assert _me(api_client) == {"authenticated": False}


# ------------------------------------------------------------ change password


def test_change_password_wrong_old_password_returns_400(api_client, ua_user_factory):
    user = ua_user_factory("test_ua_pw_wrong", roles=("operator",))
    _login(api_client, user.username, user.password)

    r = api_client.post(
        "/v2/auth/change-password",
        json={"oldPassword": CRED_WRONG, "newPassword": CRED_FRESH},
    )

    assert r.status_code == 400
    assert r.json()["detail"] == "当前密码不正确"


def test_change_password_rejects_weak_new_password(api_client, ua_user_factory):
    user = ua_user_factory("test_ua_pw_weak", roles=("operator",))
    _login(api_client, user.username, user.password)

    r = api_client.post(
        "/v2/auth/change-password",
        json={"oldPassword": user.password, "newPassword": "abcdef"},
    )

    assert r.status_code == 400
    assert "需包含大写字母" in r.json()["detail"]  # 密码策略逐条提示


def test_change_password_success_revokes_other_sessions(api_client, ua_user_factory):
    user = ua_user_factory("test_ua_pw_change", roles=("operator",))
    _login(api_client, user.username, user.password)
    token_first = _cookie_value_from_client(api_client)
    _login(api_client, user.username, user.password)
    token_second = _cookie_value_from_client(api_client)

    r = api_client.post(
        "/v2/auth/change-password",
        json={"oldPassword": user.password, "newPassword": CRED_FRESH},
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    # 其他会话全部失效：旧 cookie 立即无效。
    api_client.cookies.set("tts_erp_session", token_first)
    assert _me(api_client) == {"authenticated": False}
    # 当前会话保留。
    api_client.cookies.set("tts_erp_session", token_second)
    assert _me(api_client)["authenticated"] is True

    # 新旧口令的登录行为随之切换。
    api_client.cookies.clear()
    _login(api_client, user.username, CRED_FRESH)
    api_client.cookies.clear()
    _login(api_client, user.username, user.password, expect=401)


def _cookie_value_from_client(client) -> str:
    value = client.cookies.get("tts_erp_session")
    assert value is not None
    return value


def test_change_password_requires_login(api_client):
    r = api_client.post(
        "/v2/auth/change-password",
        json={"oldPassword": CRED_WRONG, "newPassword": CRED_FRESH},
    )
    # 未带任何凭据：中间件在进 handler 前直接 401（统一缺失凭据文案）。
    assert r.status_code == 401
    assert r.json()["detail"]
    assert "set-cookie" not in r.headers


# ------------------------------------------------- session invalidation cases


def test_disabling_user_kills_live_sessions(api_client, ua_user_factory, db_engine):
    user = ua_user_factory("test_ua_disable_live", roles=("operator",))
    _login(api_client, user.username, user.password)
    assert _me(api_client)["authenticated"] is True

    with Session(db_engine) as sess:
        service.set_user_status(
            sess, user_id=user.user_id, status="disabled", actor_id=None
        )

    assert _me(api_client) == {"authenticated": False}


def test_revoked_session_cookie_rejected_immediately(
    api_client, ua_user_factory, ua_session_factory, db_engine
):
    user = ua_user_factory("test_ua_revoke_now", roles=("operator",))
    token = ua_session_factory(user.user_id)
    api_client.cookies.set("tts_erp_session", token)
    assert _me(api_client)["authenticated"] is True

    with Session(db_engine) as sess:
        assert account_sessions.revoke_session(sess, token) is True

    assert _me(api_client) == {"authenticated": False}


def test_expired_session_cookie_rejected(api_client, ua_user_factory, ua_session_factory):
    user = ua_user_factory("test_ua_expired", roles=("operator",))
    token = ua_session_factory(
        user.user_id,
        ttl_seconds=3600,
        now=datetime.now(UTC) - timedelta(hours=2),  # 过期时间已过
    )
    api_client.cookies.set("tts_erp_session", token)

    assert _me(api_client) == {"authenticated": False}
    r = api_client.get(
        "/v2/pages/dashboard", headers={"Accept": "application/json"}
    )
    assert r.status_code == 401


def test_legacy_cookie_value_treated_as_unauthenticated(api_client):
    """旧 api-key-hmac 会话 cookie 一律视为未登录（不做过渡兼容）。"""
    api_client.cookies.set("tts_erp_session", "legacy_hmac_value.signature")

    assert _me(api_client) == {"authenticated": False}
    r = api_client.get(
        "/v2/pages/dashboard", headers={"Accept": "application/json"}
    )
    assert r.status_code == 401


# ------------------------------------------------------------ page permission


def test_user_without_users_page_gets_403_on_user_apis(api_client, ua_user_factory):
    user = ua_user_factory("test_ua_no_users_page", roles=("operator",))
    _login(api_client, user.username, user.password)

    for path in ("/v2/users", "/v2/roles"):
        r = api_client.get(path)
        assert r.status_code == 403, (path, r.text)
        assert r.json()["detail"].startswith("requires")


def test_users_page_requires_page_users_permission(api_client, ua_user_factory):
    # operator（readwrite）过得了路由角色矩阵，但没有 page:users → 403，
    # 这里隔离验证的就是页面权限点方向。
    user = ua_user_factory("test_ua_users_page_403", roles=("operator",))
    _login(api_client, user.username, user.password)

    r = api_client.get("/v2/pages/users")
    assert r.status_code == 403
    assert r.json()["detail"] == "requires page:users"

    html = api_client.get("/v2/pages/users", headers={"Accept": "text/html"})
    assert html.status_code == 403
    assert html.headers["content-type"].startswith("text/html")
    assert "无权访问" in html.text


def test_admin_tier_without_users_page_still_denied_on_user_apis(
    api_client, ua_user_factory, ua_role_factory
):
    """api_tier=admin 但无 page:users：403 必须来自页面权限点而非角色矩阵。"""
    ua_role_factory(
        "test_ua_role_no_users",
        api_tier="admin",
        permissions=("page:dashboard", "page:spu-roi"),
    )
    user = ua_user_factory("test_ua_tier_admin", roles=("test_ua_role_no_users",))
    _login(api_client, user.username, user.password)

    for path in ("/v2/users", "/v2/roles"):
        r = api_client.get(path)
        assert r.status_code == 403, (path, r.text)
        assert r.json()["detail"] == "requires page:users"


def test_admin_session_can_use_user_and_role_apis(api_client, ua_user_factory):
    user = ua_user_factory("test_ua_admin_api", roles=("admin",))
    _login(api_client, user.username, user.password)

    users_body = api_client.get("/v2/users").json()
    assert any(row["username"] == user.username for row in users_body["users"])

    roles_response = api_client.get("/v2/roles")
    assert roles_response.status_code == 200
    roles_body = roles_response.json()
    codes = {row["code"] for row in roles_body["roles"]}
    assert {"admin", "operator", "viewer"} <= codes  # 内置角色来自 0052 种子
    assert any(item["code"].startswith("page:") for item in roles_body["allPermissions"])


# ---------------------------------------------------------- API key regression


def test_api_key_user_apis_stay_key_role_gated(api_client, admin_key, readwrite_key):
    """API key 不受 page:users 权限点约束，路由角色矩阵（admin）保持不变。"""
    ok = api_client.get("/v2/users", headers={"X-API-Key": admin_key})
    assert ok.status_code == 200

    denied = api_client.get("/v2/users", headers={"X-API-Key": readwrite_key})
    assert denied.status_code == 403
    assert denied.json()["detail"] == "requires admin"


def test_api_key_data_routes_role_matrix_unchanged(api_client, readonly_key):
    ok = api_client.get(
        "/v2/reporting/missing-cost-products?limit=5",
        headers={"X-API-Key": readonly_key},
    )
    assert ok.status_code == 200

    denied = api_client.post(
        "/v2/reporting/manual-costs",
        json={"spu_id": "TEST_ua_key_ro", "unit_cost": "1", "currency": "USD"},
        headers={"X-API-Key": readonly_key, "X-Requested-With": "tts-erp"},
    )
    assert denied.status_code == 403
    assert denied.json()["detail"] == "requires readwrite"


def test_missing_or_invalid_credentials_get_401(api_client, bad_key):
    assert api_client.get("/v2/users").status_code == 401
    r = api_client.get("/v2/users", headers={"X-API-Key": bad_key})
    assert r.status_code == 401
    assert "location" not in r.headers  # API 形态不走浏览器 302
