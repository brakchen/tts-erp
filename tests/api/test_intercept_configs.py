"""Tests for /v2/intercept/configs API."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def sample_config():
    """示例配置数据"""
    return {
        "domain": "seller.tiktokglobalshop.com",
        "endpoint": "/api/v1/orders",
        "capture_headers": True,
        "capture_body": True,
        "description": "TikTok 订单列表",
        "tags": ["订单", "物流"],
    }


class TestConfigCRUD:
    """配置 CRUD 测试"""

    def test_create_config(self, client: TestClient, sample_config: dict):
        """测试创建配置"""
        resp = client.post("/v2/intercept/configs", json=sample_config)
        assert resp.status_code == 201
        data = resp.json()
        assert data["domain"] == sample_config["domain"]
        assert data["endpoint"] == sample_config["endpoint"]
        assert data["capture_headers"] == sample_config["capture_headers"]
        assert data["capture_body"] == sample_config["capture_body"]
        assert data["description"] == sample_config["description"]
        assert data["tags"] == sample_config["tags"]
        assert data["enabled"] is True
        assert "id" in data
        assert "created_at" in data
        assert "updated_at" in data

    def test_create_config_duplicate(self, client: TestClient, sample_config: dict):
        """测试创建重复配置"""
        client.post("/v2/intercept/configs", json=sample_config)
        resp = client.post("/v2/intercept/configs", json=sample_config)
        assert resp.status_code == 409
        assert "已存在" in resp.json()["detail"]

    def test_get_config(self, client: TestClient, sample_config: dict):
        """测试获取单个配置"""
        create_resp = client.post("/v2/intercept/configs", json=sample_config)
        config_id = create_resp.json()["id"]

        resp = client.get(f"/v2/intercept/configs/{config_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == config_id

    def test_get_config_not_found(self, client: TestClient):
        """测试获取不存在的配置"""
        resp = client.get("/v2/intercept/configs/99999")
        assert resp.status_code == 404

    def test_update_config(self, client: TestClient, sample_config: dict):
        """测试更新配置"""
        create_resp = client.post("/v2/intercept/configs", json=sample_config)
        config_id = create_resp.json()["id"]

        update_data = {"description": "更新后的描述", "enabled": False}
        resp = client.put(f"/v2/intercept/configs/{config_id}", json=update_data)
        assert resp.status_code == 200
        assert resp.json()["description"] == "更新后的描述"
        assert resp.json()["enabled"] is False

    def test_delete_config(self, client: TestClient, sample_config: dict):
        """测试删除配置"""
        create_resp = client.post("/v2/intercept/configs", json=sample_config)
        config_id = create_resp.json()["id"]

        resp = client.delete(f"/v2/intercept/configs/{config_id}")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

        # 验证已删除
        resp = client.get(f"/v2/intercept/configs/{config_id}")
        assert resp.status_code == 404

    def test_toggle_config(self, client: TestClient, sample_config: dict):
        """测试启停用配置"""
        create_resp = client.post("/v2/intercept/configs", json=sample_config)
        config_id = create_resp.json()["id"]

        # 禁用
        resp = client.patch(f"/v2/intercept/configs/{config_id}/toggle", json={"enabled": False})
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False

        # 启用
        resp = client.patch(f"/v2/intercept/configs/{config_id}/toggle", json={"enabled": True})
        assert resp.status_code == 200
        assert resp.json()["enabled"] is True


class TestConfigList:
    """配置列表测试"""

    def test_list_configs(self, client: TestClient, sample_config: dict):
        """测试获取配置列表"""
        client.post("/v2/intercept/configs", json=sample_config)
        client.post("/v2/intercept/configs", json={
            **sample_config,
            "endpoint": "/api/v1/orders/*",
        })

        resp = client.get("/v2/intercept/configs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["configs"]) == 2

    def test_list_configs_filter_enabled(self, client: TestClient, sample_config: dict):
        """测试按启用状态筛选"""
        create_resp = client.post("/v2/intercept/configs", json=sample_config)
        config_id = create_resp.json()["id"]
        client.patch(f"/v2/intercept/configs/{config_id}/toggle", json={"enabled": False})

        resp = client.get("/v2/intercept/configs?enabled=true")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

        resp = client.get("/v2/intercept/configs?enabled=false")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    def test_list_configs_filter_domain(self, client: TestClient, sample_config: dict):
        """测试按域名筛选"""
        client.post("/v2/intercept/configs", json=sample_config)

        resp = client.get("/v2/intercept/configs?domain=tiktok")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

        resp = client.get("/v2/intercept/configs?domain=example")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_list_configs_pagination(self, client: TestClient, sample_config: dict):
        """测试分页"""
        for i in range(5):
            client.post("/v2/intercept/configs", json={
                **sample_config,
                "endpoint": f"/api/v{i}",
            })

        resp = client.get("/v2/intercept/configs?limit=2&offset=0")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 5
        assert len(data["configs"]) == 2
        assert data["limit"] == 2
        assert data["offset"] == 0


class TestConfigBatch:
    """批量操作测试"""

    def test_batch_enable(self, client: TestClient, sample_config: dict):
        """测试批量启用"""
        ids = []
        for i in range(3):
            resp = client.post("/v2/intercept/configs", json={
                **sample_config,
                "endpoint": f"/api/v{i}",
            })
            ids.append(resp.json()["id"])
            client.patch(f"/v2/intercept/configs/{resp.json()['id']}/toggle", json={"enabled": False})

        resp = client.post("/v2/intercept/configs/batch", json={"action": "enable", "ids": ids})
        assert resp.status_code == 200
        assert resp.json()["affected"] == 3

    def test_batch_disable(self, client: TestClient, sample_config: dict):
        """测试批量禁用"""
        ids = []
        for i in range(3):
            resp = client.post("/v2/intercept/configs", json={
                **sample_config,
                "endpoint": f"/api/v{i}",
            })
            ids.append(resp.json()["id"])

        resp = client.post("/v2/intercept/configs/batch", json={"action": "disable", "ids": ids})
        assert resp.status_code == 200
        assert resp.json()["affected"] == 3

    def test_batch_delete(self, client: TestClient, sample_config: dict):
        """测试批量删除"""
        ids = []
        for i in range(3):
            resp = client.post("/v2/intercept/configs", json={
                **sample_config,
                "endpoint": f"/api/v{i}",
            })
            ids.append(resp.json()["id"])

        resp = client.post("/v2/intercept/configs/batch", json={"action": "delete", "ids": ids})
        assert resp.status_code == 200
        assert resp.json()["affected"] == 3

        resp = client.get("/v2/intercept/configs")
        assert resp.json()["total"] == 0


class TestConfigImportExport:
    """导入导出测试"""

    def test_import_configs(self, client: TestClient, sample_config: dict):
        """测试导入配置"""
        configs = [
            {**sample_config, "endpoint": "/api/v1"},
            {**sample_config, "endpoint": "/api/v2"},
            {**sample_config, "endpoint": "/api/v3"},
        ]
        resp = client.post("/v2/intercept/configs/import", json={"configs": configs})
        assert resp.status_code == 200
        assert resp.json()["imported"] == 3
        assert resp.json()["skipped"] == 0

    def test_import_configs_skip_existing(self, client: TestClient, sample_config: dict):
        """测试导入跳过已存在配置"""
        client.post("/v2/intercept/configs", json=sample_config)

        configs = [
            sample_config,  # 已存在
            {**sample_config, "endpoint": "/api/v2"},  # 新的
        ]
        resp = client.post("/v2/intercept/configs/import", json={"configs": configs})
        assert resp.status_code == 200
        assert resp.json()["imported"] == 1
        assert resp.json()["skipped"] == 1

    def test_export_configs(self, client: TestClient, sample_config: dict):
        """测试导出配置"""
        client.post("/v2/intercept/configs", json=sample_config)

        resp = client.get("/v2/intercept/configs/export")
        assert resp.status_code == 200
        data = resp.json()
        assert "configs" in data
        assert len(data["configs"]) == 1
        assert "Content-Disposition" in resp.headers
