"""Live 冒烟：SPU 实际 ROI 主表 + 四路钻取（orders/cases/ads/settlements）。

口径见 docs/archive/spu-real-roi-dashboard.md；钻取链式用例依赖
conftest 的 ``first_spu_roi_item`` 锚点，live 库无 ROI 数据时自动跳过。
"""

from __future__ import annotations

from conftest import request_json


def test_spu_roi_main_table_shape() -> None:
    """主表：items/totals/meta 容器 + 行主键/维度字段契约。"""
    status, body = request_json("GET", "/v2/analytics/spu-roi?limit=1")
    assert status == 200, body
    assert isinstance(body, dict), body
    assert isinstance(body.get("items"), list), body
    assert isinstance(body.get("totals"), dict), body
    assert isinstance(body.get("meta"), dict), body
    assert isinstance(body.get("total"), int), body
    for item in body["items"]:
        assert {"spu_pk", "spu_id", "title"} <= set(item), item


def test_spu_roi_drill_orders(first_spu_roi_item) -> None:
    """钻取订单：回显 spu_pk，行属于该 SPU。"""
    spu_pk = first_spu_roi_item["spu_pk"]
    status, body = request_json("GET", f"/v2/analytics/spu-roi/{spu_pk}/orders")
    assert status == 200, body
    assert body.get("spu_pk") == spu_pk, body
    assert isinstance(body.get("orders"), list), body
    for row in body["orders"]:
        assert {"order_id", "status", "qty"} <= set(row), row


def test_spu_roi_drill_cases(first_spu_roi_item) -> None:
    """钻取售后 case：回显 spu_pk，行字段契约。"""
    spu_pk = first_spu_roi_item["spu_pk"]
    status, body = request_json("GET", f"/v2/analytics/spu-roi/{spu_pk}/cases")
    assert status == 200, body
    assert body.get("spu_pk") == spu_pk, body
    assert isinstance(body.get("cases"), list), body
    for row in body["cases"]:
        assert {"case_id", "order_id", "type"} <= set(row), row


def test_spu_roi_drill_ads(first_spu_roi_item) -> None:
    """钻取广告：回显 spu_pk，行字段契约。"""
    spu_pk = first_spu_roi_item["spu_pk"]
    status, body = request_json("GET", f"/v2/analytics/spu-roi/{spu_pk}/ads")
    assert status == 200, body
    assert body.get("spu_pk") == spu_pk, body
    assert isinstance(body.get("ads"), list), body
    for row in body["ads"]:
        assert {"campaign_id", "spend"} <= set(row), row


def test_spu_roi_drill_settlements(first_spu_roi_item) -> None:
    """钻取结算流水：回显 spu_pk，行字段契约。"""
    spu_pk = first_spu_roi_item["spu_pk"]
    status, body = request_json("GET", f"/v2/analytics/spu-roi/{spu_pk}/settlements")
    assert status == 200, body
    assert body.get("spu_pk") == spu_pk, body
    assert isinstance(body.get("settlements"), list), body
    for row in body["settlements"]:
        assert "order_id" in row, row


def test_spu_roi_drill_unknown_spu_404() -> None:
    """不在利润口径内的 spu_pk → 404 + detail 文案。"""
    status, body = request_json("GET", "/v2/analytics/spu-roi/999999999/orders")
    assert status == 404, body
    assert "not found" in str(body), body
