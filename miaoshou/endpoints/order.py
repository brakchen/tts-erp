"""订单域 endpoint（apifox 妙手开放平台）.

- batch_create_async    下单（支持批量）   doc-716798 / api-25424031
- pay                    订单支付          doc-5978950
- query_order_list       订单列表查询        doc-7649852
- reminder               订单催服务         doc-1589297
"""

from __future__ import annotations

from typing import Any

from ..miaoshou_client import MiaoshouApiResponse, MiaoshouClient


class OrdersEndpoint:
    def __init__(self, client: MiaoshouClient) -> None:
        self._c = client

    def batch_create_async(
        self, order_list: list[dict[str, Any]]
    ) -> MiaoshouApiResponse:
        """下单（支持批量）· apifox doc-716798 / api-25424031.

        生产接口: /user-open/order/batchCreateAsync
        业务参数: order_list 内每条 order 字段见 doc 表格（必填 orderId / serviceType /
        categoryId / buyerName / buyerPhone / ...）。
        """
        return self._c._call(
            path="/user-open/order/batchCreateAsync",
            business_params={"orderList": order_list},
        )

    def pay(self, order_no: str, pay_type: str | None = None) -> MiaoshouApiResponse:
        """订单支付（支持一口价支付前置/后置）· doc-5978950."""
        params: dict[str, Any] = {"orderNo": order_no}
        if pay_type:
            params["payType"] = pay_type
        return self._c._call(path="/user-open/order/pay", business_params=params)

    def query_order_list(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        status: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        **extra: Any,
    ) -> MiaoshouApiResponse:
        """订单列表查询 · doc-7649852."""
        params: dict[str, Any] = {"page": page, "pageSize": page_size}
        if status:
            params["status"] = status
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time
        params.update(extra)
        return self._c._call(
            path="/user-open/order/queryOrderList", business_params=params
        )

    def reminder(self, order_no: str, node: str | None = None) -> MiaoshouApiResponse:
        """订单催服务（催预约/催上门/催服务）· doc-1589297.

        老路径走绝对 URL（不拼 client.cfg.path_prefix）。
        """
        params: dict[str, Any] = {"orderNo": order_no}
        if node:
            params["node"] = node
        return self._c._call(
            path="https://openapi.wanshifu.com/user-open/order/reminder",
            business_params=params,
            absolute=True,
        )
