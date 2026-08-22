"""测试工具 endpoint · apifox 妙手开放平台.

用于联调阶段：加密测试 / 节点查询。
"""

from __future__ import annotations

from typing import Any

from ..miaoshou_client import MiaoshouApiResponse, MiaoshouClient


class TestEndpoint:
    def __init__(self, client: MiaoshouClient) -> None:
        self._c = client

    def test_encode_new_v2(self, **payload: Any) -> MiaoshouApiResponse:
        """下单参数加密接口（联调自测用）· api-326417316. 走绝对 URL。"""
        return self._c._call(
            path="https://openapi.wanshifu.com/pre-release/test/user-order-open-api/order/test/encodeNewV2",
            business_params=payload,
            absolute=True,
        )

    def query_service_node(self, **payload: Any) -> MiaoshouApiResponse:
        """测试环境查下订单服务节点 · api-54436008. 走绝对 URL。"""
        return self._c._call(
            path="https://openapi.wanshifu.com/pre-release/test/user-order-open-api/script/queryServiceNode",
            business_params=payload,
            absolute=True,
        )
