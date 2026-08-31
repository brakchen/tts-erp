"""miaoshou.callbacks 单元测试 —— 17 个节点 + /callback/all 派发."""

from __future__ import annotations

import pytest

from miaoshou.callbacks.router import (  # type: ignore[reportMissingImports]
    NODE_REGISTRY,
    all_node_paths,
    dispatch_callback,
    path_for_order_status,
)

pytestmark = [pytest.mark.domain_miaoshou, pytest.mark.layer_unit]


def test_node_registry_count():
    """17+1（aftersale 拆 wait+process）= 18 个节点."""
    assert len(NODE_REGISTRY) >= 17
    assert len(NODE_REGISTRY) == 18


def test_all_node_paths_unique():
    paths = all_node_paths()
    assert len(paths) == len(NODE_REGISTRY)
    assert len(set(paths)) == len(paths)
    for p in paths:
        assert p.startswith("/miaoshou/callback/")


def test_path_for_order_status_roundtrip():
    for order_status, (_model_cls, alias) in NODE_REGISTRY.items():
        assert path_for_order_status(order_status) == f"/miaoshou/callback/{alias}"


def test_path_for_unknown():
    assert path_for_order_status("nonexistent_node") is None


def test_dispatch_unknown_order_status():
    status, body = dispatch_callback("never_heard_of_it", {})
    assert status == 400
    assert body["code"] == 400
    assert "unknown orderStatus" in body["message"]
    assert "supported" in body


def test_dispatch_service_node_ok():
    """最小化对接必接：service_node 节点 payload."""
    payload = {
        "orderStatus": "service_node",
        "thirdOrderId": "MY-ORDER-001",
        "data": {"orderNodeStatus": "MASTER_ACCEPTED"},
    }
    status, body = dispatch_callback("service_node", payload)
    assert status == 200
    assert body == {"code": 200, "message": "success", "orderStatus": "service_node"}


def test_dispatch_invalid_payload():
    """orderStatus 对了但 payload 缺 thirdOrderId → 400."""
    status, body = dispatch_callback("service_node", {"data": {}})
    assert status == 400
    assert body["code"] == 400


@pytest.mark.parametrize("order_status", list(NODE_REGISTRY.keys()))
def test_dispatch_all_valid_empty_payload(order_status):
    """每个节点最小 payload（仅 orderStatus + thirdOrderId）都应通过."""
    payload = {"orderStatus": order_status, "thirdOrderId": "X"}
    status, body = dispatch_callback(order_status, payload)
    assert status == 200, f"{order_status} should pass with minimal payload, got {body}"
