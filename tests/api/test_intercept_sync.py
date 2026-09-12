"""Tests for /v2/intercept/sync and /v2/intercept/requests API."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def sample_sync_payload():
    """示例同步数据"""
    return {
        "protocol_version": 1,
        "request_id": "test-sync-001",
        "scope": {
            "seller_id": "123456789",
            "advertiser_id": "987654321",
        },
        "session": {
            "session_id": "session-001",
            "tab_id": 123,
            "tab_url": "https://seller.tiktokglobalshop.com/orders",
            "started_at": "2026-09-12T10:00:00Z",
        },
        "requests": [
            {
                "request_id": "req-001",
                "session_id": "session-001",
                "method": "POST",
                "url": "https://seller.tiktokglobalshop.com/api/v1/orders",
                "endpoint_path": "/api/v1/orders",
                "endpoint_host": "seller.tiktokglobalshop.com",
                "is_whitelisted": True,
                "matched_config_id": 1,
                "request_headers": {"content-type": "application/json"},
                "request_body": {"status": "pending"},
                "response_status": 200,
                "response_status_text": "OK",
                "response_headers": {"content-type": "application/json"},
                "response_body": {"code": 0, "data": {"orders": []}},
                "duration_ms": 156,
                "seller_id": "123456789",
                "advertiser_id": "987654321",
                "captured_at": "2026-09-12T10:00:01Z",
            },
            {
                "request_id": "req-002",
                "session_id": "session-001",
                "method": "GET",
                "url": "https://other-domain.com/some/path",
                "endpoint_path": "/some/path",
                "endpoint_host": "other-domain.com",
                "is_whitelisted": False,
                "response_status": 200,
                "duration_ms": 89,
                "captured_at": "2026-09-12T10:00:02Z",
            },
        ],
    }


class TestSyncAPI:
    """同步 API 测试"""

    def test_sync_requests(self, client: TestClient, sample_sync_payload: dict):
        """测试同步请求数据"""
        resp = client.post("/v2/intercept/sync", json=sample_sync_payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["accepted"] == 2
        assert data["rejected"] == 0
        assert "cursor" in data

    def test_sync_requests_idempotent(self, client: TestClient, sample_sync_payload: dict):
        """测试同步幂等性"""
        # 第一次同步
        resp1 = client.post("/v2/intercept/sync", json=sample_sync_payload)
        assert resp1.json()["accepted"] == 2

        # 第二次同步（相同数据）
        resp2 = client.post("/v2/intercept/sync", json=sample_sync_payload)
        assert resp2.json()["accepted"] == 0  # 已存在，跳过

    def test_sync_requests_invalid_protocol(self, client: TestClient, sample_sync_payload: dict):
        """测试无效协议版本"""
        sample_sync_payload["protocol_version"] = 999
        resp = client.post("/v2/intercept/sync", json=sample_sync_payload)
        assert resp.status_code == 400
        assert "不支持的协议版本" in resp.json()["detail"]

    def test_sync_creates_session(self, client: TestClient, sample_sync_payload: dict):
        """测试同步创建会话"""
        resp = client.post("/v2/intercept/sync", json=sample_sync_payload)
        assert resp.status_code == 200


class TestRequestsAPI:
    """请求记录查询 API 测试"""

    def _sync_sample_data(self, client: TestClient, sample_sync_payload: dict):
        """辅助方法：同步示例数据"""
        client.post("/v2/intercept/sync", json=sample_sync_payload)

    def test_get_requests(self, client: TestClient, sample_sync_payload: dict):
        """测试获取请求列表"""
        self._sync_sample_data(client, sample_sync_payload)

        resp = client.get("/v2/intercept/requests")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["requests"]) == 2

    def test_get_requests_filter_host(self, client: TestClient, sample_sync_payload: dict):
        """测试按域名筛选"""
        self._sync_sample_data(client, sample_sync_payload)

        resp = client.get("/v2/intercept/requests?endpoint_host=tiktok")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    def test_get_requests_filter_whitelisted(self, client: TestClient, sample_sync_payload: dict):
        """测试按白名单筛选"""
        self._sync_sample_data(client, sample_sync_payload)

        resp = client.get("/v2/intercept/requests?is_whitelisted=true")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

        resp = client.get("/v2/intercept/requests?is_whitelisted=false")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    def test_get_requests_filter_method(self, client: TestClient, sample_sync_payload: dict):
        """测试按方法筛选"""
        self._sync_sample_data(client, sample_sync_payload)

        resp = client.get("/v2/intercept/requests?method=POST")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    def test_get_requests_filter_status(self, client: TestClient, sample_sync_payload: dict):
        """测试按状态码筛选"""
        self._sync_sample_data(client, sample_sync_payload)

        resp = client.get("/v2/intercept/requests?status=200")
        assert resp.status_code == 200
        assert resp.json()["total"] == 2

    def test_get_requests_pagination(self, client: TestClient, sample_sync_payload: dict):
        """测试分页"""
        self._sync_sample_data(client, sample_sync_payload)

        resp = client.get("/v2/intercept/requests?limit=1&offset=0")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["requests"]) == 1
        assert data["limit"] == 1
        assert data["offset"] == 0

    def test_get_request_detail(self, client: TestClient, sample_sync_payload: dict):
        """测试获取单条记录详情"""
        self._sync_sample_data(client, sample_sync_payload)

        resp = client.get("/v2/intercept/requests/req-001")
        assert resp.status_code == 200
        data = resp.json()
        assert data["request_id"] == "req-001"
        assert data["method"] == "POST"
        assert data["is_whitelisted"] is True

    def test_get_request_detail_not_found(self, client: TestClient):
        """测试获取不存在的记录"""
        resp = client.get("/v2/intercept/requests/nonexistent")
        assert resp.status_code == 404


class TestStatsAPI:
    """统计 API 测试"""

    def test_get_stats(self, client: TestClient, sample_sync_payload: dict):
        """测试获取统计信息"""
        client.post("/v2/intercept/sync", json=sample_sync_payload)

        resp = client.get("/v2/intercept/requests/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "total" in data
        assert "whitelisted" in data
        assert "by_host" in data
        assert "by_method" in data
        assert "by_status" in data
        assert data["total"] == 2
        assert data["whitelisted"] == 1


class TestConfigForPlugin:
    """配置下发 API 测试"""

    def test_get_config_for_plugin(self, client: TestClient):
        """测试获取配置（插件用）"""
        # 创建配置
        client.post("/v2/intercept/configs", json={
            "domain": "seller.tiktokglobalshop.com",
            "endpoint": "/api/v1/orders",
        })

        resp = client.get("/v2/intercept/config")
        assert resp.status_code == 200
        data = resp.json()
        assert "version" in data
        assert "configs" in data
        assert len(data["configs"]) == 1

    def test_get_config_for_plugin_version_match(self, client: TestClient):
        """测试版本匹配返回 304"""
        resp = client.get("/v2/intercept/config?current_version=1")
        assert resp.status_code == 304

    def test_get_config_for_plugin_only_enabled(self, client: TestClient):
        """测试只返回启用的配置"""
        # 创建启用的配置
        client.post("/v2/intercept/configs", json={
            "domain": "seller.tiktokglobalshop.com",
            "endpoint": "/api/v1/orders",
        })
        # 创建禁用的配置
        create_resp = client.post("/v2/intercept/configs", json={
            "domain": "seller.tiktokglobalshop.com",
            "endpoint": "/api/v1/logistics",
        })
        client.patch(f"/v2/intercept/configs/{create_resp.json()['id']}/toggle", json={"enabled": False})

        resp = client.get("/v2/intercept/config")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["configs"]) == 1
        assert data["configs"][0]["endpoint"] == "/api/v1/orders"
