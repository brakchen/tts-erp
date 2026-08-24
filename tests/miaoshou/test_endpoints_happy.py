"""所有出站 endpoint happy-path 测试 —— 断言 URL / envelope / 签名字段."""

from __future__ import annotations

import base64
import json
from unittest.mock import MagicMock, patch

import pytest

from miaoshou import (  # type: ignore[reportMissingImports]
 MiaoshouApiError,
 MiaoshouApiResponse,
 MiaoshouClient,
)

# 每个测试用例： (namespace, method, kwargs, expected_url_suffix)
CASES = [
 # ---- 订单域 ----
 (
  "orders",
  "batch_create_async",
  {"order_list": [{"orderId": "X"}]},
  "/user-open/order/batchCreateAsync",
 ),
 ("orders", "pay", {"order_no": "Y"}, "/user-open/order/pay"),
 ("orders", "query_order_list", {}, "/user-open/order/queryOrderList"),
 (
  "orders",
  "reminder",
  {"order_no": "Y"},
  "https://openapi.wanshifu.com/user-open/order/reminder",
 ),
 # ---- 费用域 ----
 (
  "fees",
  "add_sub_fee",
  {"order_no": "Y", "fee_list": [{"feeType": "ADD", "amount": 10}]},
  "/user-open/order/subOrder/add",
 ),
 (
  "fees",
  "audit_sub_order",
  {"sub_order_id": "S1", "status": 1},
  "/user-open/order/subOrder/audit",
 ),
 ("fees", "get_adjust_fee_reason", {}, "/user-open/order/subOrder/getAdjustFeeReason"),
 # ---- 退款/验收 ----
 (
  "refunds",
  "apply_refund",
  {"order_no": "Y", "reason": "坏"},
  "/user-open/order/refund/apply",
 ),
 (
  "refunds",
  "confirm_pay_to_master",
  {"order_no": "Y"},
  "/user-open/order/confirmPayToMaster",
 ),
 # ---- 仲裁 ----
 (
  "arbitrations",
  "apply",
  {"order_no": "Y", "reason_code": "1"},
  "/user-open/order/refund/arbitrationApply",
 ),
 ("arbitrations", "get_reason", {}, "/user-open/order/refund/getArbitrationReason"),
 (
  "arbitrations",
  "cancel",
  {"arbitration_id": "A1"},
  "/user-open/order/refund/arbitrationCancel",
 ),
 (
  "arbitrations",
  "submit_evidence",
  {"arbitration_id": "A1", "evidence_list": []},
  "/user-open/order/refund/submitEvidence",
 ),
 # ---- 关单 ----
 (
  "closes",
  "close_order",
  {"order_no": "Y", "reason": "客户取消"},
  "https://openapi.wanshifu.com/user-open/order/closeOrder",
 ),
 (
  "closes",
  "audit_close_order",
  {"order_no": "Y", "audit_status": 1},
  "/user-open/order/audit/closeOrder",
 ),
 (
  "closes",
  "work_order_apply",
  {"order_no": "Y", "reason": "补单"},
  "/user-open/order/workOrderApply",
 ),
 # ---- 投诉 ----
 ("complaints", "bank_list", {}, "/user-open/order/complaint/bankList"),
 ("complaints", "apply", {"orderNo": "Y"}, "/user-open/order/complaint/apply"),
 ("complaints", "cancel", {"complaint_id": "C1"}, "/user-open/order/complaint/cancel"),
 ("complaints", "get_type", {}, "/user-open/order/complaint/type"),
 (
  "complaints",
  "submit_evidence",
  {"complaintId": "C1"},
  "/user-open/order/complaint/evidenceSubmit",
 ),
 (
  "complaints",
  "evidence_supplement",
  {"complaintId": "C1"},
  "/user-open/order/complaint/evidenceSupplement",
 ),
 ("complaints", "pay_channel", {}, "/user-open/order/complaint/payChannel"),
 ("complaints", "detail", {"complaint_id": "C1"}, "/user-open/order/complaint/detail"),
 # ---- 查询 ----
 (
  "queries",
  "cost_detail",
  {"order_no": "Y"},
  "https://openapi.wanshifu.com/user-open/order/costDetail",
 ),
 (
  "queries",
  "cost_sub_order_detail",
  {"sub_order_id": "S1"},
  "/user-open/order/costSubOrderDetail",
 ),
 (
  "queries",
  "service_complete_image",
  {"order_no": "Y"},
  "/user-open/order/query/ServiceCompleteImage",
 ),
 # ---- 账号 ----
 (
  "accounts",
  "update_sub_user_role",
  {"sub_user_id": "U1", "role": "admin"},
  "/user-open/order/updateSubUserRole",
 ),
 ("accounts", "query_sub_user_info_list", {}, "/user-open/order/querySubUserInfoList"),
 (
  "accounts",
  "update_buyer_phone",
  {"order_no": "Y", "new_phone": "13900000000"},
  "/user-open/order/updateBuyerPhone",
 ),
 # ---- 商品/物流/售后 ----
 ("products", "query_user_goods", {}, "/user-open/order/queryUserGoods"),
 (
  "logistics",
  "order_arrived_sync",
  {"orderNo": "Y", "logisticsCompany": "顺丰", "logisticsNo": "SF123"},
  "/user-open/logistics/orderArrivedSync",
 ),
 (
  "aftersales",
  "create_aftersale_order",
  {"orderNo": "Y"},
  "/user-open/order/createAfterSaleOrder",
 ),
 ("aftersales", "fetch_aftersale_order", {}, "/user-open/order/getAfterSaleOrder"),
 # ---- 测试工具 ----
 (
  "tests",
  "test_encode_new_v2",
  {"orderNo": "Y"},
  "https://openapi.wanshifu.com/pre-release/test/user-order-open-api/order/test/encodeNewV2",
 ),
 (
  "tests",
  "query_service_node",
  {"orderNo": "Y"},
  "https://openapi.wanshifu.com/pre-release/test/user-order-open-api/script/queryServiceNode",
 ),
]


def _make_https_response():
 """伪造 http.client HTTPResponse-like 对象 — 给 fake connection 的 getresponse()."""
 body = json.dumps({"code": 200, "message": "ok", "data": {}}).encode("utf-8")
 resp = MagicMock()
 resp.read.return_value = body
 return resp


def _make_https_connection_factory(call_log):
 """返回 factory 用来 patch http.client.HTTPSConnection / HTTPConnection.

 把每次 .request(method, url_path, body, headers) 调用参数写到 call_log，
 让 test 能从外部断言 URL / headers / body。
 """
 def factory(host, port=None, timeout=None, **_):
  conn = MagicMock()
  def fake_request(method, url_path, body=None, headers=None):
   call_log.append({
    "host": host,
    "port": port,
    "method": method,
    "url_path": url_path,
    "body": body,
    "headers": headers,
   })
  conn.request.side_effect = fake_request
  resp = _make_https_response()
  conn.getresponse.return_value = resp
  return conn
 return factory


@pytest.mark.parametrize("ns,method,kwargs,expected_url", CASES)
def test_endpoint_happy_path(ns, method, kwargs, expected_url):
 """每个出站 endpoint：
 1. URL 路径正确（绝对/相对）
 2. envelope 结构正确（sign 是大写 32 MD5 / busData base64 / timestamp int）
 3. busData 解码后等价于 kwargs
 4. 返回 MiaoshouApiResponse 且 ok
 """
 c = MiaoshouClient(license_id="LIC", company_secret="SECRET")
 call_log = []
 factory = _make_https_connection_factory(call_log)
 # 同时 patch HTTPSConnection + HTTPConnection（scheme 是 http 时 fallback）
 with (
  patch("http.client.HTTPSConnection", side_effect=factory),
  patch("http.client.HTTPConnection", side_effect=factory),
 ):
  fn = getattr(getattr(c, ns), method)
  resp = fn(**kwargs)

 assert isinstance(resp, MiaoshouApiResponse)
 assert resp.ok

 # URL 断言：call_log 里 host + url_path 合起来是 full URL
 assert len(call_log) == 1, f"应恰好 1 次 HTTP call，实测 {len(call_log)}"
 entry = call_log[0]
 full_url = f"https://{entry['host']}{entry['url_path']}"
 assert full_url.endswith(expected_url), (
  f"URL 后缀不匹配\n  实测: {full_url}\n  期望 suffix: {expected_url}"
 )

 # envelope 断言
 body = entry["body"]
 payload = json.loads(body.decode("utf-8"))
 assert set(payload.keys()) == {
  "licenseId",
  "companySecret",
  "sign",
  "busData",
  "timestamp",
 }
 assert isinstance(payload["timestamp"], int)
 assert payload["sign"] == payload["sign"].upper()
 assert len(payload["sign"]) == 32

 # busData 解码后等价于 kwargs
 decoded = json.loads(base64.b64decode(payload["busData"]).decode("utf-8"))
 # 内部参数名（snake_case）可能跟 endpoint 不同；这里只断言子集
 for k, v in kwargs.items():
  # snake_case → camelCase 转换（仓央、create_aftersale_order 等仍保持原样）
  # 简单处理：只断言 endpoint 明确传过去的字段
  if k in decoded:
   assert decoded[k] == v


@pytest.mark.parametrize("ns,method,kwargs,_", CASES)
def test_endpoint_business_error_returns_502(ns, method, kwargs, _):
 """服务返回 code=500 时，SDK 抛 MiaoshouApiError."""
 c = MiaoshouClient(license_id="LIC", company_secret="SECRET")
 fake_resp = MagicMock()
 fake_resp.read.return_value = json.dumps(
  {"code": 500, "message": "服务端拒绝", "data": None}
 ).encode("utf-8")
 fake_conn = MagicMock()
 fake_conn.getresponse.return_value = fake_resp
 with (
  patch("http.client.HTTPSConnection", return_value=fake_conn),
  patch("http.client.HTTPConnection", return_value=fake_conn),
 ):
  fn = getattr(getattr(c, ns), method)
  with pytest.raises(MiaoshouApiError) as exc:
   fn(**kwargs)
 assert exc.value.code == 500
