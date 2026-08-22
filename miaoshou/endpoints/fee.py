"""订单费用调整 endpoint · apifox 妙手开放平台."""

from __future__ import annotations

from typing import Any

from ..miaoshou_client import MiaoshouApiResponse, MiaoshouClient


class FeeEndpoint:
    def __init__(self, client: MiaoshouClient) -> None:
        self._c = client

    def add_sub_fee(
        self, *, order_no: str, fee_list: list[dict[str, Any]], **extra: Any
    ) -> MiaoshouApiResponse:
        """增加子费用 · doc-802842 / api-31361867."""
        params: dict[str, Any] = {"orderNo": order_no, "feeList": fee_list}
        params.update(extra)
        return self._c._call(
            path="/user-open/order/subOrder/add", business_params=params
        )

    def audit_sub_order(
        self, *, sub_order_id: str, status: int, reason: str | None = None, **extra: Any
    ) -> MiaoshouApiResponse:
        """子订单审核（同意/拒绝）· doc-1229043.

        status: 1=同意, 2=拒绝
        """
        params: dict[str, Any] = {"subOrderId": sub_order_id, "status": status}
        if reason:
            params["reason"] = reason
        params.update(extra)
        return self._c._call(
            path="/user-open/order/subOrder/audit", business_params=params
        )

    def get_adjust_fee_reason(self, **extra: Any) -> MiaoshouApiResponse:
        """查询子订单费用调整原因 · doc-1084466."""
        params: dict[str, Any] = {}
        params.update(extra)
        return self._c._call(
            path="/user-open/order/subOrder/getAdjustFeeReason", business_params=params
        )
