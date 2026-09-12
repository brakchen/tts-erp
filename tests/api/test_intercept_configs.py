"""HTTP 契约测试：/v2/intercept/configs — 请求拦截配置管理。

测试内容：
1. 配置 CRUD 操作
2. 启停用切换
3. 批量操作
4. 导入/导出
5. 配置下发
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]

DOMAIN = "TEST_seller.tiktokglobalshop.com"
ENDPOINT = "/api/v1/orders"

_CLEANUP_SQL = (
    "DELETE FROM plugin.intercept_configs WHERE domain LIKE 'TEST_%'",
)


@pytest.fixture(autouse=True)
def _cleanup_configs(db_engine):
    """Setup + teardown 都清一遍。"""
    with db_engine.begin() as conn:
        for stmt in _CLEANUP_SQL:
            conn.execute(text(stmt))
    yield
    with db_engine.begin() as conn:
        for stmt in _CLEANUP_SQL:
            conn.execute(text(stmt))


def _config_body(
    domain: str = DOMAIN,
    endpoint: str = ENDPOINT,
    **kwargs,
) -> dict:
    """最小合法配置 body。"""
    return {
        "domain": domain,
        "endpoint": endpoint,
        "capture_headers": True,
        "capture_body": True,
        "description": "Test config",
        "tags": ["test"],
        "enabled": True,
        **kwargs,
    }


def _auth_header(key: str) -> dict:
    """Return Authorization header for the given key."""
    return {"Authorization": f"Bearer {key}"}


# ─── 路由 + auth ─────────────────────────────────────────────────────


def test_configs_route_anonymous_is_401(api_client):
    """匿名访问配置列表应返回 401"""
    assert api_client.get("/v2/intercept/configs").status_code == 401


def test_configs_route_readonly_is_403(api_client, readonly_key):
    """readonly 权限访问配置管理应返回 403"""
    resp = api_client.get(
        "/v2/intercept/configs",
        headers=_auth_header(readonly_key),
    )
    assert resp.status_code == 403


def test_configs_route_readwrite_is_200(api_client, readwrite_key):
    """readwrite 权限访问配置列表应返回 200"""
    resp = api_client.get(
        "/v2/intercept/configs",
        headers=_auth_header(readwrite_key),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "configs" in data
    assert "total" in data


# ─── CRUD 操作 ───────────────────────────────────────────────────────


def test_create_config(api_client, readwrite_key):
    """创建配置"""
    resp = api_client.post(
        "/v2/intercept/configs",
        json=_config_body(),
        headers=_auth_header(readwrite_key),
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "config" in data
    config = data["config"]
    assert config["domain"] == DOMAIN
    assert config["endpoint"] == ENDPOINT
    assert config["enabled"] is True


def test_create_config_duplicate_conflict(api_client, readwrite_key):
    """创建重复配置应返回 409"""
    api_client.post(
        "/v2/intercept/configs",
        json=_config_body(),
        headers=_auth_header(readwrite_key),
    )
    resp = api_client.post(
        "/v2/intercept/configs",
        json=_config_body(),
        headers=_auth_header(readwrite_key),
    )
    assert resp.status_code == 409


def test_get_config(api_client, readwrite_key):
    """查询单个配置"""
    # 创建配置
    create_resp = api_client.post(
        "/v2/intercept/configs",
        json=_config_body(),
        headers=_auth_header(readwrite_key),
    )
    config_id = create_resp.json()["config"]["id"]

    # 查询配置
    resp = api_client.get(
        f"/v2/intercept/configs/{config_id}",
        headers=_auth_header(readwrite_key),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["config"]["id"] == config_id


def test_get_config_not_found(api_client, readwrite_key):
    """查询不存在的配置应返回 404"""
    resp = api_client.get(
        "/v2/intercept/configs/999999",
        headers=_auth_header(readwrite_key),
    )
    assert resp.status_code == 404


def test_update_config(api_client, readwrite_key):
    """更新配置"""
    # 创建配置
    create_resp = api_client.post(
        "/v2/intercept/configs",
        json=_config_body(),
        headers=_auth_header(readwrite_key),
    )
    config_id = create_resp.json()["config"]["id"]

    # 更新配置
    update_body = _config_body(description="Updated description")
    resp = api_client.put(
        f"/v2/intercept/configs/{config_id}",
        json=update_body,
        headers=_auth_header(readwrite_key),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["config"]["description"] == "Updated description"


def test_delete_config(api_client, readwrite_key):
    """删除配置"""
    # 创建配置
    create_resp = api_client.post(
        "/v2/intercept/configs",
        json=_config_body(),
        headers=_auth_header(readwrite_key),
    )
    config_id = create_resp.json()["config"]["id"]

    # 删除配置
    resp = api_client.delete(
        f"/v2/intercept/configs/{config_id}",
        headers=_auth_header(readwrite_key),
    )
    assert resp.status_code == 200
    assert resp.json()["success"] is True

    # 确认已删除
    resp = api_client.get(
        f"/v2/intercept/configs/{config_id}",
        headers=_auth_header(readwrite_key),
    )
    assert resp.status_code == 404


def test_toggle_config(api_client, readwrite_key):
    """启停用配置"""
    # 创建配置
    create_resp = api_client.post(
        "/v2/intercept/configs",
        json=_config_body(),
        headers=_auth_header(readwrite_key),
    )
    config_id = create_resp.json()["config"]["id"]

    # 禁用配置
    resp = api_client.patch(
        f"/v2/intercept/configs/{config_id}/toggle",
        json={"enabled": False},
        headers=_auth_header(readwrite_key),
    )
    assert resp.status_code == 200
    assert resp.json()["config"]["enabled"] is False

    # 启用配置
    resp = api_client.patch(
        f"/v2/intercept/configs/{config_id}/toggle",
        json={"enabled": True},
        headers=_auth_header(readwrite_key),
    )
    assert resp.status_code == 200
    assert resp.json()["config"]["enabled"] is True


# ─── 批量操作 ────────────────────────────────────────────────────────


def test_batch_enable_configs(api_client, readwrite_key):
    """批量启用配置"""
    # 创建配置
    create_resp1 = api_client.post(
        "/v2/intercept/configs",
        json=_config_body(domain="TEST_domain1.com", endpoint="/api/1"),
        headers=_auth_header(readwrite_key),
    )
    create_resp2 = api_client.post(
        "/v2/intercept/configs",
        json=_config_body(domain="TEST_domain2.com", endpoint="/api/2"),
        headers=_auth_header(readwrite_key),
    )
    config_id1 = create_resp1.json()["config"]["id"]
    config_id2 = create_resp2.json()["config"]["id"]

    # 批量禁用
    api_client.patch(
        f"/v2/intercept/configs/{config_id1}/toggle",
        json={"enabled": False},
        headers=_auth_header(readwrite_key),
    )
    api_client.patch(
        f"/v2/intercept/configs/{config_id2}/toggle",
        json={"enabled": False},
        headers=_auth_header(readwrite_key),
    )

    # 批量启用
    resp = api_client.post(
        "/v2/intercept/configs/batch",
        json={"action": "enable", "ids": [config_id1, config_id2]},
        headers=_auth_header(readwrite_key),
    )
    assert resp.status_code == 200
    assert resp.json()["affected"] == 2


def test_batch_delete_configs(api_client, readwrite_key):
    """批量删除配置"""
    # 创建配置
    create_resp1 = api_client.post(
        "/v2/intercept/configs",
        json=_config_body(domain="TEST_domain1.com", endpoint="/api/1"),
        headers=_auth_header(readwrite_key),
    )
    create_resp2 = api_client.post(
        "/v2/intercept/configs",
        json=_config_body(domain="TEST_domain2.com", endpoint="/api/2"),
        headers=_auth_header(readwrite_key),
    )
    config_id1 = create_resp1.json()["config"]["id"]
    config_id2 = create_resp2.json()["config"]["id"]

    # 批量删除
    resp = api_client.post(
        "/v2/intercept/configs/batch",
        json={"action": "delete", "ids": [config_id1, config_id2]},
        headers=_auth_header(readwrite_key),
    )
    assert resp.status_code == 200
    assert resp.json()["affected"] == 2


# ─── 导入/导出 ──────────────────────────────────────────────────────


def test_import_configs(api_client, readwrite_key):
    """批量导入配置"""
    configs = [
        _config_body(domain="TEST_import1.com", endpoint="/api/1"),
        _config_body(domain="TEST_import2.com", endpoint="/api/2"),
        _config_body(domain="TEST_import3.com", endpoint="/api/3"),
    ]

    resp = api_client.post(
        "/v2/intercept/configs/import",
        json={"configs": configs},
        headers=_auth_header(readwrite_key),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["imported"] == 3
    assert data["skipped"] == 0
    assert len(data["errors"]) == 0


def test_import_configs_skip_existing(api_client, readwrite_key):
    """导入已存在的配置应跳过"""
    # 创建配置
    api_client.post(
        "/v2/intercept/configs",
        json=_config_body(),
        headers=_auth_header(readwrite_key),
    )

    # 尝试导入相同配置
    resp = api_client.post(
        "/v2/intercept/configs/import",
        json={"configs": [_config_body()]},
        headers=_auth_header(readwrite_key),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["imported"] == 0
    assert data["skipped"] == 1


def test_export_configs(api_client, readwrite_key):
    """导出配置"""
    # 创建配置
    api_client.post(
        "/v2/intercept/configs",
        json=_config_body(),
        headers=_auth_header(readwrite_key),
    )

    # 导出配置
    resp = api_client.get(
        "/v2/intercept/configs/export",
        headers=_auth_header(readwrite_key),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "configs" in data
    assert len(data["configs"]) > 0


# ─── 配置下发 ────────────────────────────────────────────────────────


def test_get_config_for_plugin(api_client, readwrite_key):
    """插件拉取配置"""
    # 创建配置
    api_client.post(
        "/v2/intercept/configs",
        json=_config_body(),
        headers=_auth_header(readwrite_key),
    )

    # 拉取配置
    resp = api_client.get(
        "/v2/intercept/config",
        headers=_auth_header(readwrite_key),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "version" in data
    assert "configs" in data
    assert len(data["configs"]) > 0


def test_get_config_for_plugin_only_enabled(api_client, readwrite_key):
    """插件拉取配置只返回启用的配置"""
    # 创建启用的配置
    api_client.post(
        "/v2/intercept/configs",
        json=_config_body(domain="TEST_enabled.com", endpoint="/api/enabled"),
        headers=_auth_header(readwrite_key),
    )

    # 创建禁用的配置
    create_resp = api_client.post(
        "/v2/intercept/configs",
        json=_config_body(domain="TEST_disabled.com", endpoint="/api/disabled"),
        headers=_auth_header(readwrite_key),
    )
    config_id = create_resp.json()["config"]["id"]
    api_client.patch(
        f"/v2/intercept/configs/{config_id}/toggle",
        json={"enabled": False},
        headers=_auth_header(readwrite_key),
    )

    # 拉取配置
    resp = api_client.get(
        "/v2/intercept/config",
        headers=_auth_header(readwrite_key),
    )
    assert resp.status_code == 200
    data = resp.json()
    configs = data["configs"]
    assert all(c["enabled"] for c in configs)


# ─── 列表筛选 ────────────────────────────────────────────────────────


def test_list_configs_filter_by_enabled(api_client, readwrite_key):
    """按启用状态筛选配置"""
    # 创建启用的配置
    api_client.post(
        "/v2/intercept/configs",
        json=_config_body(domain="TEST_enabled.com", endpoint="/api/enabled"),
        headers=_auth_header(readwrite_key),
    )

    # 创建禁用的配置
    create_resp = api_client.post(
        "/v2/intercept/configs",
        json=_config_body(domain="TEST_disabled.com", endpoint="/api/disabled"),
        headers=_auth_header(readwrite_key),
    )
    config_id = create_resp.json()["config"]["id"]
    api_client.patch(
        f"/v2/intercept/configs/{config_id}/toggle",
        json={"enabled": False},
        headers=_auth_header(readwrite_key),
    )

    # 筛选启用的配置
    resp = api_client.get(
        "/v2/intercept/configs?enabled=true",
        headers=_auth_header(readwrite_key),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert all(c["enabled"] for c in data["configs"])


def test_list_configs_filter_by_domain(api_client, readwrite_key):
    """按域名筛选配置"""
    # 创建配置
    api_client.post(
        "/v2/intercept/configs",
        json=_config_body(domain="TEST_domain1.com", endpoint="/api/1"),
        headers=_auth_header(readwrite_key),
    )
    api_client.post(
        "/v2/intercept/configs",
        json=_config_body(domain="TEST_domain2.com", endpoint="/api/2"),
        headers=_auth_header(readwrite_key),
    )

    # 筛选域名
    resp = api_client.get(
        "/v2/intercept/configs?domain=TEST_domain1.com",
        headers=_auth_header(readwrite_key),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert all(c["domain"] == "TEST_domain1.com" for c in data["configs"])
