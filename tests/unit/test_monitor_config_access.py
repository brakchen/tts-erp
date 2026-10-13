"""monitor_config 受限 API key：用途/店铺绑定在统一认证 owner 中的表达与跨路由拒绝。

契约来源：docs/monitor-config-sync.md §4.2（店铺专用 Token）与 §10 阶段 A
（"固定 schema、迁移、受限 Token 和跨路由拒绝"）。阶段 B 才引入下发 GET 路由
handler；这里只锁定 access 层的判定，使普通 key 与 monitor key 互不越权。
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Iterator

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from tts_erp_v2.access import (
    AccessEffect,
    AccessRequest,
    ApiKeyPurpose,
    AuthMode,
    Role,
    authenticate_key,
    authenticate_monitor_delivery_key,
    clear_credential_cache,
    evaluate_access,
    is_monitor_config_delivery_route,
    required_role,
)
from tts_erp_v2.db.base import get_session_factory
from tts_erp_v2.db.models.commerce import ChannelAccount
from tts_erp_v2.db.models.security import ApiKey
from tts_erp_v2.middleware.rate_limit import reset_shared

SHOP_ID = "TEST_monitor_config_seller_a"
DELIVERY_PATH = "/v2/monitor/config"


@pytest.fixture()
def monitor_shop(db_engine) -> Iterator[int]:
    with Session(db_engine) as session:
        session.execute(delete(ChannelAccount).where(ChannelAccount.shop_id == SHOP_ID))
        shop = ChannelAccount(
            platform="tiktok", shop_id=SHOP_ID, account_name="TEST_monitor_config"
        )
        session.add(shop)
        session.commit()
        shop_pk = shop.id
    yield shop_pk
    with Session(db_engine) as session:
        session.execute(delete(ApiKey).where(ApiKey.name.like("TEST_monitor_config%")))
        session.execute(delete(ChannelAccount).where(ChannelAccount.shop_id == SHOP_ID))
        session.commit()
    clear_credential_cache()


def _issue(
    session: Session,
    *,
    plaintext: str,
    name: str,
    role: str = "readonly",
    purpose: str = "general",
    shop_pk: int | None = None,
) -> None:
    session.add(
        ApiKey(
            key_hash=hashlib.sha256(plaintext.encode()).hexdigest(),
            key_prefix=plaintext[:16],
            name=name,
            role=role,
            status="active",
            purpose=purpose,
            shop_pk=shop_pk,
        )
    )
    session.commit()


@pytest.fixture()
def monitor_key(db_engine, monitor_shop: int) -> Iterator[str]:
    plaintext = "TEST_monitor_config_shop_key"
    with Session(db_engine) as session:
        _issue(
            session,
            plaintext=plaintext,
            name="TEST_monitor_config_shop",
            purpose="monitor_config",
            shop_pk=monitor_shop,
        )
    clear_credential_cache()
    yield plaintext
    clear_credential_cache()


@pytest.fixture()
def general_key(db_engine) -> Iterator[str]:
    plaintext = "TEST_monitor_config_general_key"
    with Session(db_engine) as session:
        _issue(session, plaintext=plaintext, name="TEST_monitor_config_general")
    clear_credential_cache()
    yield plaintext
    clear_credential_cache()


def _set_key_state(
    plaintext: str,
    *,
    status: str | None = None,
    rotated_to: str | None = None,
) -> None:
    """Mutate one issued key behind the process-local credential cache.

    Uses its own committed session so the change is visible to the cache-free
    delivery lookup without disturbing the test's own transaction.
    """

    key_hash = hashlib.sha256(plaintext.encode()).hexdigest()
    session_factory = get_session_factory()
    with session_factory() as session:
        row = session.execute(
            select(ApiKey).where(ApiKey.key_hash == key_hash)
        ).scalar_one()
        if status is not None:
            row.status = status
        row.rotated_to_key_hash = rotated_to
        session.commit()


def _request(
    path: str, *, key: str | None = None, method: str = "GET"
) -> AccessRequest:
    return AccessRequest(
        method=method,
        route_path=path,
        accepts_html=False,
        client_ip="127.0.0.1",
        bearer_key=key,
    )


def _decide(request: AccessRequest, mode: AuthMode = AuthMode.ENFORCE):
    reset_shared(limit=100)
    return asyncio.run(evaluate_access(request, mode=mode))


# ─── 用途/店铺绑定进入 typed credential ───────────────────────────


def test_legacy_key_resolves_to_general_purpose_without_shop(
    general_key: str,
) -> None:
    credential = authenticate_key(general_key)
    assert credential is not None
    assert credential.purpose is ApiKeyPurpose.GENERAL
    assert credential.shop_pk is None


def test_monitor_key_resolves_to_monitor_purpose_with_shop(
    monitor_key: str, monitor_shop: int
) -> None:
    credential = authenticate_key(monitor_key)
    assert credential is not None
    assert credential.purpose is ApiKeyPurpose.MONITOR_CONFIG
    assert credential.shop_pk == monitor_shop


def test_monitor_purpose_without_shop_binding_is_not_usable() -> None:
    """CHECK 之外的第二道防线：解不出店铺的 monitor key 不得退化成通用凭证。

    0070 的 CHECK 已经使该行无法入库（见 persistence 测试），所以这里直接
    验证 access owner 的判定，证明它不依赖数据库约束。
    """

    from tts_erp_v2.access._credentials import _purpose_of

    assert _purpose_of("monitor_config", None) is None
    assert _purpose_of("monitor_config", 7) is ApiKeyPurpose.MONITOR_CONFIG
    assert _purpose_of(None, None) is ApiKeyPurpose.GENERAL
    assert _purpose_of("general", None) is ApiKeyPurpose.GENERAL
    assert _purpose_of("not_a_real_purpose", None) is None


def test_grant_carries_purpose_and_shop_binding(
    monitor_key: str, monitor_shop: int
) -> None:
    decision = _decide(_request("/v2/commerce/shops", key=monitor_key))
    # monitor key 不能读普通只读数据；但 grant 仍需把绑定透出给下游。
    assert decision.effect is AccessEffect.DENY
    assert decision.grant.purpose is ApiKeyPurpose.MONITOR_CONFIG
    assert decision.grant.shop_pk == monitor_shop


# ─── 路由分类 ────────────────────────────────────────────────────


def test_delivery_route_is_readonly_and_other_monitor_paths_fail_closed() -> None:
    assert is_monitor_config_delivery_route("GET", DELIVERY_PATH)
    assert not is_monitor_config_delivery_route("POST", DELIVERY_PATH)
    assert not is_monitor_config_delivery_route("GET", "/v2/monitor/other")

    assert required_role("GET", DELIVERY_PATH) is Role.READONLY
    assert required_role("POST", DELIVERY_PATH) is Role.ADMIN
    assert required_role("GET", "/v2/monitor/unknown") is Role.ADMIN


# ─── 跨路由拒绝 ──────────────────────────────────────────────────


def test_shop_bound_monitor_key_reads_its_delivery_route(monitor_key: str) -> None:
    decision = _decide(_request(DELIVERY_PATH, key=monitor_key))
    assert decision.effect is AccessEffect.ALLOW
    assert decision.required_role is Role.READONLY


def test_monitor_key_cannot_read_other_readonly_data(monitor_key: str) -> None:
    for path in (
        "/v2/reporting/profit-daily",
        "/v2/commerce/shops",
        "/v2/intercept/configs",
        "/v2/video-publish/tasks",
    ):
        decision = _decide(_request(path, key=monitor_key))
        assert decision.effect is AccessEffect.DENY, path
        assert decision.status == 403, path


def test_monitor_key_cannot_reach_management_or_secret_paths(
    monitor_key: str,
) -> None:
    for method, path in (
        ("GET", "/v2/pages/runtime-configs"),
        ("GET", "/v2/config/runtime"),
        ("POST", "/v2/reporting/manual-costs"),
        ("GET", "/v2/llm-context"),
    ):
        decision = _decide(_request(path, key=monitor_key, method=method))
        assert decision.effect is AccessEffect.DENY, path
        assert decision.status == 403, path


def test_general_key_cannot_use_the_delivery_route(general_key: str) -> None:
    """普通 readonly/admin key 不是店铺绑定授权（docs §4.2 约束 4）。"""

    decision = _decide(_request(DELIVERY_PATH, key=general_key))
    assert decision.effect is AccessEffect.DENY
    assert decision.status == 403


def test_browser_admin_session_cannot_use_the_delivery_route(
    ua_user_factory, ua_session_factory, monitor_shop: int
) -> None:
    """登录 cookie 不是店铺绑定授权（docs §4.2 约束 4），即使角色是 admin。"""

    user = ua_user_factory("test_ua_monitor_config_admin", roles=("admin",))
    token = ua_session_factory(user.user_id)
    request = AccessRequest(
        method="GET",
        route_path=DELIVERY_PATH,
        accepts_html=False,
        client_ip="127.0.0.1",
        session_cookie=token,
    )
    decision = _decide(request)
    assert decision.grant.auth_method == "cookie"
    assert decision.effect is AccessEffect.DENY
    assert decision.status == 403


def test_anonymous_request_gets_401_not_a_bypass_on_the_delivery_route() -> None:
    decision = _decide(_request(DELIVERY_PATH))
    assert decision.effect is AccessEffect.DENY
    assert decision.status == 401


def test_auth_off_does_not_bypass_the_delivery_route() -> None:
    """auth=off 的匿名放行不得成为释放下发的通道（docs §4.2 约束 4）。"""

    decision = _decide(_request(DELIVERY_PATH), mode=AuthMode.OFF)
    assert decision.effect is AccessEffect.DENY
    assert decision.status == 403


def test_shadow_mode_does_not_relax_the_delivery_route() -> None:
    decision = _decide(_request(DELIVERY_PATH), mode=AuthMode.SHADOW)
    assert decision.effect is AccessEffect.DENY
    assert decision.status == 403


def test_monitor_key_is_denied_in_shadow_mode_on_other_routes(
    monitor_key: str,
) -> None:
    decision = _decide(
        _request("/v2/reporting/profit-daily", key=monitor_key),
        mode=AuthMode.SHADOW,
    )
    assert decision.effect is AccessEffect.DENY
    assert decision.status == 403


def test_shadow_mode_still_allows_ordinary_routes_for_the_same_key() -> None:
    """受限 key 不应改变其他路由的既有 shadow 语义。"""

    decision = _decide(_request("/v2/commerce/shops", key=None), mode=AuthMode.SHADOW)
    assert decision.effect is AccessEffect.SHADOW_ALLOW


def test_general_key_keeps_ordinary_readonly_access(general_key: str) -> None:
    decision = _decide(_request("/v2/reporting/profit-daily", key=general_key))
    assert decision.effect is AccessEffect.ALLOW
    assert decision.grant.purpose is ApiKeyPurpose.GENERAL
    assert decision.grant.shop_pk is None


# ─── 下发边界的实时失效（§4.2 约束 5）──────────────────────────
# 认证中间件走 <=60s 正缓存；专用下发边界必须在缓存成功后仍然核对当前
# 状态，否则被禁用/轮换的 token 还能存活一个 TTL。


def test_cache_would_hide_a_disabled_key(monitor_key: str) -> None:
    """前提：该 gap 真实存在——缓存仍返回旧的认证成功。"""

    assert authenticate_key(monitor_key) is not None
    _set_key_state(monitor_key, status="disabled")

    # 通用路径仍被正缓存蒙住（这正是下发边界必须自己核对的原因）。
    assert authenticate_key(monitor_key) is not None

    # 下发边界不受缓存影响，立即拒绝。
    assert authenticate_monitor_delivery_key(monitor_key) is None


def test_disabled_monitor_key_is_rejected_immediately(monitor_key: str) -> None:
    assert authenticate_monitor_delivery_key(monitor_key) is not None
    _set_key_state(monitor_key, status="disabled")
    assert authenticate_monitor_delivery_key(monitor_key) is None


def test_rotated_monitor_key_is_rejected_immediately(monitor_key: str) -> None:
    """轮换后的旧 key 立即失效，不再靠 status 变化。"""

    assert authenticate_monitor_delivery_key(monitor_key) is not None
    _set_key_state(monitor_key, rotated_to="0" * 64)
    assert authenticate_monitor_delivery_key(monitor_key) is None


def test_cache_would_hide_a_rotated_key(monitor_key: str) -> None:
    assert authenticate_key(monitor_key) is not None
    _set_key_state(monitor_key, rotated_to="0" * 64)
    assert authenticate_key(monitor_key) is not None
    assert authenticate_monitor_delivery_key(monitor_key) is None


def test_monitor_delivery_primitive_refuses_general_keys(general_key: str) -> None:
    """专用边界不得成为通用 key 的旁路。"""

    assert authenticate_key(general_key) is not None
    assert authenticate_monitor_delivery_key(general_key) is None


def test_monitor_delivery_primitive_returns_the_shop_binding(
    monitor_key: str, monitor_shop: int
) -> None:
    credential = authenticate_monitor_delivery_key(monitor_key)
    assert credential is not None
    assert credential.purpose is ApiKeyPurpose.MONITOR_CONFIG
    assert credential.shop_pk == monitor_shop


def test_monitor_delivery_primitive_rejects_unknown_keys() -> None:
    assert authenticate_monitor_delivery_key("TEST_monitor_config_never_issued") is None


def test_delivery_primitive_evicts_a_stale_positive_entry(monitor_key: str) -> None:
    assert authenticate_key(monitor_key) is not None
    _set_key_state(monitor_key, status="disabled")

    assert authenticate_monitor_delivery_key(monitor_key) is None
    # 陈旧正缓存不得继续存活：通用路径随后也必须重新核对。
    clear_credential_cache()
    assert authenticate_key(monitor_key) is None
