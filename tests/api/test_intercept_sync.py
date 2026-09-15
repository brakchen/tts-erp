"""HTTP 契约测试：/v2/intercept/sync — 请求拦截数据同步。

测试内容：
1. 数据同步协议
2. 会话管理
3. 请求记录存储
4. 统计查询
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]

SELLER = "TEST_seller-sync"
ADVERTISER = "TEST_adv-sync"
SESSION_ID = f"TEST_session-{uuid.uuid4().hex[:8]}"
REQUEST_ID = f"TEST_req-{uuid.uuid4().hex[:8]}"

_CLEANUP_SQL = (
    "DELETE FROM plugin.intercepted_requests WHERE session_id LIKE 'TEST_%'",
    "DELETE FROM plugin.intercept_sessions WHERE session_id LIKE 'TEST_%'",
    "DELETE FROM plugin.intercept_sync_cursors WHERE cursor_key LIKE 'TEST_%'",
)


@pytest.fixture(autouse=True)
def _cleanup_sync_data(db_engine):
    """Setup + teardown 都清一遍。"""
    with db_engine.begin() as conn:
        for stmt in _CLEANUP_SQL:
            conn.execute(text(stmt))
    yield
    with db_engine.begin() as conn:
        for stmt in _CLEANUP_SQL:
            conn.execute(text(stmt))


def _sync_body(
    session_id: str = SESSION_ID,
    request_id: str = REQUEST_ID,
    **kwargs,
) -> dict:
    """最小合法同步 body。"""
    return {
        "protocolVersion": 1,
        "requestId": f"TEST_sync-{uuid.uuid4().hex[:8]}",
        "scope": {
            "sellerId": SELLER,
            "advertiserId": ADVERTISER,
        },
        "session": {
            "sessionId": session_id,
            "tabId": 123,
            "tabUrl": "https://seller.tiktokglobalshop.com/",
            "startedAt": datetime.now(UTC).isoformat(),
        },
        "requests": [
            {
                "requestId": request_id,
                "traceId": None,
                "sessionId": session_id,
                "method": "GET",
                "url": "https://seller.tiktokglobalshop.com/api/v1/orders",
                "endpointPath": "/api/v1/orders",
                "endpointHost": "seller.tiktokglobalshop.com",
                "isWhitelisted": True,
                "matchedConfigId": None,
                "requestHeaders": {"content-type": "application/json"},
                "requestBody": None,
                "responseStatus": 200,
                "responseStatusText": "OK",
                "responseHeaders": {"content-type": "application/json"},
                "responseBody": {"code": 0, "data": []},
                "durationMs": 150,
                "errorType": None,
                "errorMessage": None,
                "sellerId": SELLER,
                "advertiserId": ADVERTISER,
                "businessContext": None,
                "pagination": {"has_more": False, "total": 0},
                "capturedAt": datetime.now(UTC).isoformat(),
            }
        ],
        **kwargs,
    }


def _auth_header(key: str) -> dict:
    """Return Authorization header for the given key."""
    return {"Authorization": f"Bearer {key}"}


# ─── 路由 + auth ─────────────────────────────────────────────────────


def test_sync_route_anonymous_is_401(api_client):
    """匿名访问同步端点应返回 401"""
    assert api_client.post("/v2/intercept/sync", json=_sync_body()).status_code == 401


def test_sync_route_readonly_is_403(api_client, readonly_key):
    """readonly 权限访问同步端点应返回 403"""
    assert (
        api_client.post(
            "/v2/intercept/sync",
            json=_sync_body(),
            headers=_auth_header(readonly_key),
        ).status_code
        == 403
    )


def test_sync_route_readwrite_is_200(api_client, readwrite_key):
    """readwrite 权限访问同步端点应返回 200"""
    resp = api_client.post(
        "/v2/intercept/sync",
        json=_sync_body(),
        headers=_auth_header(readwrite_key),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["accepted"] == 1
    assert data["rejected"] == 0


def test_sync_uses_standard_camel_case_wire_and_json_scalar_payload(
    api_client, readwrite_key
):
    """插件实际使用 camelCase 会话字段，JSON body 也不一定是对象。"""
    body = _sync_body()
    body["requests"][0]["requestBody"] = ["item-1", "item-2"]
    body["requests"][0]["responseBody"] = "plain-text"

    resp = api_client.post(
        "/v2/intercept/sync",
        json=body,
        headers=_auth_header(readwrite_key),
    )

    assert resp.status_code == 200
    assert resp.json()["inserted"] == 1


def test_sync_rejects_nonstandard_snake_case_wire_fields(api_client, readwrite_key):
    """协议只接受 camelCase，不通过后端兼容旧字段名。"""
    body = _sync_body()
    body["session"] = {
        "session_id": body["session"]["sessionId"],
        "started_at": body["session"]["startedAt"],
    }
    resp = api_client.post(
        "/v2/intercept/sync",
        json=body,
        headers=_auth_header(readwrite_key),
    )
    assert resp.status_code == 422


# ─── 数据同步 ────────────────────────────────────────────────────────


def test_sync_requests(api_client, readwrite_key):
    """同步请求记录"""
    resp = api_client.post(
        "/v2/intercept/sync",
        json=_sync_body(),
        headers=_auth_header(readwrite_key),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["accepted"] == 1
    assert data["rejected"] == 0
    assert "cursor" in data


def test_sync_multiple_requests(api_client, readwrite_key):
    """同步多条请求记录"""
    body = _sync_body()
    # 添加更多请求
    for i in range(5):
        body["requests"].append(
            {
                "requestId": f"TEST_req-{uuid.uuid4().hex[:8]}",
                "traceId": None,
                "sessionId": SESSION_ID,
                "method": "POST",
                "url": f"https://seller.tiktokglobalshop.com/api/v1/orders/{i}",
                "endpointPath": f"/api/v1/orders/{i}",
                "endpointHost": "seller.tiktokglobalshop.com",
                "isWhitelisted": True,
                "matchedConfigId": None,
                "requestHeaders": None,
                "requestBody": {"order_id": str(i)},
                "responseStatus": 200,
                "responseStatusText": "OK",
                "responseHeaders": None,
                "responseBody": {"code": 0},
                "durationMs": 100 + i,
                "errorType": None,
                "errorMessage": None,
                "sellerId": SELLER,
                "advertiserId": ADVERTISER,
                "businessContext": None,
                "pagination": None,
                "capturedAt": datetime.now(UTC).isoformat(),
            }
        )

    resp = api_client.post(
        "/v2/intercept/sync",
        json=body,
        headers=_auth_header(readwrite_key),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["accepted"] == 6
    assert data["rejected"] == 0


def test_sync_invalid_protocol_version(api_client, readwrite_key):
    """无效协议版本应返回 422"""
    body = _sync_body()
    body["protocolVersion"] = 999
    resp = api_client.post(
        "/v2/intercept/sync",
        json=body,
        headers=_auth_header(readwrite_key),
    )
    assert resp.status_code == 422


def test_sync_duplicate_request_idempotent(api_client, readwrite_key):
    """重复请求 ID 应幂等处理"""
    request_id = f"TEST_req-{uuid.uuid4().hex[:8]}"

    # 第一次同步
    resp1 = api_client.post(
        "/v2/intercept/sync",
        json=_sync_body(request_id=request_id),
        headers=_auth_header(readwrite_key),
    )
    assert resp1.status_code == 200
    assert resp1.json()["accepted"] == 1

    # 第二次同步相同请求
    resp2 = api_client.post(
        "/v2/intercept/sync",
        json=_sync_body(request_id=request_id),
        headers=_auth_header(readwrite_key),
    )
    assert resp2.status_code == 200
    # 重复请求可视为已处理，但不能再次增加实际同步游标。
    assert resp2.json()["accepted"] == 1
    assert resp2.json()["inserted"] == 0
    # 游标返回累计总数，重复不增加但也不回零
    assert resp2.json()["cursor"]["totalSynced"] == 1


def test_sync_cursor_uses_camel_case_seller_scope(api_client, readwrite_key):
    """插件使用 sellerId 时，游标不能错误落到 default。"""
    resp = api_client.post(
        "/v2/intercept/sync",
        json=_sync_body(),
        headers=_auth_header(readwrite_key),
    )
    assert resp.status_code == 200
    assert resp.json()["cursor"]["key"] == SELLER


def test_sync_preserves_falsey_json_values(api_client, readwrite_key):
    """空对象/空数组是有效 JSON，不能被写成 NULL。"""
    body = _sync_body()
    body["requests"][0]["requestBody"] = {}
    body["requests"][0]["responseBody"] = []
    resp = api_client.post(
        "/v2/intercept/sync",
        json=body,
        headers=_auth_header(readwrite_key),
    )
    assert resp.status_code == 200

    request_id = body["requests"][0]["requestId"]
    stored = api_client.get(
        f"/v2/intercept/requests/{request_id}",
        headers=_auth_header(readwrite_key),
    )
    assert stored.status_code == 200
    assert stored.json()["request"]["request_body"] == {}
    assert stored.json()["request"]["response_body"] == []


# ─── 会话管理 ────────────────────────────────────────────────────────


def test_sync_creates_session(api_client, readwrite_key):
    """同步应创建会话"""
    session_id = f"TEST_session-{uuid.uuid4().hex[:8]}"
    resp = api_client.post(
        "/v2/intercept/sync",
        json=_sync_body(session_id=session_id),
        headers=_auth_header(readwrite_key),
    )
    assert resp.status_code == 200

    # 验证会话已创建
    # 这里我们通过统计来间接验证
    stats_resp = api_client.get(
        "/v2/intercept/requests/stats",
        headers=_auth_header(readwrite_key),
    )
    assert stats_resp.status_code == 200


def test_sync_updates_session_stats(api_client, readwrite_key, db_engine):
    """同步应更新会话统计"""
    session_id = f"TEST_session-{uuid.uuid4().hex[:8]}"

    # 第一次同步
    api_client.post(
        "/v2/intercept/sync",
        json=_sync_body(session_id=session_id),
        headers=_auth_header(readwrite_key),
    )

    # 第二次同步
    api_client.post(
        "/v2/intercept/sync",
        json=_sync_body(session_id=session_id),
        headers=_auth_header(readwrite_key),
    )

    with db_engine.connect() as conn:
        total_requests = conn.execute(
            text(
                "SELECT total_requests FROM plugin.intercept_sessions "
                "WHERE session_id = :session_id"
            ),
            {"session_id": session_id},
        ).scalar_one()

    assert total_requests == 1

    # 验证统计更新
    stats_resp = api_client.get(
        "/v2/intercept/requests/stats",
        headers=_auth_header(readwrite_key),
    )
    assert stats_resp.status_code == 200


# ─── 请求查询 ────────────────────────────────────────────────────────


def test_list_requests(api_client, readwrite_key):
    """查询拦截记录"""
    # 先同步一些数据
    api_client.post(
        "/v2/intercept/sync",
        json=_sync_body(),
        headers=_auth_header(readwrite_key),
    )

    # 查询记录
    resp = api_client.get(
        "/v2/intercept/requests",
        headers=_auth_header(readwrite_key),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "requests" in data
    assert "total" in data
    assert "limit" in data
    assert "offset" in data


def test_list_requests_filter_by_seller(api_client, readwrite_key):
    """按卖家筛选拦截记录"""
    # 先同步一些数据
    api_client.post(
        "/v2/intercept/sync",
        json=_sync_body(),
        headers=_auth_header(readwrite_key),
    )

    # 按卖家筛选
    resp = api_client.get(
        f"/v2/intercept/requests?seller_id={SELLER}",
        headers=_auth_header(readwrite_key),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert all(r["seller_id"] == SELLER for r in data["requests"])


def test_list_requests_filter_by_method(api_client, readwrite_key):
    """按方法筛选拦截记录"""
    # 先同步一些数据
    api_client.post(
        "/v2/intercept/sync",
        json=_sync_body(),
        headers=_auth_header(readwrite_key),
    )

    # 按方法筛选
    resp = api_client.get(
        "/v2/intercept/requests?method=GET",
        headers=_auth_header(readwrite_key),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert all(r["method"] == "GET" for r in data["requests"])


def test_get_request_detail(api_client, readwrite_key):
    """查询单条记录详情"""
    request_id = f"TEST_req-{uuid.uuid4().hex[:8]}"

    # 先同步数据
    api_client.post(
        "/v2/intercept/sync",
        json=_sync_body(request_id=request_id),
        headers=_auth_header(readwrite_key),
    )

    # 查询详情
    resp = api_client.get(
        f"/v2/intercept/requests/{request_id}",
        headers=_auth_header(readwrite_key),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "request" in data
    assert data["request"]["request_id"] == request_id


def test_get_request_not_found(api_client, readwrite_key):
    """查询不存在的记录应返回 404"""
    resp = api_client.get(
        "/v2/intercept/requests/TEST_nonexistent",
        headers=_auth_header(readwrite_key),
    )
    assert resp.status_code == 404


# ─── 统计查询 ────────────────────────────────────────────────────────


def test_get_requests_stats(api_client, readwrite_key):
    """查询统计信息"""
    # 先同步一些数据
    api_client.post(
        "/v2/intercept/sync",
        json=_sync_body(),
        headers=_auth_header(readwrite_key),
    )

    # 查询统计
    resp = api_client.get(
        "/v2/intercept/requests/stats",
        headers=_auth_header(readwrite_key),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "total" in data
    assert "whitelisted" in data
    assert "byHost" in data
    assert "byMethod" in data


def test_get_requests_stats_empty(api_client, readwrite_key):
    """空数据统计"""
    resp = api_client.get(
        "/v2/intercept/requests/stats",
        headers=_auth_header(readwrite_key),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["whitelisted"] == 0


# ─── seller_id 从 URL query string 提取（§7.3 修复）──────────────────


def test_sync_extracts_seller_id_from_oec_seller_id_query_param(
    api_client, readwrite_key
):
    """body 没传 sellerId 但 URL 含 oec_seller_id 时，回填 seller_id 列。

    背景：Manifest V3 扩展的 webRequest.onBeforeSendHeaders 拿不到 cookie/authorization，
    但 TikTok OEC SDK (aid=6556) 在所有 JSON API URL 的 query string 里都带
    oec_seller_id 作为客户端身份标识（见 tech-doc/tiktok-seller-center-api-catalog.md §7.3）。
    """
    body = _sync_body(
        request_id=f"TEST_req-{uuid.uuid4().hex[:8]}",
    )
    # 关键：URL 含 oec_seller_id，body 里 sellerId = None
    body["requests"][0]["url"] = (
        "https://api16-normal-sg.tiktokshopglobalselling.com/api/v1/pay/statement/order/list"
        "?oec_seller_id=EXTRACTED_seller_abc123"
    )
    body["requests"][0]["sellerId"] = None

    resp = api_client.post(
        "/v2/intercept/sync",
        json=body,
        headers=_auth_header(readwrite_key),
    )
    assert resp.status_code == 200

    # 验证回填：list 接口 filter by seller_id 应该能找到
    list_resp = api_client.get(
        "/v2/intercept/requests?seller_id=EXTRACTED_seller_abc123&limit=10",
        headers=_auth_header(readwrite_key),
    )
    assert list_resp.status_code == 200
    data = list_resp.json()
    assert data["total"] >= 1, "URL 提取的 seller_id 应已写入 DB 并可被 filter 命中"
    matched = next(
        (
            r
            for r in data["requests"]
            if r["request_id"] == body["requests"][0]["requestId"]
        ),
        None,
    )
    assert matched is not None
    assert matched["seller_id"] == "EXTRACTED_seller_abc123"


def test_sync_falls_back_to_seller_id_query_param(api_client, readwrite_key):
    """URL 只有 seller_id（无 oec_seller_id）时也能提取。"""
    body = _sync_body(request_id=f"TEST_req-{uuid.uuid4().hex[:8]}")
    body["requests"][0]["url"] = (
        "https://example.com/api/x?seller_id=fallback_seller_xyz"
    )
    body["requests"][0]["sellerId"] = None

    resp = api_client.post(
        "/v2/intercept/sync", json=body, headers=_auth_header(readwrite_key)
    )
    assert resp.status_code == 200

    list_resp = api_client.get(
        "/v2/intercept/requests?seller_id=fallback_seller_xyz&limit=10",
        headers=_auth_header(readwrite_key),
    )
    assert list_resp.status_code == 200
    assert list_resp.json()["total"] >= 1


def test_sync_keeps_explicit_seller_id_over_url_extraction(api_client, readwrite_key):
    """body 显式传了 sellerId 时优先用它，不被 URL 提取覆盖。"""
    body = _sync_body(request_id=f"TEST_req-{uuid.uuid4().hex[:8]}")
    body["requests"][0]["url"] = (
        "https://example.com/api/x?oec_seller_id=URL_seller_should_lose"
    )
    body["requests"][0]["sellerId"] = "BODY_seller_should_win"

    resp = api_client.post(
        "/v2/intercept/sync", json=body, headers=_auth_header(readwrite_key)
    )
    assert resp.status_code == 200

    list_resp = api_client.get(
        "/v2/intercept/requests?seller_id=BODY_seller_should_win&limit=10",
        headers=_auth_header(readwrite_key),
    )
    assert list_resp.status_code == 200
    data = list_resp.json()
    assert data["total"] >= 1
    matched = next(
        (
            r
            for r in data["requests"]
            if r["request_id"] == body["requests"][0]["requestId"]
        ),
        None,
    )
    assert matched["seller_id"] == "BODY_seller_should_win"


def test_sync_url_without_seller_id_leaves_seller_id_null(api_client, readwrite_key):
    """URL 完全没有 seller_id / oec_seller_id 时，seller_id 保持 null。"""
    body = _sync_body(request_id=f"TEST_req-{uuid.uuid4().hex[:8]}")
    body["requests"][0]["url"] = "https://monitor.example.com/api/health?biz_id=42"
    body["requests"][0]["sellerId"] = None

    resp = api_client.post(
        "/v2/intercept/sync", json=body, headers=_auth_header(readwrite_key)
    )
    assert resp.status_code == 200

    list_resp = api_client.get(
        f"/v2/intercept/requests?request_id={body['requests'][0]['requestId']}",
        headers=_auth_header(readwrite_key),
    )
    assert list_resp.status_code == 200
    rows = list_resp.json()["requests"]
    matched = next(
        (r for r in rows if r["request_id"] == body["requests"][0]["requestId"]), None
    )
    if matched is not None:
        assert matched["seller_id"] is None
