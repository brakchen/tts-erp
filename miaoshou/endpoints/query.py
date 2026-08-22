"""订单信息查询 endpoint · apifox 妙手开放平台."""

from __future__ import annotations

from typing import Any

from ..miaoshou_client import MiaoshouApiResponse, MiaoshouClient


class QueryEndpoint:
    def __init__(self, client: MiaoshouClient) -> None:
        self._c = client

    def cost_detail(self, *, order_no: str, **extra: Any) -> MiaoshouApiResponse:
        """费用明细查询 · doc-802865 / api-44014989."""
        params: dict[str, Any] = {"orderNo": order_no}
        params.update(extra)
        return self._c._call(
            path="https://openapi.wanshifu.com/user-open/order/costDetail",
            business_params=params,
            absolute=True,
        )

    def cost_sub_order_detail(
        self, *, sub_order_id: str, **extra: Any
    ) -> MiaoshouApiResponse:
        """订单子费用明细查询 · doc-3255523."""
        params: dict[str, Any] = {"subOrderId": sub_order_id}
        params.update(extra)
        return self._c._call(
            path="/user-open/order/costSubOrderDetail", business_params=params
        )

    def service_complete_image(
        self, *, order_no: str, **extra: Any
    ) -> MiaoshouApiResponse:
        """查询师傅完工图 · doc-2339457."""
        params: dict[str, Any] = {"orderNo": order_no}
        params.update(extra)
        return self._c._call(
            path="/user-open/order/query/ServiceCompleteImage",
            business_params=params,
        )
